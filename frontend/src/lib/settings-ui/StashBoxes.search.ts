// SPDX-License-Identifier: AGPL-3.0-or-later
/* The stash-box list's own words, and its search entry built from the same object, so the heading on
 * the pane and the result a search finds cannot say two different things. None of this is in the
 * settings registry: a stash-box is a row in a table, not a preference. */
import type { Searchable } from './search';

/** The list of stash-boxes and their keys. */
export const STASH_BOX_KEYS = {
	name: 'Stash-box API keys',
	help: 'Add a stash-box and your API key for it, so Sift can look up your files.'
};

/**
 * The words the section writes itself: the status line and the page one level in. The registered
 * rows carry their own words.
 */
export const COPY = {
	status: {
		off: "Turned off. Sift doesn't look up your files on any stash-box.",
		allOff: 'On, but every stash-box is turned off.',
		noKey: 'On, but no stash-box has a key Sift can use.',
		ready: (count: number) =>
			`Ready. ${count.toLocaleString()} ${count === 1 ? 'stash-box is' : 'stash-boxes are'} turned on.`
	},
	more: {
		label: 'More settings',
		help: 'Which stash-box lookups use, how much two lengths may differ, whether exact matches are confirmed automatically, and what a lookup may create.',
		action: 'Edit'
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.more.label,
		key: 'stash-boxes.more',
		section: 'stash-boxes',
		help: COPY.more.help,
		keywords: 'auto-enrich using which stash-box length duration exact matches create new advanced'
	},
	{
		...STASH_BOX_KEYS,
		key: 'stash-boxes.list',
		section: 'stash-boxes',
		keywords: 'stash-boxes stashdb fansdb metadata api key endpoint identify scene lookup match'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: 'Stash-box search',
		section: 'stash-boxes',
		keywords: 'stash-box search look up match scenes'
	}
];
