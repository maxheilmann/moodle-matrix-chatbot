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
 * The source card a citation chip shows on hover or focus.
 *
 * @module     local_llmchat/citations
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {renderNode, uniqid} from 'local_llmchat/render';
import {citationCard} from 'local_llmchat/tooldetails';

export class CitationCards {
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
        /** Assistant message node => the citations its [n] chips refer to. */
        this.citationsOf = new WeakMap();
        /** The citation card shown for a hovered or focused chip: {chip, card}, or null. */
        this.citeCard = null;
    }

    /**
     * Wire the hover and focus events of the citation chips.
     */
    bind() {
        // Citation chips ([n] in an answer) show their source on hover and keyboard focus.
        const chipOf = (e) => e.target.closest && e.target.closest('.local-llmchat-cite[data-cite]');
        ['mouseover', 'focusin'].forEach((type) => this.root.addEventListener(type, (e) => {
            const chip = chipOf(e);
            if (chip) {
                this.showCitationCard(chip);
            }
        }));
        ['mouseout', 'focusout'].forEach((type) => this.root.addEventListener(type, (e) => {
            const chip = chipOf(e);
            if (chip && !(e.relatedTarget && chip.contains(e.relatedTarget))) {
                this.hideCitationCard(chip);
            }
        }));
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.hideCitationCard();
            }
        });
    }

    /**
     * Show the source card for a citation chip, above it (below when there is no room).
     *
     * @param {HTMLElement} chip a.local-llmchat-cite[data-cite]
     */
    async showCitationCard(chip) {
        if (this.citeCard && this.citeCard.chip === chip) {
            return;
        }
        this.hideCitationCard();
        const citations = this.citationsOf.get(chip.closest('[data-role="assistant"]')) || [];
        const citation = citations.find((c) => Number(c.index) === Number(chip.dataset.cite));
        if (!citation) {
            return;
        }
        const shown = {chip, card: null};
        this.citeCard = shown;
        const card = await renderNode('citation_card', citationCard(citation, this.str.page));
        if (this.citeCard !== shown || !chip.isConnected) {
            return;
        }
        shown.card = card;
        card.id = uniqid('local-llmchat-citecard');
        chip.setAttribute('aria-describedby', card.id);
        this.root.appendChild(card);
        const gap = 6;
        const margin = 8;
        const anchor = chip.getBoundingClientRect();
        const box = card.getBoundingClientRect();
        const above = anchor.top - box.height - gap;
        const top = above >= margin ? above : anchor.bottom + gap;
        const left = Math.min(Math.max(margin, anchor.left + anchor.width / 2 - box.width / 2),
            window.innerWidth - box.width - margin);
        card.style.top = `${Math.round(top)}px`;
        card.style.left = `${Math.round(Math.max(margin, left))}px`;
        card.classList.add('show');
    }

    /**
     * Remove the citation card (only the one for `chip`, when given).
     *
     * @param {HTMLElement} [chip]
     */
    hideCitationCard(chip) {
        const shown = this.citeCard;
        if (!shown || (chip && shown.chip !== chip)) {
            return;
        }
        this.citeCard = null;
        shown.chip.removeAttribute('aria-describedby');
        if (shown.card) {
            shown.card.remove();
        }
    }
}
