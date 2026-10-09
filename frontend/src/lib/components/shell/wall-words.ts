/* A wall's search words live in its address (`q`), so the box, the chip, Back and a link agree. */
import { goto } from '$app/navigation';
import { ANCHOR, NEAR } from '$lib/grid/anchor';

/** The address's name for a wall's search words. */
export const WORDS = 'q';

// A box searching something other than the library keeps its own name, as the bar reads `q` as
// the query language: a Loop's name as `called`, a person's on the Faces tabs as `who`.
export const LOOP_WORDS = 'called';
export const FACES_WORDS = 'who';

/** The words in this address, trimmed; empty when there are none. */
export function wordsIn(url: URL, name: string = WORDS): string {
	return (url.searchParams.get(name) ?? '').trim();
}

/** The address carrying these words, or null if it does; the old position is dropped. */
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

// A write comes back as an address change; the box must not be handed its own words back.
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

/** What an empty wall says, naming the search or filters in force first. */
export function emptyWallSays(
	plural: string,
	words: string,
	filtered: boolean,
	otherwise: string
): string {
	if (words && filtered) return `No ${plural} match "${words}" with these filters.`;
	if (words) return `No ${plural} match "${words}".`;
	if (filtered) return `No ${plural} match these filters.`;
	return otherwise;
}

/** The press that empties what `emptyWallSays` named, in the same terms. */
export function clearWallSays(words: string, filtered: boolean): string {
	if (words && filtered) return 'Clear the search and filters';
	return words ? 'Clear the search' : 'Clear the filters';
}
