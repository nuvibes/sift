// SPDX-License-Identifier: AGPL-3.0-or-later
/* What somebody can find on Appearance that is not a registry setting: the headings of its groups
 * and the sidebar's reset. Its rows are the registry's and feed the search by themselves. */
import type { Searchable } from './search';

export const SEARCHABLE: Searchable[] = [
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: 'Ratings',
		section: 'appearance',
		keywords: 'ratings stars'
	},
	{
		name: 'Time',
		section: 'appearance',
		keywords: 'time clock format 24 hour'
	},
	{
		name: 'Motion',
		section: 'appearance',
		keywords: 'motion animation reduce movement'
	},
	{
		name: 'Movement',
		section: 'appearance',
		keywords: 'movement animation reduce motion'
	},
	{
		name: 'People details',
		section: 'appearance',
		keywords: 'people details record height units'
	},
	{
		name: 'Sidebar',
		section: 'appearance',
		keywords: 'sidebar pages order hide rail navigation'
	},
	{
		name: 'Reset the sidebar',
		key: 'appearance.sidebar-reset',
		section: 'appearance',
		keywords: 'reset sidebar default order pages'
	}
];
