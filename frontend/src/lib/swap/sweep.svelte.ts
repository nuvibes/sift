// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Sweeping across a wall in swap mode picks for the swap with the same `TileGesture`, over a
 * selection that mirrors `swapMode`: taken up before each press, written back row by row after.
 * A file is picked when any of its rows is; Hidden and refused rows never are; off-screen picks
 * are never touched. Card walls sweep the same way through `cardSweep`.
 */

import { Selection } from '$lib/components/common/selection.svelte';
import { TILE_ID, TileGesture } from '$lib/components/common/tile-gesture.svelte';
import { swapMode, type SwapKind } from './mode.svelte';

export interface SwapRow {
	id: string;
	/** The file the row pictures: the row itself, or the video on a wall of moments. */
	file: string;
	name: string;
	concealed: boolean;
	refused?: boolean;
	kind?: SwapKind;
}

export class SwapSelection extends Selection {
	#rows: () => readonly SwapRow[];

	constructor(rows: () => readonly SwapRow[]) {
		super();
		this.#rows = rows;
	}

	/** What the swap holds among the rows on screen, taken up as what is picked here. */
	takeUp(): void {
		this.hold(
			this.#rows()
				.filter((row) => swapMode.has(row.kind ?? 'asset', row.file))
				.map((row) => row.id)
		);
	}

	only(id: string): void {
		super.only(id);
		this.#mirror();
	}

	extendTo(id: string, items: readonly string[]): void {
		super.extendTo(id, items);
		this.#mirror();
	}

	toggle(id: string): void {
		super.toggle(id);
		this.#mirror();
	}

	#mirror(): void {
		const wanted = new Map<string, SwapRow & { picked: boolean }>();
		for (const row of this.#rows()) {
			if (row.concealed || row.refused) continue;
			const key = `${row.kind ?? 'asset'}:${row.file}`;
			const seen = wanted.get(key);
			const picked = this.has(row.id) || (seen?.picked ?? false);
			wanted.set(key, { ...row, picked });
		}
		for (const row of wanted.values()) {
			const kind = row.kind ?? 'asset';
			const held = swapMode.has(kind, row.file);
			if (row.picked && !held) swapMode.toggle({ kind, id: row.file, name: row.name });
			else if (!row.picked && held) swapMode.drop(kind, row.file);
		}
	}
}

/** What a card in swap mode says of itself, for the card sweep to read off the page. */
export const SWAP_KIND = 'data-swap-kind';
export const SWAP_ID = 'data-swap-id';
export const SWAP_NAME = 'data-swap-name';
/** On a card that will not go in a swap: which mark keeps it out. */
export const SWAP_REFUSED = 'data-swap-refused';

/** The cards on the page offering themselves, in wall order: a card wall is one per screen. */
function cardsOnPage(): SwapRow[] {
	if (typeof document === 'undefined') return [];
	return [...document.querySelectorAll<HTMLElement>(`[${TILE_ID}][${SWAP_KIND}]`)].map((card) => ({
		id: card.getAttribute(TILE_ID) ?? '',
		file: card.getAttribute(SWAP_ID) ?? '',
		name: card.getAttribute(SWAP_NAME) ?? '',
		kind: card.getAttribute(SWAP_KIND) as SwapKind,
		concealed: false,
		refused: card.hasAttribute(SWAP_REFUSED)
	}));
}

/** A card wall's press, hold and sweep in swap mode; one for the window, as the mode is. */
class CardSweep {
	#picks = new SwapSelection(cardsOnPage);
	#gesture = new TileGesture(this.#picks, () => cardsOnPage().map((row) => row.id));

	pressStart(id: string, event: PointerEvent): void {
		// What the swap holds now, before the press measures a run against it.
		this.#picks.takeUp();
		this.#gesture.pressStart(id, event);
	}

	pressEnd(): void {
		this.#gesture.pressEnd();
	}

	/** True when the click was the sweep's and the card must not also toggle itself. */
	clicked(id: string, event: MouseEvent): boolean {
		this.#gesture.clicked(id, event);
		return event.defaultPrevented;
	}
}

export const cardSweep = new CardSweep();
