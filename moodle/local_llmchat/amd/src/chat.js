// This file is part of Moodle - https://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Moodle is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with Moodle.  If not, see <https://www.gnu.org/licenses/>.


/**
 * Course chat client. Streams tokens, thinking, tool calls and interactions from the API
 * websocket and renders them with local_llmchat templates (theme-overridable, theme icons).
 * Reconnects with resume{last_seq}, offers "Continue" after an interrupted turn and shows the
 * token budget. A fresh chat token is fetched before every send.
 *
 * Frames are handled strictly in order through a promise queue, because template rendering is
 * asynchronous and a stream must never be reordered.
 *
 * @module     local_llmchat/chat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';
import Log from 'core/log';
import {getStrings} from 'core/str';
import {add as addToast} from 'core/toast';
import ReconnectingSocket from 'local_llmchat/socket';
import * as frames from 'local_llmchat/frames';
import {escapeHtml} from 'local_llmchat/markdown';
import {renderInto, renderNode} from 'local_llmchat/render';
import {fill, formatTime, uuid} from 'local_llmchat/util';
import {CitationCards} from 'local_llmchat/citations';
import {Conversations} from 'local_llmchat/conversations';
import {Layout} from 'local_llmchat/layout';
import {LinkResolver} from 'local_llmchat/links';
import {Questions} from 'local_llmchat/questions';
import {QuotaMeter} from 'local_llmchat/quota';
import {ToolCards} from 'local_llmchat/toolcards';
import {TurnView} from 'local_llmchat/turn';
import {RESULT_STRING_KEYS, STRING_KEYS as TOOL_STRING_KEYS} from 'local_llmchat/tooldetails';

const STRING_KEYS = [
    'cancelled', 'confirm:no', 'confirm:yes', 'copied', 'error', 'newconversation', 'offline', 'queuedmessage',
    'quota:exemptuser', 'quota:remaining',
    'quota:remainingmonth', 'quota:thismonth', 'quota:today', 'quota:usedof', 'ratelimited:burst', 'ratelimited:site',
    'ratelimited:user', 'reconnected', 'resume',
    'turninterrupted', 'unknowninteraction', 'untitled',
];

/** Server frame type => [part that renders it ('' = the controller), method(frame, replay)]. */
const FRAME_HANDLERS = {
    'user_message': ['turns', 'onUserMessage'],
    'thinking': ['turns', 'onThinking'],
    'token': ['turns', 'onToken'],
    'tool_call': ['cards', 'onToolCall'],
    'tool_progress': ['cards', 'onToolProgress'],
    'tool_result': ['cards', 'onToolResult'],
    'question': ['questions', 'onQuestion'],
    'user_answer': ['questions', 'onUserAnswer'],
    'citations': ['turns', 'onCitations'],
    'final': ['turns', 'onFinal'],
    'cancelled': ['turns', 'onCancelled'],
    'turn_interrupted': ['turns', 'onInterrupted'],
    'rate_limited': ['', 'onRateLimited'],
    'quota': ['quota', 'onQuota'],
    'title': ['conversations', 'onTitle'],
    'error': ['', 'onError'],
};

class ChatController {
    /**
     * Constructor.
     *
     * @param {HTMLElement} root
     * @param {Object} config {courseid, conversationid, preloaded: {list}}
     * @param {Object} strings
     */
    constructor(root, config, strings) {
        this.root = root;
        this.courseid = Number(config.courseid) || 0;
        // Compact: the chat block, one conversation, no list (the full page has the list).
        this.compact = root.dataset.mode === 'compact';
        this.initialConversation = config.conversationid ? String(config.conversationid) : '';
        // Whether the page arrived with the conversation list (the chat is always ours to load).
        this.preloaded = config.preloaded || {};
        this.str = strings;
        this.regions = {};
        ['messages', 'empty', 'loading', 'conversations', 'status', 'quota', 'quota-button', 'quota-ring', 'quota-percent',
            'quota-panel', 'input', 'composer', 'panel', 'sidebar', 'title', 'expand'].forEach((name) => {
            this.regions[name] = root.querySelector(`[data-region="${name}"]`);
        });
        this.sendButton = root.querySelector('[data-action="send"]');
        this.cancelButton = root.querySelector('[data-action="cancel"]');
        this.conversationId = null;
        this.queued = null;
        this.queue = Promise.resolve();
        this.offlineNotice = null;
        this.wasOffline = false;

        // The parts of the page; each is handed this controller for the strings, regions and the other parts.
        this.layout = new Layout(this);
        this.links = new LinkResolver((chunks) => Ajax.call(chunks.map((refs) => ({
            methodname: 'local_llmchat_resolve_refs', args: {refs},
        }))));
        this.citations = new CitationCards(this);
        this.cards = new ToolCards(this);
        this.turns = new TurnView(this);
        this.questions = new Questions(this);
        this.quota = new QuotaMeter(this);
        this.conversations = new Conversations(this);

        this.socket = new ReconnectingSocket({
            credentials: async() => {
                const t = await this.issueToken();
                return {wsurl: t.wsurl, token: t.token};
            },
            onFrame: (frame) => this.handleFrame(frame, false),
            onReady: (frame) => this.enqueue(() => this.handleReady(frame)),
            onStatus: (online) => this.enqueue(() => this.setOnline(online)),
        });
        this.bind();
    }

