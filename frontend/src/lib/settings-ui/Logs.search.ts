// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Logs tab of Tasks and Activity: its words, and what somebody can type to find it. Its
 * registered settings reach the index from the registry on their own; what is written here is the
 * reader under them (the one list of both logs, the level, the narrowing box, the copy and the
 * download), which nothing registers.
 *
 * ONE COPY MODULE PER PANE. `Logs.svelte` (the tab) and `ApplicationLog.svelte` (the reader it draws)
 * take every word they add from `COPY`, and the search entry is built from the same object. */
import type { operations } from '$lib/api/schema';
import type { Searchable } from './search';

/* A level the log route filters by, as the server declares it. */
type ServerLevel = NonNullable<
	NonNullable<operations['recent_api_logs_get']['parameters']['query']>['level']
>;

/*
 * The levels a record can carry, quietest first: the server's `LEVELS`, checked against it.
 *
 * `satisfies` refuses a level the server does not know, and `EVERY_LEVEL` below refuses a server
 * level missing here, so the screen cannot offer a different set from the one the route filters by.
 */
export const LOG_LEVELS = [
	'debug',
	'info',
	'warning',
	'error',
	'critical'
] as const satisfies readonly ServerLevel[];

export type LogLevel = (typeof LOG_LEVELS)[number];

/* Compiles only while every server level is in the list above. */
const EVERY_LEVEL: Exclude<ServerLevel, LogLevel> extends never ? true : never = true;
void EVERY_LEVEL;

export const COPY = {
	heading: 'Logs',
	help: 'A record of what Sift did. Read it first when something goes wrong.',
	library: 'Library',
	app: 'This app',
	/* The app's own log on the computer running Sift, read from another computer through the server. */
	appThere: 'Sift app',
	bothCovers:
		"Each line says which log it's from. Library lines are what the library did: scans, downloads, tasks and requests. This app lines are what the app on this device did, such as dragging a file out, updating or moving the library.",
	bothThereCovers: (machine: string | null) =>
		`Each line says which log it's from. Library lines are what the library did: scans, downloads, tasks and requests. Sift app lines are what the Sift app on ${machine ?? 'the computer running Sift'} did, such as starting Sift, updating it or moving its data.`,
	level: 'Show',
	levelHelp: 'Each choice also shows everything more serious than it.',
	/* Python's own level names, one for each of `LOG_LEVELS`. */
	levels: {
		critical: 'Critical',
		error: 'Error',
		warning: 'Warning',
		info: 'Info',
		debug: 'Debug'
	},
	narrow: 'Filter the log',
	refresh: 'Refresh',
	copy: 'Copy for a bug report',
	copied: 'Copied',
	download: 'Download log',
	downloadTrailer: 'Download redacted log',
	fileName: (stamp: string) => `sift-log ${stamp}.txt`,
	savedTo: (folder: string) => `Saved to ${folder}`,
	savedInBrowser: "Saved to your browser's downloads.",
	cannotDownload: "Couldn't download the log. Try again.",
	blocked: 'Your browser blocked copying. Select the lines and copy them by hand.',
	cannotLoad: "Couldn't load the log.",
	emptyTitle: 'Nothing written yet',
	empty: 'The log is empty. Sift writes to it as it works.',
	noMatchTitle: 'Nothing matches',
	noMatch: 'No line in the log matches. Choose Debug or change the words in the box.',
	/* A narrowed read stops looking after the newest part of the log (see `SEARCH_BUDGET` on the
	   server), and says so rather than letting "no errors" stand for "not looked". */
	partly: (searched: string) =>
		`Only the newest ${searched} of the log was searched. Older lines were not.`,
	olderLeftOut: (log: string) =>
		`Older ${log} lines are left out, so both logs cover the same stretch of time. Filter the log to see them.`,
	appNarrowed: (app: string) => `${app} lines are filtered from the newest 500 of its log.`,
	where: (log: string, size: string, path: string) => `${log}: ${size} at ${path}`
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.heading,
		key: 'activity.log',
		section: 'tasks',
		show: 'log',
		help: COPY.help,
		keywords:
			'logs log file critical error errors warning warnings info debug troubleshoot bug report copy download redacted personal details hide what went wrong crash diagnostics level narrow search'
	}
];
