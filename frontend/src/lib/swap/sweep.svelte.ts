// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Sweeping across a wall of files in swap mode picks them FOR THE SWAP.
 *
 * Outside the mode a press held and dragged across tiles picks them into the wall's selection
 * (`TileGesture`, `Selection`): a run from the tile the press began on, painting in the direction
 * that first tile decided, and giving back an overshoot. In swap mode the same hand means the same
 * thing about a different pick: what the swap will offer. So the gesture is not written a second
 * time. The wall keeps a second `TileGesture` for the mode, over this selection, and this
 * selection hands every change it makes on to `swapMode`.
 *
 * It is a MIRROR of the swap's picks on screen, never a second store of them:
 *
 *   - before each press the wall calls `takeUp`, and what the swap holds among the rows on screen
 *     becomes what is picked here, so a pick taken out in the drawer since is not painted back;
 *   - after each change it writes the difference, row by row, to `swapMode`: added where a row is
 *     now picked and the swap does not hold its file, dropped where it is not and the swap does;
 *   - a file is picked when ANY of its rows is, since a wall of moments draws one file more than
 *     once and the swap offers files;
 *   - a Hidden row is never picked, as a press on it never is: the server would leave it out, and a
 *     mark on a tile that will not travel is a promise the offer does not keep;
 *   - nor is a row that will not go (`refused`: Kept local or "Don't swap", on it or above it),
 *     which wears the refused mark instead and says why when it is pressed alone;
 *   - a pick off screen is never touched: only the rows handed in are compared.
 *
 * The card walls (People, Sites, tags, Collections, Photo Sets) sweep the same way, through
 * `cardSweep` below: a card in swap mode hands its press to it rather than to its wall's own
 * gesture. Without it a drag across cards in swap mode would run the wall's selection (the bar
 * that shares or hides), because only a press on a card, never a sweep, would know about the mode.
 */

import { Selection } from '$lib/components/common/selection.svelte';
import { TILE_ID, TileGesture } from '$lib/components/common/tile-gesture.svelte';
import { swapMode, type SwapKind } from './mode.svelte';

/** One row of the wall, as the swap needs it. */
export interface SwapRow {
	/** The row's own id, the one the tile carries and the gesture reads. */
	id: string;
	/** The file the row is a picture of: the row itself on most walls, the video on a wall of moments. */
	file: string;
	/** What the drawer calls it. */
	name: string;
	/** Hidden: never picked. */
	concealed: boolean;
	/** Will not go in a swap (Kept local or "Don't swap"): never picked either. */
	refused?: boolean;
	/** What kind of thing the swap offers for it: a file unless a card says otherwise. */
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

	/** Write what is picked here to the swap, file by file. See the header. */
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

/** Every card on the page that offers itself in the swap, in the order the wall draws them. The
 *  wall's own list is not in reach of a card, and the page is: a card wall is one per screen. */
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

/**
 * A card wall's press, hold and sweep in swap mode: the wall's gesture, over the swap's picks.
 *
 * One for the window, because the cards that use it are one wall's at a time and the mode is the
 * window's. A card hands it the press, the let-go and the click while the mode is on.
 */
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

	/** The click a press ends in. True when it was the sweep's (a hold, a run, a pick made while
	 *  others are picked) and the card must not also toggle itself. */
	clicked(id: string, event: MouseEvent): boolean {
		this.#gesture.clicked(id, event);
		return event.defaultPrevented;
	}
}

export const cardSweep = new CardSweep();
