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
 * Links for Moodle objects (files, folders) a tool card names, resolved on the server for the user.
 *
 * @module     local_llmchat/links
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {refKey} from 'local_llmchat/util';

/** Refs per local_llmchat_resolve_refs call (its MAX_REFS). */
const REFS_PER_CALL = 100;

export class LinkResolver {
    /**
     * Constructor.
     *
     * @param {Function} fetchLinks (chunks) => Promise[]: asks the server for each chunk of refs
     *     ([{kind, id}]) and resolves to [{kind, id, url}]; the chat passes its web service call, tests a fake
     */
    constructor(fetchLinks) {
        this.fetchLinks = fetchLinks;
        this.linkCache = new Map();
        /** Refs held back while a history replays (see batchRefs), or null. */
        this.refBatch = null;
    }

    /**
     * Links for Moodle objects by id, resolved on the server for the current user; cached per page.
     *
     * @param {Array} refs [{kind, id}]
     * @returns {Promise<Object>} "kind:id" => url ('' when the user cannot open it)
     */
    async resolveRefs(refs) {
        const missing = [];
        refs.filter((ref) => !this.linkCache.has(refKey(ref))).forEach((ref) => {
            const pending = {ref};
            this.linkCache.set(refKey(ref), new Promise((resolve) => {
                pending.resolve = resolve;
            }));
            missing.push(pending);
        });
        if (this.refBatch) {
            // A history is replaying: its cards' links are asked for together once it is in place.
            this.refBatch.push(...missing);
        } else {
            this.requestRefs(missing);
        }
        const out = {};
        for (const ref of refs) {
            out[refKey(ref)] = await this.linkCache.get(refKey(ref));
        }
        return out;
    }

    /**
     * Forget every resolved link (access may have changed since).
     */
    clear() {
        this.linkCache.clear();
    }

    /**
     * Collect the refs of the cards rendered from now on until flushRefs() (a replaying history).
     */
    batchRefs() {
        this.refBatch = this.refBatch || [];
    }

    /**
     * Ask for the refs collected since batchRefs() and stop collecting.
     */
    flushRefs() {
        const batch = this.refBatch || [];
        this.refBatch = null;
        this.requestRefs(batch);
    }

    /**
     * Resolve refs on the server: one HTTP request, split into calls of at most REFS_PER_CALL refs.
     * A ref that fails resolves to '' and is dropped from the cache, so a later card asks again.
     *
     * @param {Array} pending [{ref, resolve}]
     */
    requestRefs(pending) {
        if (!pending.length) {
            return;
        }
        const chunks = [];
        for (let i = 0; i < pending.length; i += REFS_PER_CALL) {
            chunks.push(pending.slice(i, i + REFS_PER_CALL));
        }
        const calls = this.fetchLinks(chunks.map((chunk) => chunk.map((p) => p.ref)));
        chunks.forEach(async(chunk, i) => {
            let result;
            try {
                result = await calls[i];
            } catch (e) {
                chunk.forEach((p) => {
                    this.linkCache.delete(refKey(p.ref));
                    p.resolve('');
                });
                return;
            }
            chunk.forEach((p) => p.resolve((result.find((r) => refKey(r) === refKey(p.ref)) || {}).url || ''));
        });
    }
}
