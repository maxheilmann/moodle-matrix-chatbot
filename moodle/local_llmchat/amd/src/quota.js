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
 * The token budget: the ring in the header and its details panel.
 *
 * @module     local_llmchat/quota
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {renderInto} from 'local_llmchat/render';
import {fill, formatTime, formatNumber} from 'local_llmchat/util';

/** Circumference of the token ring (r = 15 in a 36-unit viewBox). */
const RING_CIRCUMFERENCE = 2 * Math.PI * 15;

/**
 * What the token budget shows for a quota frame (pure, so it can be tested without a page).
 *
 * @param {Object} frame quota{used_today, limit_today, used_month, limit_month, resets_at, exempt?}
 * @param {Object} str language strings ('quota:today', 'quota:remaining', ...)
 * @returns {Object} {rows, exempt, percent, label, state, tooltip, hasresets, resets, visible}
 */
export const quotaView = (frame, str) => {
    const {'used_today': usedToday, 'limit_today': limitToday, 'used_month': usedMonth, 'limit_month': limitMonth,
        'resets_at': resetsAt} = frame;
    const windows = [
        [str['quota:today'], str['quota:remaining'], usedToday, limitToday],
        [str['quota:thismonth'], str['quota:remainingmonth'], usedMonth, limitMonth],
    ].filter(([, , , limit]) => limit !== undefined && limit !== null && limit > 0);
    const rows = windows.map(([label, , used, limit]) => {
        const percent = Math.min(100, Math.round(((used || 0) / limit) * 100));
        return {
            label, percent, warning: percent >= 80 && percent < 100, danger: percent >= 100,
            usedof: fill(str['quota:usedof'], {used: formatNumber(used || 0), limit: formatNumber(limit)}),
        };
    });
    const exempt = Boolean(frame.exempt);
    const percent = exempt ? 0 : Math.max(0, ...rows.map((r) => r.percent));
    let state = '';
    if (rows.some((r) => r.danger)) {
        state = 'danger';
    } else if (rows.some((r) => r.warning)) {
        state = 'warning';
    }
    return {
        rows, exempt, percent,
        // The number inside tells the ring apart from a loading spinner.
        label: exempt ? '∞' : `${percent}%`,
        state,
        tooltip: exempt ? str['quota:exemptuser']
            : windows.map(([, text, used, limit]) => fill(text, {used: formatNumber(used || 0), limit: formatNumber(limit)}))
                .join(' · '),
        hasresets: Boolean(resetsAt) && !exempt,
        resets: formatTime(resetsAt),
        visible: exempt || rows.length > 0,
    };
};

export class QuotaMeter {
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
     * Wire the panel: it closes on Escape and on any click outside it.
     */
    bind() {
        document.addEventListener('click', (e) => {
            if (!this.regions.quota.contains(e.target)) {
                this.toggleQuotaPanel(false);
            }
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.regions['quota-panel'].classList.contains('show')) {
                this.toggleQuotaPanel(false);
                this.regions['quota-button'].focus();
            }
        });
    }

    /**
     * Token budget: a small ring in the header (fullest window), a tooltip, and a details panel.
     *
     * @param {Object} frame quota{used_today, limit_today, used_month, limit_month, resets_at, exempt?}
     */
    async onQuota(frame) {
        const view = quotaView(frame, this.str);
        this.regions['quota-ring'].setAttribute(
            'stroke-dasharray', `${(view.percent / 100) * RING_CIRCUMFERENCE} ${RING_CIRCUMFERENCE}`);
        this.regions['quota-percent'].textContent = view.label;
        this.regions['quota-percent'].classList.toggle('local-llmchat-ringlabel-long', view.label.length > 3);
        this.regions.quota.dataset.state = view.state;
        this.regions['quota-button'].title = view.tooltip;
        await renderInto(this.regions['quota-panel'], 'local_llmchat/quota_panel', {
            exempt: view.exempt, rows: view.rows, hasresets: view.hasresets, resets: view.resets,
        });
        this.regions.quota.classList.toggle('invisible', !view.visible);
    }

    /**
     * Open or close the token budget panel behind the ring.
     *
     * @param {boolean} open
     */
    toggleQuotaPanel(open) {
        this.regions['quota-panel'].classList.toggle('show', open);
        this.regions['quota-button'].setAttribute('aria-expanded', open ? 'true' : 'false');
    }
}
