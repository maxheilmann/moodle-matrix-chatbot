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
 * Tool cards: a call, its progress and result, and the Moodle links inside them.
 *
 * @module     local_llmchat/toolcards
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Log from 'core/log';
import {renderNode, renderInto, uniqid} from 'local_llmchat/render';
import {callTarget, describeCall, describeResult, linkSegments} from 'local_llmchat/tooldetails';

/** Tool name => pix icon, used when the API's tool metadata names no pix icon. */
const TOOL_ICONS = {
    'search_content': ['a/search', 'core'],
    'search_text': ['a/search', 'core'],
    'search_regex': ['a/search', 'core'],
    'list_dir': ['i/folder', 'core'],
    'stat_file': ['i/files', 'core'],
    'read_file': ['i/files', 'core'],
    'list_documents': ['i/calendar', 'core'],
    'current_datetime': ['i/calendar', 'core'],
    'current_user': ['i/user', 'core'],
    'ask_user': ['i/question', 'core'],
};

/**
 * Pix icon for a tool: an icon named by the API as "component:key" wins, then the name table.
 *
 * @param {string} name
 * @param {Object} meta tool metadata from ready{tools}
 * @returns {Array} [key, component]
 */
const toolIcon = (name, meta) => {
    const named = String(meta.icon || '').match(/^([a-z0-9_]+):([a-z0-9_/-]+)$/);
    if (named) {
        return [named[2], named[1]];
    }
    return TOOL_ICONS[name] || ['tool', 'local_llmchat'];
};

export class ToolCards {
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
        /** Tool name => metadata from ready{tools}. */
        this.tools = new Map();
    }

    /**
     * Tool card.
     *
     * @param {Object} frame tool_call{id, name, args, locations?}
     */
    async onToolCall(frame) {
        const turn = await this.host.turns.currentTurn(frame);
        const meta = this.tools.get(frame.name) || {};
        const args = frame.args || {};
        const firstArg = callTarget(args, frame.locations, this.str['toolval:root']);
        let label;
        if (meta.summary_template) {
            label = meta.summary_template.replace(/\{(\w+)\}/g, (m, k) => (args[k] !== undefined ? String(args[k]) : ''));
        } else {
            label = `${meta.label || frame.name}${firstArg ? ` „${firstArg}“` : ''}`;
        }
        const [icon, component] = toolIcon(frame.name, meta);
        const call = describeCall(args, Object.assign({locations: frame.locations}, this.displayOptions()));
        const node = await renderNode('tool_call', Object.assign({uniqid: uniqid('llmchat-tool'), icon, component, label}, call));
        this.host.turns.advance(turn, 'tool_call');
        this.host.turns.toolGroup(turn).appendChild(node);
        node.dataset.tool = frame.name;
        turn.cards.set(frame.id, node);
        // Not awaited: links arrive when ready and never hold up the stream.
        this.linkPaths(node, call);
    }

    /**
     * Intermediate tool progress.
     *
     * @param {Object} frame
     */
    async onToolProgress(frame) {
        const card = (await this.host.turns.currentTurn(frame)).cards.get(frame.id);
        const text = frame.message || frame.text || frame.summary || '';
        if (card && text) {
            card.querySelector('[data-region="summary"]').textContent = String(text);
        }
    }

    /**
     * Tool result: spinner becomes a check mark, summary and citations go into the card.
     *
     * @param {Object} frame tool_result{id, summary, citations?, attachments?}
     */
    async onToolResult(frame) {
        const turn = await this.host.turns.currentTurn(frame);
        const card = turn.cards.get(frame.id);
        if (!card) {
            return;
        }
        this.host.turns.advance(turn, 'tool_result');
        card.querySelector('[data-region="spinner"]').classList.add('d-none');
        card.querySelector('[data-region="done"]').classList.remove('d-none');
        card.querySelector('[data-region="summary"]').textContent = frame.summary ? String(frame.summary) : '';
        const result = describeResult(frame, this.displayOptions());
        await renderInto(card.querySelector('[data-region="result"]'), 'local_llmchat/tool_result', result);
        // Not awaited: links arrive when ready and never hold up the stream.
        this.linkEntries(card, result.refs);
    }

    /**
     * Turn the location breadcrumbs of a tool card into links to what the user can open.
     *
     * @param {HTMLElement} card
     * @param {Object} call result of describeCall()
     */
    async linkPaths(card, call) {
        if (!call.refs.length) {
            return;
        }
        try {
            const links = await this.host.links.resolveRefs(call.refs);
            for (const param of call.params.filter((p) => p.ispath)) {
                const holder = [...card.querySelectorAll('[data-region="path"]')].find((h) => h.dataset.arg === param.arg);
                const segments = linkSegments(param.segments, links);
                if (holder && segments.some((s) => s.hasurl)) {
                    await renderInto(holder, 'local_llmchat/path_breadcrumb', {segments});
                }
            }
        } catch (e) {
            Log.debug(e);
        }
    }

    /**
     * Link the names of listed folders and files to what the user can open in Moodle.
     *
     * @param {HTMLElement} card
     * @param {Array} refs [{kind, id}] of the entries
     */
    async linkEntries(card, refs) {
        if (!refs.length) {
            return;
        }
        try {
            const links = await this.host.links.resolveRefs(refs);
            card.querySelectorAll('[data-region="entry"]').forEach((holder) => {
                if (holder.querySelector('a')) {
                    return;
                }
                const found = String(links[holder.dataset.ref] || '');
                const url = /^https?:\/\//i.test(found) ? found : '';
                if (url) {
                    const link = document.createElement('a');
                    link.href = url;
                    link.target = '_blank';
                    link.rel = 'noopener';
                    link.textContent = holder.textContent;
                    holder.replaceChildren(link);
                }
            });
        } catch (e) {
            Log.debug(e);
        }
    }

    /**
     * Strings and locale for local_llmchat/tooldetails.
     *
     * @returns {Object}
     */
    displayOptions() {
        return {strings: this.str, locale: document.documentElement.lang || undefined};
    }
}
