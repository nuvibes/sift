/*
 * The one order a picker's alphabetical list is in, where the picker holds the rows itself.
 *
 * Its own module rather than a corner of `$lib/search/frequent`, because the question is not about memory:
 * "where does this name file" is asked by any list that sorts names on the client, and a copy of
 * the rule in each would drift from the server's the first time one of them was touched.
 */

/**
 * The key a name is put in order by: the client's copy of the server's `kernel.sorting.sort_key`.
 *
 * Compatibility-decomposed, combining marks removed, lower-cased, whitespace collapsed, so an
 * accented name files with its unaccented spelling and "beach" sits between "BBQ" and "Bicycle",
 * exactly where the walls and the pickers' server pages put it. Compared by code point afterwards,
 * which is what SQLite does with the stored key, and NOT `localeCompare`: a second rule here that
 * disagreed with the server's would make a sheet and a flyout over one list order it differently.
 *
 * Two small differences from Python's are known and accepted: `toLowerCase` leaves the German sharp
 * s where `casefold` writes "ss", and a JavaScript string compares in UTF-16 units where SQLite
 * compares UTF-8 bytes, which differ only between a character above U+FFFF and one near the top of
 * the basic plane. Neither is a name a library is likely to hold.
 */
export function nameKey(text: string): string {
	return text
		.normalize('NFKD')
		.replace(/\p{M}/gu, '')
		.toLowerCase()
		.split(/\s+/)
		.filter((word) => word !== '')
		.join(' ');
}

/** Alphabetical by `nameKey`, the raw name settling two spellings of one key. The one comparator
 *  every picker's alphabetical tail is ordered by where it holds the rows itself. */
export function byName(one: { name: string }, other: { name: string }): number {
	const left = nameKey(one.name);
	const right = nameKey(other.name);
	if (left !== right) return left < right ? -1 : 1;
	return one.name < other.name ? -1 : one.name > other.name ? 1 : 0;
}
