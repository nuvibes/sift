/* SPDX-License-Identifier: AGPL-3.0-or-later */
/*
 * How many things an entity holds, as the one sentence every wall draws under a name.
 *
 * One place, because copies come apart in the two ways a copied sentence always does: the word
 * ("file", not "item": see the one-word gate) and the separator in the number, so a person on
 * eight thousand files reads a grouped figure that can be taken in at a glance.
 *
 * `toLocaleString` is the application's own way of writing a number for somebody to read (the
 * jobs panel, the ledger and the selection bar all use it), so a count on a card is grouped the
 * way a count everywhere else already is, in whatever the browser's own locale groups by.
 *
 * A Photo Set counts PICTURES rather than files, and that is a real difference rather than a
 * synonym: a set is a post's worth of stills and saying "files" about it would be the one place the
 * word stopped meaning what it means everywhere else.
 */

import { size } from '$lib/library/facts';

/**
 * A count as a person reads it: "8,400", grouped the way the browser's own locale groups. What the
 * sentences here write their number with, and what a bare count in a column uses (a filter
 * panel's "12,345", Maintenance's figures), so a count alone and a count in a sentence cannot
 * group differently ("12,345" beside "12345 files").
 */
export function counted(count: number): string {
	return count.toLocaleString();
}

/** "8,400 files", or "1 file". What a person, a Site, a tag or a collection holds. */
export function filesSaid(count: number): string {
	return `${counted(count)} ${count === 1 ? 'file' : 'files'}`;
}

/** "397 people", or "1 person". Grouped like the files beside it on the same line. */
export function peopleSaid(count: number): string {
	return count === 1 ? '1 person' : `${count.toLocaleString()} people`;
}

/**
 * The mark between two counts on one line: an EM DASH, spaced.
 *
 * One separator for every line that puts counts side by side ("8,354 files \u2014 397 people"),
 * the same mark the server writes for the same kind of line (a waiting username's "1,867 files on
 * Instagram" on the Organize board): one mark for one pause. The em dash is the one the words on
 * screen use.
 */
export const COUNTS_SEPARATOR = ' \u2014 ';

/** Counts side by side, joined by `COUNTS_SEPARATOR`, leaving out any that are empty. */
export function joinCounts(...parts: string[]): string {
	return parts.filter((part) => part !== '').join(COUNTS_SEPARATOR);
}

/** "24 pictures", or "1 picture". What a Photo Set holds. */
export function picturesSaid(count: number): string {
	return `${count.toLocaleString()} ${count === 1 ? 'picture' : 'pictures'}`;
}

/**
 * The mark between a count of files and their size: a MIDDLE DOT, spaced ("1,867 files \u00b7 23 GB").
 *
 * Not `COUNTS_SEPARATOR`. That one stands between two counts of different things, each a figure of
 * its own; a size is not a second count but a fact about the same files, and the screen already
 * writes a file's facts with this mark between them (a size, a shape and a length on a duplicate's
 * row). One mark per kind of pause.
 */
export const SIZE_SEPARATOR = ' \u00b7 ';

/**
 * A row that may say how big its files are: `size_bytes` on a wall's row, which the server sums
 * over exactly the files the row's count counts, for the viewer asking.
 *
 * One shape for every kind of row a wall draws (a person, a Site, a tag, a collection, a Photo
 * Set, a username), so a card asks one question whatever it is a card of; absent and null both
 * mean the answer did not say, which is drawn as nothing rather than as nought.
 */
export interface Sized {
	size_bytes?: number | null;
}

/** The size a row says, or null where it says none. */
export function sizeOf(row: object): number | null {
	const said = (row as Sized).size_bytes;
	return typeof said === 'number' ? said : null;
}

/**
 * A count with the size of what it counts beside it: "1,867 files \u00b7 23 GB".
 *
 * The size goes through `facts.size`, the application's one way of writing bytes, so a card and a
 * file's own record cannot write one size two ways. Left off where it is not known, and where the
 * count is nought: "0 files \u00b7 0 B" says the same thing twice.
 */
export function withSize(said: string, count: number, bytes: number | null | undefined): string {
	if (count === 0 || typeof bytes !== 'number') return said;
	const written = size(bytes);
	return written === null ? said : `${said}${SIZE_SEPARATOR}${written}`;
}

/** "1,867 files \u00b7 23 GB": `filesSaid` with the size of those files beside it. */
export function filesSized(count: number, bytes: number | null | undefined): string {
	return withSize(filesSaid(count), count, bytes);
}

/** "24 pictures \u00b7 310 MB": a Photo Set's count with the size of those pictures. */
export function picturesSized(count: number, bytes: number | null | undefined): string {
	return withSize(picturesSaid(count), count, bytes);
}
