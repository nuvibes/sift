// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Maintenance pane's words, and what somebody can type to find them.
 *
 * ONE COPY MODULE PER PANE. `Maintenance.svelte` draws every word it adds from `COPY`, and the
 * search entries are built from the same objects, so a button cannot be called one thing on the
 * screen and another in a search result. None of the pane's controls is a preference (each is a
 * task you press), so nothing here reaches the index on its own.
 *
 * Nothing on this pane scans, so "Rescan the library" is not declared here: scanning is
 * Importing's, and its entry is there.
 */
import { counted, filesSaid } from '$lib/entity/entity-counts';
import type { Searchable } from './search';

export const COPY = {
	/* Where the duplicate settings filed under this pane are drawn: Organize's Near duplicates card. */
	duplicatesScreen: 'Near duplicates',
	cleanup: {
		name: 'Cleanup',
		help: 'Records and files left behind by things you have already done. Each row says what it would delete before you choose Delete, and every delete is permanent.'
	},
	survey: {
		name: 'Count thumbnails and previews on disk',
		help: 'Two of the counts below read every thumbnail and preview on disk, so they run only when you choose Count now.',
		action: 'Count now',
		counting: 'Counting',
		never: 'Not counted yet',
		counted: (when: string) => `Counted ${when}`
	},
	cannotCheck: "Couldn't check for leftover files. Refresh the page to try again.",
	nothing: 'Nothing to delete.',
	/* Each in the noun its tidying declares for what it counts (`howMany`), never a word that
	   stands for any of them: a run that deleted thumbnails says thumbnails. */
	deleted: (n: number, noun: string, nouns: string) =>
		`${counted(n)} ${n === 1 ? `${noun} was` : `${nouns} were`} deleted.`,
	deletedFrom: (n: number, noun: string, nouns: string) => `${n === 1 ? noun : nouns} deleted from`,
	none: 'None',
	/** "134 jobs": the count with the noun its tidying declares for what it counts. */
	howMany: (n: number, noun: string, nouns: string) => `${counted(n)} ${n === 1 ? noun : nouns}`,
	toFree: (said: string, bytes: string) => `${said} \u2014 ${bytes} to free`,
	delete: 'Delete',
	/** A Delete's accessible name, so a list of them is not a list of the same word. */
	deleteWhat: (what: string) => `Delete ${what.toLocaleLowerCase()}`,
	optimize: {
		name: 'Optimize the database',
		help: 'Frees disk space and can speed up search and slow screens. Nothing in your library changes. Rebuilding the search index can leave the database a little larger, and Sift reuses that room as it writes.',
		freed: (bytes: string, now: string) => `Freed ${bytes}. The database is now ${now}.`,
		nothingFreed: (now: string) => `Nothing freed. The database is now ${now}.`,
		action: 'Optimize'
	},
	rebuild: {
		name: 'Generate all thumbnails again',
		help: "Thumbnails are generated once, when a file is imported, so files imported before a size change keep the old ones. Scanning again doesn't fix that; this does.",
		action: 'Generate',
		queued: (n: number) => `${counted(n)} queued`,
		title: 'Generate all thumbnails again?',
		consequence: (n: number) =>
			`Sift generates the thumbnails for ${filesSaid(n)} again in the background. Nothing is deleted and nothing in your library changes. On a large library this takes a long time; you can follow or cancel it in Activity.`
	},
	restyle: {
		name: 'Generate all hover previews again',
		help: 'A change to hover preview length reaches only files imported afterwards. This brings the rest of the library up to date, and each preview keeps playing until its replacement is ready.',
		action: 'Generate',
		generating: 'Generating',
		upToDate: 'All up to date',
		title: 'Generate all hover previews again?',
		consequence: (n: number) =>
			`Sift generates the hover preview for ${filesSaid(n)} again in the background. Nothing is deleted and nothing in your library changes, and each preview keeps playing until its replacement is ready. You can follow or cancel it in Activity.`
	},
	cannotCount: "Couldn't count",
	files: (n: number) => filesSaid(n),
	quarantine: {
		name: 'Quarantined files',
		help: 'Review quarantined files in Organize, where each decision is recorded and can be undone. Once you choose how many days to keep them, Sift deletes the older ones by itself, once a day during quiet hours.',
		searchHelp: "How long files Sift couldn't import are kept before Sift deletes them."
	},
	tidy: {
		title: (what: string) => `Delete ${what}?`,
		consequence: (n: number, noun: string, nouns: string) =>
			`${counted(n)} ${n === 1 ? noun : nouns} ${n === 1 ? 'is' : 'are'} deleted permanently.`,
		frees: (bytes: string) => ` This frees ${bytes}.`,
		confirm: 'Delete permanently'
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.rebuild.name,
		key: 'maintenance.rebuild',
		section: 'maintenance',
		help: COPY.rebuild.help,
		keywords: 'thumbnails previews sprites broken pictures regenerate rebuild cache repair'
	},
	{
		name: COPY.survey.name,
		key: 'maintenance.survey',
		section: 'maintenance',
		help: COPY.survey.help,
		keywords: 'survey count leftover thumbnails previews faces disk cache orphans'
	},
	{
		name: COPY.cleanup.name,
		key: 'maintenance.tidy',
		section: 'maintenance',
		help: COPY.cleanup.help,
		keywords: 'tidy up clean disk space temporary orphans prune vacuum reclaim delete leftovers'
	},
	{
		name: COPY.quarantine.name,
		key: 'maintenance.quarantine',
		section: 'maintenance',
		help: COPY.quarantine.searchHelp,
		keywords: 'quarantine quarantined refused files delete prune clean up keep days'
	},
	{
		name: COPY.optimize.name,
		section: 'maintenance',
		help: COPY.optimize.help,
		keywords: 'optimize settle vacuum analyze database faster slow compact'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.restyle.name,
		section: 'maintenance',
		keywords: 'hover previews generate again rebuild thumbnails animated'
	}
];
