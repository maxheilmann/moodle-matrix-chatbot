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
 * Small pure helpers of the chat client.
 *
 * @module     local_llmchat/util
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/**
 * Fill a {$a} / {$a->x} placeholder string.
 *
 * @param {string} template
 * @param {*} a
 * @returns {string}
 */
export const fill = (template, a) => String(template)
    .replace(/\{\$a->(\w+)\}/g, (m, k) => (a && a[k] !== undefined ? String(a[k]) : ''))
    .replace(/\{\$a\}/g, a === undefined || a === null ? '' : String(a));

/**
 * Cache key of a Moodle object ref.
 *
 * @param {Object} ref {kind, id}
 * @returns {string} "kind:id"
 */
export const refKey = (ref) => `${ref.kind}:${ref.id}`;

/**
 * Random id for client_msg_id.
 *
 * @returns {string}
 */
export const uuid = () => (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

/**
 * Format a timestamp (seconds or ISO string) for display.
 *
 * @param {number|string} value
 * @returns {string}
 */
export const formatTime = (value) => {
    if (value === undefined || value === null || value === '') {
        return '';
    }
    const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
    return isNaN(date.getTime()) ? String(value) : date.toLocaleString();
};

export const formatNumber = (n) => (typeof n === 'number' ? n.toLocaleString() : (n ?? '–'));
