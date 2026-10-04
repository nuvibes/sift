/*
 * Addresses that have been renamed, and what answers their question now.
 *
 * ONE table, so a moved address is followed in one place rather than in each page that has to
 * follow it. The queues have their own (`movedTo` in `$lib/organize/panels.ts`, beside the
 * registry that says what draws what), and this is the same shape for the addresses that are not a
 * queue: a top-level destination that changed its word.
 *
 * `/platforms` and `/platforms/<id>` are the old words for Sites, and they live on in links,
 * bookmarks, open tabs and notes. Left alone they would reach the screen this application draws for
 * an address it has never heard of, which is the right answer for a page from another version and a
 * wrong one for a page that is still here under a different name.
 *
 * 308 rather than 307: the move is permanent. What is preserved is the query, because a filter
 * somebody shared is part of where they were going, and dropping it would land them on the wall
 * with their filter quietly gone.
 */

/** The old first segment, and the one that answers for it now.
 *
 * A Map and not an object literal: a segment is whatever somebody typed into the address, and an
 * object answers `toString` or `constructor` with what it inherited: a function where a word was
 * promised. A Map holds only what was put in it. */
const MOVED: ReadonlyMap<string, string> = new Map([['platforms', 'sites']]);

/**
 * Where a renamed top-level address leads now, or null where this one has not moved.
 *
 * Takes the SEGMENT rather than the whole path, so the page that redirects keeps whatever it has
 * to add after it (an id, a query) instead of this having to know about every shape of address
 * underneath a name.
 */
export function movedAddress(segment: string): string | null {
	return MOVED.get(segment) ?? null;
}
