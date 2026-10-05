/* What the Browse address ASKS, as against where in the answer it stands. */
import { ANCHOR, NEAR } from '$lib/grid/anchor';

/** Parameters that say where the wall is, never what it is asked. */
const POSITION: readonly string[] = [ANCHOR, NEAR];

/** Every parameter but the position and the ones named in `also`. */
export function questionIn(
	params: URLSearchParams,
	also: readonly string[] = []
): Record<string, string> {
	return Object.fromEntries(
		[...params].filter(([name]) => !POSITION.includes(name) && !also.includes(name))
	);
}

/** Parameters that order the answer, turn its page or say how its words are read. */
const NOT_A_FILTER: readonly string[] = ['q', 'sort', 'seed', 'offset', 'meaning', 'depth'];

/** Whether the question carries a filter beside its words. */
export function filteredBy(question: Record<string, string>): boolean {
	return Object.keys(question).some((name) => !NOT_A_FILTER.includes(name));
}

const PLACE: readonly string[] = [...POSITION, 'offset'];

/** The address with its words, its filters or both taken off; `also` names neither (the explorer). */
export function cleared(
	params: URLSearchParams,
	take: { words: boolean; filters: boolean },
	also: readonly string[] = []
): URLSearchParams {
	const kept = new URLSearchParams();
	for (const [name, value] of params) {
		const filter = name === 'depth' || (!NOT_A_FILTER.includes(name) && !also.includes(name));
		const gone = PLACE.includes(name) || (take.words && name === 'q') || (take.filters && filter);
		if (!gone) kept.append(name, value);
	}
	return kept;
}

/*
 * The bare word searched for, or empty for anything more. Bare is the absence of a colon and a
 * minus, since the client has no parser; a wrong guess draws a band that comes back empty.
 */
export function bareWord(question: Record<string, string>): string {
	const word = (question.q ?? '').trim();
	const bare =
		word.length > 0 &&
		!word.includes(':') &&
		!word.includes('-') &&
		Object.keys(question).length === 1;
	return bare ? word : '';
}
