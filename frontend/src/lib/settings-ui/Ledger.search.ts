// SPDX-License-Identifier: AGPL-3.0-or-later
/* The whole-install record (the App History tab of Tasks and Activity), its words, and what
 * somebody can type to find it. */
import type { Searchable } from './search';

export const COPY = {
	title: 'App History',
	all: {
		name: 'Library history',
		help: 'Everything Sift and you have done in this library, newest first. Select a name to open the thing it happened to.'
	},
	type: { label: 'Type', help: 'Show only one type of thing.' },
	action: { label: 'Action', help: 'Show only one action.' },
	/* Everything, or only the decisions: what was decided on Organize and what Sift filed by
	   itself, each with its Undo. */
	show: {
		label: 'Show',
		help: 'Everything, or only the decisions, each with its Undo.',
		everything: 'Everything',
		decisions: 'Decisions'
	},
	cannotLoad: "Couldn't load App History. Refresh the page to try again.",
	cannotLoadMore: "Couldn't load more of App History. Try again.",
	notUndone: "Couldn't undo that",
	emptyTitle: 'Nothing yet',
	empty: 'What you and Sift do appears here.',
	noMatchTitle: 'Nothing like that yet',
	noMatch: 'Nothing in App History matches. Widen one of the two filters above.',
	list: 'Library history, newest first',
	undoing: 'Undoing\u2026',
	undo: 'Undo',
	/* A folded line's Undo: every decision the press it stands for took. */
	undoAll: 'Undo all',
	/* And the question it asks first: a press nobody can see the effect of before pressing would
	   put back thousands of filings on one click. */
	undoAllAsk: (standing: number) =>
		standing === 1 ? 'Undo 1 decision?' : `Undo ${standing.toLocaleString()} decisions?`,
	undoAllSays:
		'Each one goes back to how it was before it was decided. One already undone stays as it is.',
	/* The words a decision card's own Undo all answers with, so one act reads one way. */
	undoneAll: (undone: number, of: number) =>
		undone === of ? 'All undone' : `${(of - undone).toLocaleString()} already undone`,
	/* A `ran` line's report, opened under it. */
	showReport: 'Report',
	hideReport: 'Hide report',
	copyReport: 'Copy report',
	copied: 'Copied',
	cannotReport: "Couldn't load that report. Try again.",
	blocked: 'Your browser blocked copying. Select the report and copy it by hand.',
	/* The two lists that folded into this one, as the narrowings they became. */
	saved: {
		name: 'Saved to a device',
		help: "App History, showing only the copies someone saved to their own device. It's a record of what left this device, not a way to stop it."
	},
	decisions: {
		name: 'Decisions',
		help: 'App History, showing only what was decided on Organize and what Sift filed by itself. Undo takes one back while it still can.'
	},
	runs: {
		name: 'How long tasks take',
		help: "Every task that ran over your library, from App History. Open one's report to see how many files it read, how long it took and on which device. Copy the report to share or compare it."
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.title,
		key: 'activity.history',
		section: 'tasks',
		show: 'history',
		help: COPY.all.help,
		keywords:
			'history ledger record log audit provenance what happened who did deleted removed renamed merged shared hidden enriched scanned activity timeline events'
	},
	{
		name: COPY.saved.name,
		key: 'activity.saved',
		section: 'tasks',
		show: 'history',
		help: COPY.saved.help,
		keywords: 'saved downloaded copy kept device export left audit who saved'
	},
	{
		name: COPY.decisions.name,
		key: 'activity.decisions',
		section: 'tasks',
		show: 'history',
		help: COPY.decisions.help,
		keywords:
			'decisions decided recent organize answers answered filed skipped refused undo take back review'
	},
	{
		name: COPY.runs.name,
		key: 'activity.runs',
		section: 'tasks',
		show: 'history',
		help: COPY.runs.help,
		keywords:
			'scan generate identify fingerprint smart search history benchmark speed slow import duration timing runs report copy share compare passes'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.show.label,
		section: 'tasks',
		show: 'history',
		keywords: 'show only decisions everything decided organize filter narrow'
	},
	{
		name: COPY.all.name,
		section: 'tasks',
		show: 'history',
		keywords: 'library history ledger everything that happened record changes'
	}
];
