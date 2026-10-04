// SPDX-License-Identifier: AGPL-3.0-or-later
/* Which browser a link out of Sift opens in. Absent in a browser and absent when the machine has
 * only one, so this is a control the registry could not describe. */
import type { Searchable } from './search';

/** The row, by the words on the pane and in a search result alike. */
export const LINKS_OPEN_IN = {
	name: 'Open links in',
	help: 'Which browser Sift opens a link with.'
};

export const SEARCHABLE: Searchable[] = [
	{
		...LINKS_OPEN_IN,
		key: 'general.links_open_in',
		section: 'general',
		keywords: 'browser default chrome firefox edge external link'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: 'Links',
		section: 'general',
		keywords: 'links open browser app external'
	}
];
