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
 * Questions the agent asks the user: the box inside its tool card, the answer and its summary.
 *
 * @module     local_llmchat/questions
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import * as frames from 'local_llmchat/frames';
import {rendererFor} from 'local_llmchat/interactions/registry';
import {renderNode} from 'local_llmchat/render';

export class Questions {
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
    }

    /**
     * Interaction (ask_user etc.), rendered by kind.
     *
     * @param {Object} frame question{interrupt_id, kind, payload}
     * @param {boolean} replay
     */
    async onQuestion(frame, replay) {
        const turn = await this.host.turns.currentTurn(frame);
        this.host.turns.advance(turn, 'question');
        const interruptId = String(frame.interrupt_id);
        turn.body.querySelectorAll('[data-interrupt]').forEach((n) => {
            if (n.dataset.interrupt === interruptId) {
                n.remove();
            }
        });
        const renderer = rendererFor(frame.kind);
        const node = await renderer.render(frame.payload || {}, (response, summary) => {
            this.answerInteraction(turn, node, interruptId, response, summary);
        }, {unknowninteraction: this.str.unknowninteraction});
        node.dataset.interrupt = interruptId;
        if (frame.error) {
            const error = document.createElement('div');
            error.className = 'text-danger small mb-2';
            error.textContent = String(frame.error);
            node.prepend(error);
        }
        const card = this.pendingCard(turn, frame.tool);
        if (card) {
            this.openInCard(card, node);
        } else {
            this.host.turns.place(turn, node);
        }
        this.host.turns.newTextSegment(turn);
        this.host.setRunning(false);
        if (!replay) {
            (node.querySelector('input[type="text"]') || node.querySelector('button'))?.focus({preventScroll: true});
        }
    }

    /**
     * The card of the tool call that is waiting for the answer: the latest one of that tool that has no result yet.
     *
     * @param {Object} turn
     * @param {string} tool name of the interactive tool
     * @returns {HTMLElement|undefined}
     */
    pendingCard(turn, tool) {
        return [...turn.cards.values()].reverse().find((card) => card.isConnected
            && (!tool || card.dataset.tool === tool)
            && !card.querySelector('[data-region="spinner"]').classList.contains('d-none'));
    }

    /**
     * Show the question inside the tool card that asked it, so call and question read as one box. The
     * card's label gives way to the bare tool name meanwhile, as the question is right below it.
     *
     * @param {HTMLElement} card
     * @param {HTMLElement} node
     */
    openInCard(card, node) {
        const label = card.querySelector('[data-region="label"]');
        const meta = this.host.cards.tools.get(card.dataset.tool) || {};
        if (label && meta.label) {
            label.dataset.full = label.textContent;
            label.textContent = meta.label;
        }
        card.classList.add('local-llmchat-asking');
        card.querySelector('[data-region="collapsible"]').before(node);
    }

    /**
     * Send an interaction answer: the prompt collapses into a one-line summary of the answer.
     *
     * @param {Object} turn
     * @param {HTMLElement} node
     * @param {string} interruptId
     * @param {Object} response
     * @param {string} summary
     */
    async answerInteraction(turn, node, interruptId, response, summary) {
        try {
            const t = await this.host.issueToken();
            this.host.socket.send(frames.answer({conversationId: this.host.conversationId, interruptId, response, token: t.token}));
            await this.settleInteraction(node, interruptId, summary);
            this.host.setRunning(true);
            this.host.turns.advance(turn, 'user_answer');
            this.host.layout.scroll();
        } catch (e) {
            this.host.showStatus('error', String(e && e.message ? e.message : e));
        }
    }

    /**
     * Replace an open prompt with the one-line summary of its answer. Inside a tool card the prompt folds
     * away and the card gets its own label back.
     *
     * @param {HTMLElement} node the open prompt
     * @param {string} interruptId
     * @param {string} summary
     * @param {boolean} [animate] fold it away (false: gone at once, for a history being replayed)
     */
    async settleInteraction(node, interruptId, summary, animate = true) {
        const answered = await renderNode('interaction_answered', {answer: summary});
        answered.dataset.interrupt = interruptId;
        const card = node.closest('[data-region="tool"]');
        if (!card) {
            node.replaceWith(answered);
            return;
        }
        const label = card.querySelector('[data-region="label"]');
        if (label && label.dataset.full) {
            label.textContent = label.dataset.full;
        }
        card.classList.remove('local-llmchat-asking');
        node.after(answered);
        if (animate) {
            this.collapse(node);
        } else {
            node.remove();
        }
    }

    /**
     * A stored (or another tab's) answer: the prompt it belongs to is answered, so it no longer asks.
     * Without this a re-opened conversation would show every answered question as open again.
     *
     * @param {Object} frame user_answer{interrupt_id, response}
     * @param {boolean} replay
     */
    async onUserAnswer(frame, replay) {
        const turn = await this.host.turns.currentTurn(frame);
        const interruptId = String(frame.interrupt_id);
        const node = [...turn.body.querySelectorAll('[data-region="interaction"][data-interrupt]')]
            .find((n) => n.dataset.interrupt === interruptId
                && !n.classList.contains('local-llmchat-answered') && !n.classList.contains('local-llmchat-collapsing'));
        if (!node) {
            return; // Answered here: answerInteraction() already settled it.
        }
        const summary = frames.answerSummary(frame.response, {
            yes: this.str['confirm:yes'], no: this.str['confirm:no'],
            label: (id) => [...node.querySelectorAll('[data-action="choose"]')]
                .find((b) => b.dataset.id === id)?.textContent.trim() || '',
        });
        await this.settleInteraction(node, interruptId, summary, !replay);
        this.host.turns.advance(turn, 'user_answer');
    }

    /**
     * Fold a block away (height and opacity ease to nothing), then remove it. Without motion (reduced
     * motion, or no transition end) it goes right away.
     *
     * @param {HTMLElement} node
     */
    collapse(node) {
        node.style.height = `${node.offsetHeight}px`;
        node.classList.add('local-llmchat-collapsing');
        node.setAttribute('inert', '');
        // Force a reflow so the height above is the transition's start value.
        void node.offsetHeight;
        node.style.height = '0';
        const done = () => node.remove();
        node.addEventListener('transitionend', done, {once: true});
        setTimeout(done, 400);
    }
}
