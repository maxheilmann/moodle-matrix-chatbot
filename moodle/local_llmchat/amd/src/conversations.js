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
 * The open conversation: remembered per scope, its list, its title and the last seen event seqs.
 *
 * @module     local_llmchat/conversations
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Log from 'core/log';
import * as frames from 'local_llmchat/frames';
import {renderInto} from 'local_llmchat/render';
import {fill} from 'local_llmchat/util';

const SEQ_STORAGE_KEY = 'local_llmchat_lastseq';

/** Current conversation per course (0 = global chat), so the block and the full page agree. */
const CURRENT_STORAGE_KEY = 'local_llmchat_current';

export class Conversations {
    /**
     * Constructor.
     *
     * @param {Object} host the ChatController (its regions, strings and the other parts)
     */
    constructor(host) {
        this.host = host;
        this.regions = host.regions;
        this.str = host.str;
        this.root = host.root;
        this.lastSeq = this.loadSeqs();
    }

    /**
     * The conversation last open in this scope (sessionStorage), or ''.
     *
     * @returns {string}
     */
    loadCurrent() {
        try {
            const current = JSON.parse(window.sessionStorage.getItem(CURRENT_STORAGE_KEY) || '{}') || {};
            return current[this.host.courseid] ? String(current[this.host.courseid]) : '';
        } catch (e) {
            return '';
        }
    }

    /**
     * Make a conversation the current one (null = new): remembered for this scope, and the
     * block's "open in full view" link opens it.
     *
     * @param {string|null} id
     */
    setConversation(id) {
        this.host.conversationId = id;
        try {
            const current = JSON.parse(window.sessionStorage.getItem(CURRENT_STORAGE_KEY) || '{}') || {};
            if (id) {
                current[this.host.courseid] = id;
            } else {
                delete current[this.host.courseid];
            }
            window.sessionStorage.setItem(CURRENT_STORAGE_KEY, JSON.stringify(current));
        } catch (e) {
            // Storage unavailable: the block starts with a new conversation next time.
        }
        if (this.regions.expand) {
            const url = new URL(this.regions.expand.href, window.location.href);
            if (id) {
                url.searchParams.set('conversation', id);
            } else {
                url.searchParams.delete('conversation');
            }
            this.regions.expand.href = url.toString();
        }
    }

    /**
     * Last seq per conversation, kept across reloads in sessionStorage.
     *
     * @returns {Object}
     */
    loadSeqs() {
        try {
            return JSON.parse(window.sessionStorage.getItem(SEQ_STORAGE_KEY) || '{}') || {};
        } catch (e) {
            return {};
        }
    }

    /**
     * Persist last seqs.
     */
    saveSeqs() {
        try {
            window.sessionStorage.setItem(SEQ_STORAGE_KEY, JSON.stringify(this.lastSeq));
        } catch (e) {
            // Storage unavailable: resume still works within this page.
        }
    }

    /**
     * Reload the conversation list (and the header's title).
     */
    async refreshConversations() {
        if (!this.host.conversationId) {
            this.showTitle(null);
            if (this.host.compact) {
                // The block lists nothing; a new conversation needs no request for its title.
                return;
            }
        }
        try {
            const data = await this.host.apiGet('/api/conversations');
            const items = Array.isArray(data) ? data : (data.conversations || data.items || []);
            // The conversation may have changed while the list loaded.
            this.showTitle(items);
            if (this.host.compact) {
                return;
            }
            await renderInto(this.regions.conversations, 'local_llmchat/conversation_list', {
                hasconversations: items.length > 0,
                conversations: items.map((c) => ({
                    id: String(c.id), title: c.title || this.str.untitled, active: String(c.id) === this.host.conversationId,
                })),
            });
        } catch (e) {
            // The list is a convenience; the chat works without it.
            Log.debug(e);
        }
    }

    /**
     * Show the open conversation's title in the header.
     *
     * @param {Array|null} items the conversation list (null: not loaded)
     */
    showTitle(items) {
        if (!this.host.conversationId) {
            this.regions.title.textContent = this.str.newconversation;
            return;
        }
        if (items) {
            const current = items.find((c) => String(c.id) === this.host.conversationId);
            this.regions.title.textContent = (current && current.title) || this.str.untitled;
        }
    }

