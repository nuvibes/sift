// SPDX-License-Identifier: AGPL-3.0-or-later
/* What is IN a folder, as against what is UNDER it. */

/** The server refuses a depth it cannot read, so the value is spelled once, here. */
const DEPTH = 'depth';
const DIRECT = 'direct';

/** The wall's query, filtered from "everything under this folder" to "what is in it". */
export function directContents(query: Record<string, string>): Record<string, string> {
	if ((query.q ?? '').trim() !== '') return query;
	return { ...query, [DEPTH]: DIRECT };
}
