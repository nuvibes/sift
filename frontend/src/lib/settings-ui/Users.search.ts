// SPDX-License-Identifier: AGPL-3.0-or-later
/* What somebody can find on the Users pane, declared beside the pane that draws it.
 *
 * None of this is in the settings registry: a user is a row in a table, not a preference, so
 * nothing about it reaches the search index by itself. See `search.ts` for why these live next to
 * the component rather than in one list somewhere else. */
import type { Searchable } from './search';

/** The list of guests, by the words on its heading and in a search result alike. */
export const GUESTS = {
	name: 'Guests',
	help: 'Create a user for someone else and choose what they can see.'
};

/** The one-press guest, by the words on its row and in a search result alike. */
export const RANDOM_GUEST = {
	name: 'Create a random guest',
	help: 'Sift picks a username and password. The password appears only once, so write it down.'
};

export const SEARCHABLE: Searchable[] = [
	{
		...GUESTS,
		key: 'users.guests',
		section: 'users',
		// Nobody looking for this types "guest" first. They type the thing they are trying to do.
		keywords: 'add user invite share people who can sign in new user permissions'
	},
	{
		...RANDOM_GUEST,
		key: 'users.invent',
		section: 'users',
		keywords: 'generate random password create quick invent'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: 'Your account',
		section: 'users',
		keywords: 'your account admin me'
	}
];