    /**
     * The API named the conversation: show the name in the header and the list.
     *
     * @param {Object} frame title{title}
     * @param {boolean} replay
     */
    onTitle(frame, replay) {
        if (replay || !frame.title) {
            // Stored history: the list request that opened it brought the current title.
            return;
        }
        this.regions.title.textContent = frame.title;
        const item = this.host.compact ? null : this.regions.conversations.querySelector(
            `[data-action="open-conversation"][data-id="${CSS.escape(this.host.conversationId || '')}"] .text-truncate`);
        if (item) {
            item.textContent = frame.title;
        } else if (!this.host.compact) {
            this.refreshConversations();
        }
    }

    /**
     * Open a stored conversation (null = new).
     *
     * @param {string|null} id
     * @param {Object} [options]
     * @param {boolean} [options.focus=true] move the focus to the composer
     * @param {boolean} [options.restore=false] reopening the remembered conversation: if it is
     *     gone, quietly start a new one instead of showing an error
     * @param {boolean} [options.refreshList=true] reload the list (false: it already marks this one)
     */
    openConversation(id, {focus = true, restore = false, refreshList = true} = {}) {
        this.host.enqueue(async() => {
            this.setConversation(id);
            this.host.turns.turn = null;
            // Access may have changed since the links were resolved.
            this.host.links.clear();
            this.regions.messages.replaceChildren(this.regions.empty, this.regions.loading);
            // Stored history renders frame by frame and template rendering yields to the browser
            // between frames: keep it hidden behind the loader until all of it is in place.
            this.host.layout.setReplaying(Boolean(id));
            this.host.layout.showLoading(Boolean(id));
            // Links of cards from a history left mid-replay: those cards are gone.
            this.host.links.flushRefs();
            if (id) {
                this.host.links.batchRefs();
            }
            this.host.clearStatusErrors();
            this.host.setRunning(false);
            if (focus) {
                this.regions.input.focus();
            }
            if (refreshList) {
                // After the switch, so the list (or the block's title) marks this conversation.
                this.refreshConversations();
            }
        });
        if (!id) {
            return;
        }
        this.host.enqueue(async() => {
            let data;
            try {
                // Stored history holds one event per streamed run, so one render per text run.
                data = await this.host.apiGet(`/api/conversations/${encodeURIComponent(id)}`);
            } catch (e) {
                if (this.host.conversationId !== id) {
                    return;
                }
                this.host.layout.setReplaying(false);
                this.host.links.flushRefs();
                this.host.layout.showLoading(false);
                if (restore) {
                    Log.debug(e);
                    this.setConversation(null);
                    this.refreshConversations();
                    return;
                }
                // Not the empty state's suggestions for a conversation that failed to open.
                this.regions.empty.classList.add('d-none');
                await this.host.showStatus('error', fill(this.str.error, e && e.message ? e.message : String(e)));
                return;
            }
            if (this.host.conversationId !== id) {
                return;
            }
            const events = data.events || [];
            events.forEach((frame) => this.host.handleFrame(Object.assign({'conversation_id': id}, frame), true));
            if (!events.length) {
                (data.messages || []).forEach((m) => {
                    const text = String(m.text ?? m.content ?? '');
                    this.host.handleFrame(m.role === 'user' ? {type: 'user_message', text} : {type: 'final', text,
                        citations: m.citations || []}, true);
                });
            }
            this.host.enqueue(() => {
                if (this.host.conversationId === id) {
                    this.regions.loading.classList.add('d-none');
                    this.host.layout.setReplaying(false);
                    this.host.links.flushRefs();
                }
                this.host.setRunning(false);
                // Anything newer than history (a running turn, a pending question) arrives via resume.
                this.host.socket.send(frames.resume({conversationId: id, lastSeq: this.lastSeq[id]}));
                this.host.layout.scroll(true);
            });
        });
    }
}
