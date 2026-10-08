// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Insights pane's words, and what somebody can type to find it.
 *
 * The four recap switches are registry rows, whose own label and help feed the search by
 * themselves. The two rows below them point at settings that live elsewhere. */
import type { Searchable } from './search';

/** The recap switches, a period each, in the order the rows are drawn. */
export const RECAP_KEYS = [
	'insights.recap_day',
	'insights.recap_week',
	'insights.recap_month',
	'insights.recap_year'
] as const;

export const COPY = {
	lede: 'A recap is a few cards about a day, a week, a month or a year, ready once it has ended. Turned off, Sift creates no new recaps of that kind; the ones it already created stay.',
	recaps: {
		heading: 'Recaps'
	},
	history: {
		label: 'Pause or clear your history',
		help: 'Insights and recaps are counted from your history, which you can pause or clear.',
		link: 'Privacy and Security'
	},
	pictures: {
		label: 'Where saved cards go',
		help: 'A recap card saved as a picture goes to the folder screenshots are saved to.',
		link: 'Save screenshots to'
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.recaps.heading,
		section: 'insights',
		help: COPY.lede,
		keywords: 'recap recaps daily weekly monthly yearly day week month year cards insights'
	},
	{
		name: COPY.history.label,
		section: 'insights',
		help: COPY.history.help,
		keywords: 'history pause clear insights privacy'
	},
	{
		name: COPY.pictures.label,
		section: 'insights',
		help: COPY.pictures.help,
		keywords: 'save picture recap card screenshot folder'
	}
];
