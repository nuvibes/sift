// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Tasks pane's words, and what somebody can type to find them. */
import { counted } from '$lib/entity/entity-counts';
import type { Searchable } from './search';

export const COPY = {
	lede: 'When each task runs. A task can run as files are imported or on a schedule, wait for quiet hours, or run only when you press it.',
	cannotLoad: "Couldn't load the tasks. Refresh the page to try again.",
	quiet: {
		heading: 'Quiet hours',
		help: 'A stretch of each day when this device is usually free. Every task set to run during quiet hours waits for it to begin and pauses when it ends.',
		range: (from: string, until: string) => `${from} to ${until}`,
		edit: 'Edit quiet hours',
		editShort: 'Edit',
		openNow: (until: string) => `Quiet hours are on now, until ${until}.`,
		allDay: 'Quiet hours are on all day, because they start and end at the same time.',
		nextAt: (at: string) => `Quiet hours start at ${at}.`,
		awakeNow: 'This device is being kept awake for quiet-hours tasks.'
	},
	stages: {
		heading: 'Import tasks',
		help: "The three things Sift does with each file as it's imported, in the order it does them."
	},
	others: {
		name: 'Other tasks',
		help: 'Work Sift does for the library as a whole, such as finding duplicates and creating backups.'
	},
	activity: {
		heading: 'Activity',
		help: 'What Sift is working on now, what is waiting, and how each task ended.'
	},
	other: {
		name: 'Other settings',
		help: 'Settings filed with the tasks that belong to no task above.'
	},
	/* The row, wherever it is drawn. */
	when: {
		choose: (title: string) => `When to run: ${title}`,
		run: 'Run now',
		atQuiet: 'Run during quiet hours',
		/* The press's menu: when it runs, the rarer ways to run it, and part of it. */
		more: (title: string) => `When to run ${title}, and more ways to run it`,
		whenHeading: 'When it runs',
		parts: 'Parts to run',
		folders: 'Folders to run it for',
		/* The box over the parts and folders, the one every Add to list has. */
		narrow: (title: string) => `Filter the parts of ${title}`,
		nothingMatches: 'Nothing matches.',
		runTicked: 'Run the ticked ones now',
		dryRun: 'Dry run',
		dryRunTicked: 'Dry run of the ticked ones',
		dryStarted:
			'Dry run started. It changes nothing, and its report appears on this row when it finishes.',
		/* The lead of a sentence that ends in Activity, as a link. */
		startedPart: (named: string) => `Started for ${named}. Follow it in`,
		lastDry: (when: string) => `Dry run ${when}`,
		/* A dry run going now, on the In progress chip: the chip's word, saying which run. */
		dryRunning: 'Dry run in progress',
		dryReport: 'What the dry run found',
		lastDryFailed: (when: string) => `Dry run failed ${when}`,
		mixed: 'Mixed',
		off: (title: string) => `${title} is off.`,
		turnOn: 'Turn it on under',
		lastRan: (when: string) => `Last ran ${when}`,
		lastFailed: (when: string) => `Last run failed ${when}`,
		lastCanceled: (when: string) => `Last run canceled ${when}`,
		next: (when: string) => `Next ${when}`,
		/* The count says what it counts: "3 files waiting", "1 run waiting". */
		waiting: (n: number, unit: string) => `${counted(n)} ${unit} waiting`,
		held: (n: number, unit: string) => `${counted(n)} ${unit} waiting for quiet hours`,
		said: (exact: string, said: string) => `${exact}. ${said}`,
		started: 'Started. Follow it in',
		/* The upkeep Activity does not list (the backup): its run is read on its own row. */
		startedHere: 'Started. This row says how it went when it ends.',
		nothingToDo: "There's nothing to run for this task right now.",
		startsAt: (at: string) => `Queued for quiet hours. It starts at ${at}.`,
		startsNow: 'Queued for quiet hours, which are on now.',
		cannotLoad: "Couldn't load this task.",
		cannotRun: "Couldn't start the task. Check that Sift is still running."
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.quiet.heading,
		key: 'tasks.quiet-hours',
		section: 'tasks',
		keywords: 'quiet hours night pause tasks schedule'
	},
	{
		name: COPY.stages.heading,
		key: 'tasks.stages',
		section: 'tasks',
		keywords: 'import tasks scan generate identify stages'
	},
	{
		name: COPY.activity.heading,
		key: 'tasks.activity',
		section: 'tasks',
		keywords: 'activity task queue running waiting queued failed canceled done jobs progress'
	},
	{
		name: COPY.other.name,
		section: 'tasks',
		keywords: 'other settings tasks more'
	}
];
