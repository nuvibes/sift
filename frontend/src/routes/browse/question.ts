/*
 * What the Browse address ASKS, as against where in the answer it stands.
 *
 * Pure and apart from the page so the rule can be held by a test without mounting the whole wall.
 */
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

/*
 * The bare word being searched for, or empty when the question is anything more than one.
 *
 * Bare is decided by the ABSENCE of a colon and a leading minus rather than by parsing, because the
 * client has no parser and must never grow one; the worst this can be wrong about is drawing a band
 * that comes back empty, which draws nothing.
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
