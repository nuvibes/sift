/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* How many things an entity holds, as the one sentence every wall draws under a name. */

import { size } from '$lib/library/facts';

/** A count as a person reads it: "8,400", grouped the way the browser's own locale groups. */
export function counted(count: number): string {
	return count.toLocaleString();
}

/** "8,400 files", or "1 file". What a person, a Site, a tag or a collection holds. */
export function filesSaid(count: number): string {
	return `${counted(count)} ${count === 1 ? 'file' : 'files'}`;
}

export function peopleSaid(count: number): string {
	return count === 1 ? '1 person' : `${count.toLocaleString()} people`;
}

export const COUNTS_SEPARATOR = ' \u2014 ';

export function joinCounts(...parts: string[]): string {
	return parts.filter((part) => part !== '').join(COUNTS_SEPARATOR);
}

export function picturesSaid(count: number): string {
	return `${count.toLocaleString()} ${count === 1 ? 'picture' : 'pictures'}`;
}

/** The mark between a count of files and their size: a MIDDLE DOT, spaced. */
export const SIZE_SEPARATOR = ' \u00b7 ';

/** A row that may say how big its files are: the server sums `size_bytes` over the row's files. */
export interface Sized {
	size_bytes?: number | null;
}

export function sizeOf(row: object): number | null {
	const said = (row as Sized).size_bytes;
	return typeof said === 'number' ? said : null;
}

/** A count with the size of what it counts beside it: "1,867 files \u00b7 23 GB". */
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
