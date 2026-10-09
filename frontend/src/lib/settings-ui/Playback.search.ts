// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Playback pane's words, and what somebody can find on it that is not a registry setting. */
import type { Searchable } from './search';

export const COPY = {
	cannotLoad: "These settings couldn't be loaded. Reload the page to try again.",
	/* Theater's group. What a wall STARTS as; every cell can be changed on the wall itself. */
	theater: 'Theater',
	popout: 'Popout',
	/* The drawer's Screenshot press. Both rows' words are the registry's; this is the group's. */
	screenshots: 'Screenshots',
	/* Why a search or a link lands on no row. The folder a saved screenshot goes to is an admin's
	   choice, because it adds a file to the library; the three the player keeps are set by using it. */
	folderIsAdmins: 'Only an admin chooses a library folder for screenshots. Yours are downloaded.',
	onThePlayer: (label: string) =>
		`\u201c${label}\u201d is set on the player itself: it remembers what you last chose while watching.`,
	leaveToMini: {
		name: 'Keep playing when you leave the popout',
		help: 'When on, following a link out of the popout keeps the video playing in the mini player. When off, it stops, as closing the popout does. Pressing Escape or clicking outside the popout always stops it.'
	},
	/* The phone as a remote. A fact about THIS browser, remembered in it, so it is not a registry
	   setting: the same person may want the laptop on the sofa driven and the desk's tab left alone. */
	remote: 'Remote',
	thisBrowser: {
		name: 'Let your phone control this browser',
		help: 'When on, a video or Theater wall open in this browser shows up on the Remote tab of a phone signed in as you. The phone can pause it, play the next file and change the volume. This browser remembers the choice; other browsers keep their own.',
		helpApp:
			"Sift's app always shows up on the Remote tab of a phone signed in as you. The phone can pause what it plays, play the next file and change the volume."
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.leaveToMini.name,
		key: 'playback.popout.leave_to_mini',
		section: 'playback',
		help: COPY.leaveToMini.help,
		keywords: 'popout mini player picture in picture link navigate keep playing'
	},
	{
		name: COPY.thisBrowser.name,
		key: 'playback.remote.this_browser',
		section: 'playback',
		help: COPY.thisBrowser.help,
		keywords: 'remote phone control cast pair tab browser laptop'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.popout,
		section: 'playback',
		keywords: 'popout window player mini'
	},
	{
		name: COPY.remote,
		section: 'playback',
		keywords: 'remote phone control cast'
	}
];
