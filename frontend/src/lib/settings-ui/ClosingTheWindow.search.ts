// SPDX-License-Identifier: AGPL-3.0-or-later
/* What the window's close button does. Kept by the desktop shell rather than the library, so the
 * registry could not describe it and the search list names it here. */
import type { Searchable } from './search';

/** The switch, by the words on the pane and in a search result alike. */
export const KEEP_RUNNING = {
	name: 'Keep Sift running when the window is closed',
	help: 'Closing the window leaves Sift in the notification area instead of quitting it.'
};

export const SEARCHABLE: Searchable[] = [
	{
		...KEEP_RUNNING,
		key: 'general.closing_the_window',
		section: 'general',
		keywords:
			'tray notification area close quit background minimise minimize system tray exit this device'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: 'Closing the window',
		section: 'general',
		keywords: 'close window tray keep running quit exit background'
	}
];