    /**
     * Run work after everything queued before it.
     *
     * @param {Function} task () => (Promise|void)
     * @returns {Promise}
     */
    enqueue(task) {
        this.queue = this.queue.then(task).catch((e) => Log.error(e));
        return this.queue;
    }

    /**
     * Wire DOM events.
     */
    bind() {
        const input = this.regions.input;
        this.regions.composer.addEventListener('submit', (e) => {
            e.preventDefault();
            this.submit();
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
                e.preventDefault();
                this.submit();
            }
        });
        input.addEventListener('input', () => this.layout.autosize());
        this.cancelButton.addEventListener('click', () => {
            if (this.conversationId) {
                this.socket.send(frames.cancel({conversationId: this.conversationId}));
            }
        });
        this.regions.messages.addEventListener('scroll', () => {
            this.citations.hideCitationCard();
            const m = this.regions.messages;
            this.layout.stickToBottom = m.scrollTop + m.clientHeight >= m.scrollHeight - 48;
        });
        this.root.addEventListener('click', (e) => this.onClick(e));
        this.citations.bind();
        this.quota.bind();
    }

    /**
     * Delegated clicks: toggles, suggestions, conversation list, new conversation, copy.
     *
     * @param {MouseEvent} e
     */
    onClick(e) {
        const target = e.target.closest('[data-action]');
        if (!target || !this.root.contains(target)) {
            return;
        }
        const action = target.dataset.action;
        if (action === 'toggle') {
            const panel = document.getElementById(target.getAttribute('aria-controls'));
            if (panel) {
                const open = panel.classList.toggle('show');
                target.setAttribute('aria-expanded', open ? 'true' : 'false');
            }
        } else if (action === 'quota-toggle') {
            this.quota.toggleQuotaPanel(!this.regions['quota-panel'].classList.contains('show'));
        } else if (action === 'suggest') {
            this.regions.input.value = target.dataset.text;
            this.submit();
        } else if (action === 'open-conversation') {
            this.layout.closeSidebarOnMobile();
            this.conversations.openConversation(target.dataset.id);
        } else if (action === 'new-conversation') {
            this.layout.closeSidebarOnMobile();
            this.conversations.openConversation(null);
        } else if (action === 'copy') {
            this.copy(target.dataset.copytext || '');
        }
    }

    /**
     * Put an answer on the clipboard.
     *
     * @param {string} text
     */
    async copy(text) {
        try {
            await navigator.clipboard.writeText(text);
            addToast(this.str.copied);
        } catch (e) {
            Log.debug(e);
        }
    }

    /**
     * Start. The block waits until it is first on screen (the block drawer may stay closed), so
     * a page with the block costs no token, socket or API call until the chat is looked at.
     */
    start() {
        if (!this.compact) {
            // Boost-based themes are sized by styles.css from the first paint; other layouts
            // get the card fitted to the window here.
            if (!document.body.classList.contains('uses-drawers')) {
                this.layout.fitHeight();
                window.addEventListener('resize', () => this.layout.fitHeight());
            }
            this.run();
            return;
        }
        if (typeof window.IntersectionObserver !== 'function') {
            this.run();
            return;
        }
        const observer = new window.IntersectionObserver((entries) => {
            if (entries.some((entry) => entry.isIntersecting)) {
                observer.disconnect();
                this.run();
            }
        });
        observer.observe(this.root);
    }

    /**
     * Connect and open the conversation to start with: the one asked for in the URL, in the block
     * the one last open in this scope, otherwise a new one.
     */
    run() {
        this.socket.connect();
        const id = this.initialConversation || (this.compact ? this.conversations.loadCurrent() : '');
        // A list the page arrived with already marks the conversation asked for in the URL.
        const listed = this.preloaded.list && id === this.initialConversation;
        if (id) {
            this.conversations.openConversation(id, {focus: false, restore: !this.initialConversation, refreshList: !listed});
        } else if (!this.preloaded.list) {
            this.conversations.refreshConversations();
        }
    }

    /**
     * Fresh chat token (sesskey-protected AJAX).
     *
     * @returns {Promise<Object>} {token, expires, apiurl, wsurl}
     */
    async issueToken() {
        const result = await Ajax.call([{methodname: 'local_llmchat_issue_token', args: {courseid: this.courseid}}])[0];
        this.apiurl = result.apiurl;
        return result;
    }

    /**
     * Online/offline notice in the status area; a toast once the connection is back.
     *
     * @param {boolean} online
     */
    async setOnline(online) {
        if (online) {
            if (this.offlineNotice) {
                this.offlineNotice.remove();
                this.offlineNotice = null;
            }
            if (this.wasOffline) {
                addToast(this.str.reconnected, {type: 'success'});
                this.wasOffline = false;
            }
            return;
        }
        this.wasOffline = true;
        if (!this.offlineNotice) {
            this.offlineNotice = await renderNode('notice', {
                type: 'warning', icon: 'offline', component: 'local_llmchat', message: this.str.offline, spinner: true,
            });
            this.offlineNotice.classList.add('mb-0', 'rounded-0', 'border-start-0', 'border-end-0');
            this.regions.status.prepend(this.offlineNotice);
        }
    }

    /**
     * After (re)authentication: remember tools, resume the open conversation, flush the queue.
     *
     * @param {Object} frame ready{tools, server_time}
     */
    handleReady(frame) {
        (frame.tools || []).forEach((tool) => this.cards.tools.set(tool.name, tool));
        if (this.conversationId) {
            this.socket.send(frames.resume({conversationId: this.conversationId,
                lastSeq: this.conversations.lastSeq[this.conversationId]}));
        }
        if (this.queued) {
            const queued = this.queued;
            this.queued = null;
            queued.note.textContent = '';
            this.dispatchSend(queued.text, queued.clientMsgId);
        }
    }

    /**
     * Composer submit.
     */
    submit() {
        const text = this.regions.input.value.trim();
        if (!text) {
            return;
        }
        this.regions.input.value = '';
        this.layout.autosize();
        const clientMsgId = uuid();
        this.enqueue(async() => {
            const bubble = await this.turns.addUserBubble(text);
            this.turns.pendingUserBubbles.set(clientMsgId, bubble);
            if (!this.socket.isReady()) {
                // Offline: keep at most one unsent message.
                if (this.queued) {
                    this.queued.bubble.remove();
                }
                const note = bubble.querySelector('[data-region="note"]');
                note.textContent = this.str.queuedmessage;
                this.queued = {text, clientMsgId, bubble, note};
                return;
            }
            this.dispatchSend(text, clientMsgId);
        });
    }

    /**
     * Send with a fresh token (scope refresh per turn).
     *
     * @param {string} text
     * @param {string} clientMsgId
     */
    async dispatchSend(text, clientMsgId) {
        this.clearStatusErrors();
        try {
            const t = await this.issueToken();
            const sent = this.socket.send(frames.send({text, token: t.token, clientMsgId,
                conversationId: this.conversationId, courseId: this.courseid}));
            if (sent) {
                this.setRunning(true);
            } else {
                const bubble = this.turns.pendingUserBubbles.get(clientMsgId);
                const note = bubble.querySelector('[data-region="note"]');
                note.textContent = this.str.queuedmessage;
                this.queued = {text, clientMsgId, bubble, note};
            }
        } catch (e) {
            this.showStatus('error', e && e.message ? e.message : String(e));
        }
    }

    /**
     * Toggle running state: the send button becomes a stop button.
     *
     * @param {boolean} running
     */
    setRunning(running) {
        this.running = running;
        this.cancelButton.classList.toggle('d-none', !running);
        this.sendButton.classList.toggle('d-none', running);
    }

    /**
     * Handle one server frame (in order, after anything queued before).
     *
     * @param {Object} frame
     * @param {boolean} replay true when re-rendering stored history
     * @returns {Promise}
     */
    handleFrame(frame, replay) {
        return this.enqueue(async() => {
            if (!this.acceptFrame(frame, replay)) {
                return;
            }
            const [owner, method] = FRAME_HANDLERS[frame.type] || [];
            if (method) {
                await (owner ? this[owner] : this)[method](frame, replay);
                this.layout.scroll();
            }
            // Unknown types are ignored (forward compatibility).
        });
    }

    /**
     * Adopt the conversation id of a new conversation, drop frames of other conversations and
     * frames already seen (resume replays everything after last_seq; duplicates are skipped).
     *
     * @param {Object} frame
     * @param {boolean} replay
     * @returns {boolean} whether the frame should be rendered
     */
    acceptFrame(frame, replay) {
        if (frame.conversation_id && !this.conversationId && !replay) {
            this.conversations.setConversation(String(frame.conversation_id));
            this.conversations.refreshConversations();
        }
        const convId = frame.conversation_id ? String(frame.conversation_id) : this.conversationId;
        if (convId && convId !== this.conversationId) {
            return false;
        }
        if (typeof frame.seq === 'number' && convId) {
            const last = this.conversations.lastSeq[convId] || 0;
            if (!replay && frame.seq <= last) {
                return false;
            }
            this.conversations.lastSeq[convId] = Math.max(last, frame.seq);
            this.conversations.saveSeqs();
        }
        return true;
    }

    /**
     * Show a dismissible core notification in the chat's status area.
     *
     * @param {string} type error|warning|info|success
     * @param {string} message plain text
     * @returns {Promise<void>}
     */
    async showStatus(type, message) {
        const holder = document.createElement('div');
        holder.dataset.statusType = type;
        this.regions.status.appendChild(holder);
        await renderInto(holder, `core/notification_${type}`, {
            message: escapeHtml(message), closebutton: true, announce: true,
            extraclasses: 'mb-0 rounded-0 border-start-0 border-end-0',
        });
    }

    /**
     * Remove error and warning notifications (before a new send).
     */
    clearStatusErrors() {
        this.regions.status.querySelectorAll('[data-status-type]').forEach((n) => n.remove());
    }

    /**
     * GET from the API with the chat token.
     *
     * @param {string} path
     * @returns {Promise<Object>}
     */
    async apiGet(path) {
        const t = await this.issueToken();
        const response = await fetch(`${t.apiurl}${path}`, {
            headers: {Authorization: `Bearer ${t.token}`, Accept: 'application/json'},
            credentials: 'omit',
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        return response.json();
    }

    /**
     * Error frame; a cancelled turn is shown inline instead of as an error.
     *
     * @param {Object} frame error{code, message}
     */
    async onError(frame) {
        if (frame.code === 'cancelled') {
            await this.turns.onCancelled(frame);
            return;
        }
        this.setRunning(false);
        if (this.turns.turn) {
            this.turns.advance(this.turns.turn, 'error');
        }
        await this.showStatus('error', fill(this.str.error, frame.message || frame.code || ''));
    }

    /**
     * Budget refusal.
     *
     * @param {Object} frame rate_limited{scope, used?, limit?, resets_at}
     */
    async onRateLimited(frame) {
        this.setRunning(false);
        if (this.turns.turn) {
            this.turns.advance(this.turns.turn, 'rate_limited');
        }
        const scope = ['burst', 'user', 'site'].includes(frame.scope) ? frame.scope : 'user';
        await this.showStatus('warning', fill(this.str[`ratelimited:${scope}`], formatTime(frame.resets_at)));
        if (frame.used !== undefined && frame.limit !== undefined) {
            await this.quota.onQuota({'used_today': frame.used, 'limit_today': frame.limit, 'resets_at': frame.resets_at});
        }
    }
}

/**
 * Initialise the chat page.
 *
 * @param {Object} config {courseid, rootid, conversationid, preloaded: {list}}
 */
export const init = async(config) => {
    const root = document.getElementById(config.rootid);
    if (!root) {
        return;
    }
    const requests = [...STRING_KEYS, ...TOOL_STRING_KEYS, ...RESULT_STRING_KEYS].map((key) => ({key, component: 'local_llmchat'}));
    requests.push({key: 'page', component: 'core'});
    const values = await getStrings(requests);
    const strings = {};
    requests.forEach(({key}, i) => {
        strings[key] = values[i];
    });
    const controller = new ChatController(root, config, strings);
    controller.start();
};

export {ChatController, fill};