// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * What is IN a folder, as against what is UNDER it.
 *
 * `in:` means a folder's whole subtree, which is right for a search and wrong for browsing: a
 * folder holding only folders would draw every grandchild's tiles. `depth=direct` beside the `in`
 * asks the server for what is in this one folder. It is a parameter, not query words or child
 * exclusions composed here, because an exclusion hides a file that also has a copy in a subfolder,
 * and words in `q` would make the wall behave as a search. A search typed inside a folder still
 * looks under it, as a file manager's does.
 */

/** The server refuses a depth it cannot read, so the value is spelled once, here. */
const DEPTH = 'depth';
const DIRECT = 'direct';

/**
 * The wall's query, filtered from "everything under this folder" to "what is in it".
 *
 * Returns the query unchanged where the filtering is deliberately not wanted (a search in
 * progress), so a caller can use it unconditionally.
 */
export function directContents(query: Record<string, string>): Record<string, string> {
	if ((query.q ?? '').trim() !== '') return query;
	return { ...query, [DEPTH]: DIRECT };
}
