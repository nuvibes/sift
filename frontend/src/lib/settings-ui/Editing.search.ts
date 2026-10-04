// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Editing pane's words.
 *
 * ONE COPY MODULE PER PANE. `Editing.svelte` draws every word it adds from `COPY`. Every row on the
 * pane is a registered setting and reaches the search by itself, so nothing is declared below; the
 * two confirmations are on General and are declared there.
 */
import type { Searchable } from './search';

export const COPY = {
	cannotLoad: "Couldn't load these settings. Refresh the page to try again.",
	gifs: 'GIFs',
	compression: 'Compression',
	compressionHelp:
		"The size Sift aims for when you compress a file. Each one starts at one of Discord's upload limits, and you can change it."
} as const;

export const SEARCHABLE: Searchable[] = [
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.gifs,
		section: 'editing',
		keywords: 'gif gifs animated make clip loop'
	},
	{
		name: COPY.compression,
		section: 'editing',
		keywords: 'compress compression smaller file size quality encode'
	}
];
