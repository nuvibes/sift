// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Get to know Sift pane's words, and what somebody can type to find it.
 *
 * The paths and their goals carry the server's own words (see `your-path.ts`); this adds the one
 * sentence that says what the section is for. */
import type { Searchable } from './search';

export const COPY = {
	lede: 'Short paths that show you what Sift can do, one goal at a time. What you have done is ticked as you go.'
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: 'Learning paths',
		section: 'get-to-know',
		help: COPY.lede,
		keywords:
			'getting started learning paths goals achievements learn tour tips first steps tutorial progress'
	}
];
