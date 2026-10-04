// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Privacy pane's words, and what somebody can type to find what the registry does not describe.
 *
 * The rest of the pane's words are still written in its markup, and its registered rows (how long a
 * sign-in lasts, what Hidden hides, the locks, how long search history is kept) are the registry's
 * copy, which feeds the search by itself. The clean-up that deletes old search history runs in the
 * background and is set nowhere, so nothing here names it. */
import type { Searchable } from './search';

export const COPY = {
	lede: 'How long you stay signed in, what Hidden hides, and when Sift locks.',
	/** The block holding "Hide my profile folder in paths": how a file's full location reads. */
	locations: {
		heading: 'File locations'
	},
	searchHistory: {
		heading: 'Search history'
	},
	/* Your own history of use: the switch that pauses it is the registry's row; clearing it is a
	   press, behind a confirm naming what goes and what stays. */
	yourHistory: {
		heading: 'Your history',
		clear: {
			label: 'Clear your history',
			help: "Deletes Sift's record of what you've watched, the pages you've opened and what you've searched for. Your files, ratings, tags and saved searches stay.",
			action: 'Clear',
			link: 'Clear your history',
			title: 'Clear your history?',
			consequence:
				"Sift deletes its record of what you've watched, the pages you've opened and what you've searched for. Insights counts again from what is left. This can't be undone.",
			confirm: 'Clear history',
			done: 'Your history is cleared.',
			failed: "Your history couldn't be cleared. Try again."
		}
	},
	/* A pointer, not a list: what left this device is Activity's History filtered to saves, which
	   keeps every save and says who saved what. Beside the switch that decides who may save. */
	saved: {
		label: 'Copies saved to a device',
		help: 'Every copy saved to a device is listed in Tasks and Activity, under App History.',
		link: 'Show the copies saved to a device',
		open: 'Open'
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: 'Sign-in',
		section: 'privacy',
		keywords: 'sign in session how long stay signed in login'
	},
	{
		name: 'Hidden',
		section: 'privacy',
		keywords: 'hidden vault private hide pin'
	},
	{
		name: 'Auto-lock',
		section: 'privacy',
		keywords: 'auto lock lock hidden idle away timeout'
	},
	{
		name: 'Sift lock',
		section: 'privacy',
		keywords: 'lock sift pin password shut'
	},
	{
		name: 'Save to device',
		section: 'privacy',
		keywords: 'save to device download copy phone strip location'
	},
	{
		name: COPY.saved.label,
		key: 'privacy.saved',
		section: 'privacy',
		keywords: 'copies saved device downloads record'
	},
	{
		name: COPY.locations.heading,
		section: 'privacy',
		keywords: 'file locations paths profile folder hide'
	},
	{
		name: COPY.searchHistory.heading,
		key: 'privacy.search_history',
		section: 'privacy',
		keywords: 'search history recent searches forget clear keep'
	},
	{
		name: COPY.yourHistory.heading,
		key: 'privacy.your_history',
		section: 'privacy',
		help: COPY.yourHistory.clear.help,
		keywords: 'history watched viewed pages visits pause stop recording insights'
	},
	{
		name: COPY.yourHistory.clear.label,
		key: 'privacy.clear_history',
		section: 'privacy',
		help: COPY.yourHistory.clear.help,
		keywords: 'clear delete erase forget history watched pages searches insights'
	}
];
