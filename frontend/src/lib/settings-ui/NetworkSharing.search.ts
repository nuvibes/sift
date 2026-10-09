// SPDX-License-Identifier: AGPL-3.0-or-later
/* Sharing the library on the local network. It is drawn on General and it is not a plain
 * setting: the address it hands out is worked out at the moment it is switched on. */
import type { Searchable } from './search';

/** The switch, by the words on the pane and in a search result alike. */
export const NETWORK_SHARING = {
	heading: 'Network sharing',
	lede: 'Sift normally opens only on this device. Turn this on to open it from other devices on your home network too.',
	name: 'Share this library on my network',
	help: 'Let other devices on your network open Sift.',
	rowHelp:
		"Anyone on your network can then reach the sign-in screen. They still need a username and password to sign in. Connections on your home network aren't encrypted. For an encrypted connection, reach this device through a VPN into your home network.",
	/* What a person sees when it goes on, said before they press it. */
	prompts:
		'Turning this on restarts Sift, which takes a few seconds. Windows then keeps other devices out until you allow them. Sift offers to ask Windows for you, and Windows shows its own prompt asking for your permission. Sift opens only its own port, and only on private networks.'
};

export const SEARCHABLE: Searchable[] = [
	{
		...NETWORK_SHARING,
		key: 'privacy.network_sharing',
		section: 'general',
		keywords:
			'lan network address phone tablet remote access other computer device ip port firewall windows'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: NETWORK_SHARING.heading,
		section: 'general',
		keywords: 'network sharing lan phone other devices address share library'
	}
];
