/*
 * THE WORDS A WALL IS SEARCHED BY, kept in its address.
 *
 * A wall's search box and the chip on the bar are two views of one question, and the address is
 * the only place both can read it from. Held in the box alone, the words would be lost to Back, a
 * link to a wall filtered by them could not be written, and the search band's See all would open
 * the wall unfiltered under a chip saying it was filtered.
 *
 * `q` is the parameter because it is the one the library's own words ride in: the chip reads it,
 * the top search box follows it, and a See all written as `/people?q=...` is the wall's own address.
 */
import { goto } from '$app/navigation';
import { ANCHOR, NEAR } from '$lib/grid/anchor';

/** The address's name for a wall's search words. */
export const WORDS = 'q';

/*
 * A list whose box searches something other than the library keeps its words under a name of its
 * own, since the bar reads `q` as the query language. Loops asks after a Loop's own name as
 * `called` and tells the bar that name (the screen's `words`), so its chip wears them; the Faces
 * tabs ask after a person's as `who`, read by the tab's own box and count alone.
 */
export const LOOP_WORDS = 'called';
export const FACES_WORDS = 'who';

/** The words in this address, trimmed; empty when there are none. */
export function wordsIn(url: URL, name: string = WORDS): string {
	return (url.searchParams.get(name) ?? '').trim();
}

/**
 * The address carrying these words instead, or null when it carries them already.
 *
 * The position goes with the old words: a row to start at belongs to the list the old words
 * produced, and landing part way down a different one is not what typing meant.
 */
export function addressWith(url: URL, typed: string, name: string = WORDS): string | null {
	const wanted = typed.trim();
	if (wanted === wordsIn(url, name)) return null;
	const next = new URLSearchParams(url.searchParams);
	if (wanted) next.set(name, wanted);
	else next.delete(name);
	for (const position of [ANCHOR, NEAR, 'offset']) next.delete(position);
	const search = next.toString();
	return `${url.pathname}${search ? `?${search}` : ''}`;
}

/*
 * The box's half: writing what was typed, and telling its own write from anybody else's.
 *
 * A write comes back as a change of address, and the box must not be handed its own words back:
 * somebody still typing would lose every letter typed since the write left. Words arriving any
 * other way (the chip's cross, Back, a link) are put in the box.
 */
export class WallWords {
	#ours: string | null = null;
	readonly #name: string;

	constructor(name: string = WORDS) {
		this.#name = name;
	}

	/** Put what was typed into the address. False when it was there already. */
	write(url: URL, typed: string): boolean {
		const next = addressWith(url, typed, this.#name);
		if (next === null) return false;
		this.#ours = typed.trim();
		void goto(next, { replaceState: true, keepFocus: true, noScroll: true });
		return true;
	}

	/** Whether these words, now in the address, are the ones this box just wrote. */
	echoed(words: string): boolean {
		const ours = this.#ours === words;
		this.#ours = null;
		return ours;
	}
}

/**
 * What an empty wall says, by why it is empty.
 *
 * A wall filtered to nothing is not an empty library: "No Sites yet" under a search reads as if
 * every Site had gone. So the words, then the bar's filters, are named before the wall's own
 * first-run sentence.
 */
export function emptyWallSays(
	plural: string,
	words: string,
	filtered: boolean,
	otherwise: string
): string {
	if (words) return `No ${plural} match "${words}".`;
	if (filtered) return `No ${plural} match these filters.`;
	return otherwise;
}
