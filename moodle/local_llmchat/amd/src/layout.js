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
 * Page layout: card height, composer size, sidebar and scrolling of the message list.
 *
 * @module     local_llmchat/layout
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/** Space kept below the chat card. */
const BOTTOM_GAP = 16;

/** Moodle's floating page controls (help button, drawer toggles) that may sit over the card. */
const FLOATING_CONTROLS = [
    '.btn-footer-popover', '.drawer-toggles .drawer-toggler', '[data-region="footer-container-popover"] .btn',
].join(', ');

export class Layout {
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
        this.stickToBottom = true;
    }

    /**
     * Make the chat card fill the window below its top edge, with the composer always in view.
     * Only for themes without Boost's drawer layout, which styles.css sizes.
     */
    fitHeight() {
        const panel = this.regions.panel;
        const box = panel.getBoundingClientRect();
        const top = box.top + window.scrollY;
        // Keep the composer clear of floating controls that overlap the card horizontally.
        let gap = BOTTOM_GAP;
        document.querySelectorAll(FLOATING_CONTROLS).forEach((control) => {
            const r = control.getBoundingClientRect();
            const fixed = window.getComputedStyle(control).position === 'fixed' ||
                window.getComputedStyle(control.offsetParent || control).position === 'fixed';
            if (r.width && fixed && r.right > box.left && r.left < box.right) {
                gap = Math.max(gap, window.innerHeight - r.top + 8);
            }
        });
        const height = Math.max(440, window.innerHeight - top - gap);
        panel.style.height = `${height}px`;
        if (!this.regions.sidebar) {
            return;
        }
        if (window.matchMedia('(min-width: 992px)').matches) {
            this.regions.sidebar.style.height = `${height}px`;
        } else {
            this.regions.sidebar.style.height = '';
        }
    }

    /**
     * Grow the composer with its content, up to a limit.
     */
    autosize() {
        const input = this.regions.input;
        input.style.height = 'auto';
        input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
    }

    /**
     * Collapse the conversation list again after a pick on small screens.
     */
    closeSidebarOnMobile() {
        if (!this.regions.sidebar) {
            return;
        }
        this.regions.sidebar.classList.remove('show');
        this.root.querySelectorAll(`[aria-controls="${this.regions.sidebar.id}"]`)
            .forEach((b) => b.setAttribute('aria-expanded', 'false'));
    }

    /**
     * Scroll to the newest message, unless the user scrolled up to read.
     *
     * @param {boolean} [force]
     */
    scroll(force = false) {
        const m = this.regions.messages;
        if (force || this.stickToBottom) {
            m.scrollTop = m.scrollHeight;
        }
    }

    /**
     * Show the loading indicator (while a conversation's history loads) or the empty state.
     *
     * @param {boolean} loading
     */
    showLoading(loading) {
        this.regions.loading.classList.toggle('d-none', !loading);
        this.regions.empty.classList.toggle('d-none', loading);
    }

    /**
     * Hide the message list (all but the loading indicator) while a stored history renders.
     *
     * @param {boolean} replaying
     */
    setReplaying(replaying) {
        this.regions.messages.classList.toggle('local-llmchat-replaying', replaying);
    }
}
