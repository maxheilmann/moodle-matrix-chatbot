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
 * One assistant turn as it streams in: text, thinking, the answer, notices and the user's bubbles.
 *
 * @module     local_llmchat/turn
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import * as activity from 'local_llmchat/activity';
import * as frames from 'local_llmchat/frames';
import {render as renderMarkdown} from 'local_llmchat/markdown';
import {renderNode, uniqid} from 'local_llmchat/render';
import {citationMeta} from 'local_llmchat/tooldetails';

export class TurnView {
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
        this.turn = null;
        /** Maps a client_msg_id to the user bubble waiting for the server's echo. */
        this.pendingUserBubbles = new Map();
    }

    /**
     * The assistant message of the current turn (rendered on demand).
     *
     * @param {Object} frame
     * @returns {Promise<Object>}
     */
    async currentTurn(frame) {
        const turnId = frame.turn_id ?? null;
        if (this.turn && (turnId === null || this.turn.id === null || this.turn.id === turnId)) {
            if (this.turn.id === null) {
                this.turn.id = turnId;
            }
            return this.turn;
        }
        const node = await renderNode('message_assistant');
        this.appendMessage(node);
        const region = (name) => node.querySelector(`[data-region="${name}"]`);
        this.turn = {
            id: turnId, node, body: region('body'), text: region('text'), typing: region('typing'), raw: '',
            thinking: null, toolGroup: null, cards: new Map(), citations: [], activity: activity.initial(),
        };
        return this.turn;
    }

    /**
     * Track what the turn is doing (local_llmchat/activity): close the live thinking block when the
     * step moves on, and show the working indicator whenever the model works with nothing streaming
     * (e.g. after the last tool result, while it decides what to do next).
     *
     * @param {Object} turn
     * @param {string} type frame type, or 'user_answer' / 'continue' for what the client sent
     * @returns {Object} {before, after} activity states
     */
    advance(turn, type) {
        const before = turn.activity;
        const after = activity.step(before, type);
        turn.activity = after;
        if (activity.endsThinking(before, after)) {
            this.finishThinking(turn);
        }
        turn.typing.classList.toggle('d-none', !activity.showsIndicator(after));
        return {before, after};
    }

    /**
     * Insert a node at the end of the turn, above the working indicator (content stays in stream order).
     *
     * @param {Object} turn
     * @param {HTMLElement} node
     */
    place(turn, node) {
        turn.body.insertBefore(node, turn.typing);
    }

    /**
     * The text segment for answer text: the current one if nothing was placed after it, else a new one.
     *
     * @param {Object} turn
     * @returns {HTMLElement}
     */
    textSegment(turn) {
        if (turn.text.nextElementSibling !== turn.typing) {
            this.newTextSegment(turn);
        }
        return turn.text;
    }

    /**
     * Start a new text segment at the end of the turn (text after an interaction goes below it).
     *
     * @param {Object} turn
     */
    newTextSegment(turn) {
        const text = document.createElement('div');
        text.className = 'local-llmchat-text';
        this.place(turn, text);
        turn.text = text;
        turn.raw = '';
    }

    /**
     * Reasoning delta (text only when the API sends it; otherwise an indicator). Every model step
     * that thinks gets its own block, placed in stream order in the activity group with the tool cards.
     *
     * @param {Object} frame
     */
    async onThinking(frame) {
        const turn = await this.currentTurn(frame);
        const {before, after} = this.advance(turn, 'thinking');
        if (activity.startsThinking(before, after) || !turn.thinking) {
            const node = await renderNode('thinking', {uniqid: uniqid('llmchat-thinking')});
            this.toolGroup(turn).appendChild(node);
            const q = (sel) => node.querySelector(sel);
            turn.thinking = {
                node, text: q('[data-region="thinking-text"]'), spinner: q('[data-region="spinner"]'),
                active: q('[data-region="label-active"]'), done: q('[data-region="label-done"]'),
            };
        }
        if (frame.delta) {
            turn.thinking.text.textContent += frame.delta;
        }
    }

    /**
     * Finish the live thinking block once the step moves on (answer, tool call, question, end).
     * It stays open or closed as the reader left it (it starts collapsed). A block that streamed no
     * text is dropped, and with it a group it leaves empty.
     *
     * @param {Object} turn
     */
    finishThinking(turn) {
        const t = turn.thinking;
        if (t && !t.spinner.classList.contains('d-none')) {
            t.spinner.classList.add('d-none');
            t.active.classList.add('d-none');
            t.done.classList.remove('d-none');
            if (!t.text.textContent.trim()) {
                const group = t.node.parentElement;
                t.node.remove();
                if (group && !group.children.length) {
                    group.remove();
                }
            }
        }
    }

    /**
     * The activity group for thinking blocks and tool cards: the current one if nothing visible was
     * placed after it, else a new one, so steps without text between them read as one flow.
     *
     * @param {Object} turn
     * @returns {HTMLElement}
     */
    toolGroup(turn) {
        const blanks = [];
        for (let n = turn.toolGroup && turn.toolGroup.nextElementSibling; n && n !== turn.typing; n = n.nextElementSibling) {
            blanks.push(n.classList.contains('local-llmchat-text') && !n.textContent.trim());
        }
        if (!turn.toolGroup || !turn.toolGroup.isConnected || !activity.joinsGroup(blanks)) {
            turn.toolGroup = document.createElement('div');
            turn.toolGroup.className = 'list-group local-llmchat-tools';
            turn.toolGroup.dataset.region = 'tools';
            this.place(turn, turn.toolGroup);
        }
        return turn.toolGroup;
    }

    /**
     * Answer token.
     *
     * @param {Object} frame
     */
    async onToken(frame) {
        const turn = await this.currentTurn(frame);
        this.advance(turn, 'token');
        const text = this.textSegment(turn);
        turn.raw += frame.delta || '';
        this.host.citations.citationsOf.set(turn.node, turn.citations);
        text.innerHTML = renderMarkdown(turn.raw, turn.citations);
        if (this.host.citations.citeCard && !this.host.citations.citeCard.chip.isConnected) {
            // The chip it belonged to was re-rendered with the text.
            this.host.citations.hideCitationCard();
        }
    }

    /**
     * Citation list sent ahead of the final answer.
     *
     * @param {Object} frame citations{citations}
     */
    async onCitations(frame) {
        (await this.currentTurn(frame)).citations = frame.citations || [];
    }

    /**
     * Final answer with sources and actions.
     *
     * @param {Object} frame final{text, citations}
     * @param {boolean} replay part of a stored history being rendered
     */
    async onFinal(frame, replay) {
        const turn = await this.currentTurn(frame);
        this.advance(turn, 'final');
        turn.citations = frame.citations || turn.citations || [];
        const text = this.textSegment(turn);
        turn.raw = frame.text ?? turn.raw;
        this.host.citations.citationsOf.set(turn.node, turn.citations);
        text.innerHTML = renderMarkdown(turn.raw, turn.citations);
        const footer = await renderNode('answer_footer', {
            copytext: turn.raw,
            budgetexhausted: Boolean(frame.budget_exhausted || frame.truncated_by_budget),
            hascitations: turn.citations.length > 0,
            citations: turn.citations.map((c) => ({
                index: c.index,
                title: c.title || c.url || '',
                meta: citationMeta(c, this.str.page),
                url: c.url || '',
                hasurl: Boolean(c.url),
            })),
        });
        this.place(turn, footer);
        this.host.setRunning(false);
        this.turn = null;
        if (!replay) {
            // A finished turn may have titled the conversation; stored history changes nothing.
            this.host.conversations.refreshConversations();
        }
    }

    /**
     * The API restarted mid-turn: offer "Continue".
     *
     * @param {Object} frame turn_interrupted{turn_id}
     */
    async onInterrupted(frame) {
        const turn = await this.currentTurn(frame);
        this.advance(turn, 'turn_interrupted');
        this.host.setRunning(false);
        const notice = await renderNode('notice', {
            type: 'info', icon: 'i/info', component: 'core', message: this.str.turninterrupted,
            hasaction: true, actionlabel: this.str.resume,
        });
        notice.querySelector('[data-action="notice-action"]').addEventListener('click', async() => {
            const t = await this.host.issueToken();
            if (this.host.socket.send(frames.continueTurn({conversationId: this.host.conversationId, turnId: frame.turn_id,
                token: t.token}))) {
                notice.remove();
                this.host.setRunning(true);
                this.advance(turn, 'continue');
            }
        });
        this.place(turn, notice);
    }

    /**
     * The user stopped the turn: say so inline.
     *
     * @param {Object} frame cancelled{turn_id}
     */
    async onCancelled(frame) {
        this.host.setRunning(false);
        const turn = await this.currentTurn(frame);
        this.advance(turn, 'cancelled');
        const note = document.createElement('div');
        note.className = 'small text-muted';
        note.textContent = this.str.cancelled;
        this.place(turn, note);
        this.turn = null;
    }

    /**
     * Server echo of a user message.
     *
     * @param {Object} frame
     * @param {boolean} replay
     */
    async onUserMessage(frame, replay) {
        const known = frame.client_msg_id && this.pendingUserBubbles.has(frame.client_msg_id);
        if (known) {
            this.pendingUserBubbles.delete(frame.client_msg_id);
        } else {
            await this.addUserBubble(String(frame.text ?? (frame.message && frame.message.text) ?? ''));
        }
        this.turn = null;
        if (!replay) {
            // Open the assistant message right away so the typing indicator shows while waiting.
            this.host.setRunning(true);
            await this.currentTurn(frame);
        }
    }

    /**
     * Add a user bubble.
     *
     * @param {string} text
     * @returns {Promise<HTMLElement>}
     */
    async addUserBubble(text) {
        const bubble = await renderNode('message_user', {text});
        this.appendMessage(bubble);
        this.turn = null;
        this.host.layout.scroll(true);
        return bubble;
    }

    /**
     * Append a node to the message list (hiding the empty state).
     *
     * @param {HTMLElement} node
     */
    appendMessage(node) {
        this.regions.empty.classList.add('d-none');
        this.regions.messages.appendChild(node);
    }
}
