// SPDX-License-Identifier: AGPL-3.0-or-later
/* The words of Tasks and Activity for its four tabs and for the link each bar now is. The tabs'
 * contents bring their own: Tasks' are `settings-ui/ScheduledTasks.search.ts`, App History's
 * `settings-ui/Ledger.search.ts`, and the Logs tab's `settings-ui/Logs.search.ts`.
 *
 * ONE COPY MODULE PER PANE. `JobsScreen.svelte` draws the words it adds from `COPY`. What a person
 * can type to find each tab is declared by the module of what the tab draws, so no entry is here. */

import { usingShareOf } from '$lib/components/shell/full-amount';

export const COPY = {
	tabs: { tasks: 'Tasks', now: 'Activity', history: 'App History', log: 'Logs' },
	tabsLabel: 'Tasks and what Sift has been doing',
	/* A bar's link to its task's row on the Tasks tab, where it is run and when it runs is chosen. */
	runInTasks: 'Run in Tasks',
	/* The heads of the Activity tab's columns, drawn once over both groups. */
	columns: { status: 'Status', left: 'Time left', progress: 'Progress', done: 'Done' },
	/* The group headings inside the one list. The first is the tasks that work over every file in
	   the library, in the vocabulary's word "task". The second is every task that is not a library
	   task, named the way the Tasks tab names its own second group. */
	groups: { passes: 'Library tasks', housekeeping: 'Other tasks' },
	/* The one list's name, for a screen reader. */
	summaryLabel: 'Library tasks and other tasks',
	/* The list of tasks under the two groups, one row each: its heading, and the narrowing by kind
	   that History's Type is the model for. Which state is the strip under it. */
	list: {
		heading: 'All tasks',
		type: { label: 'Type', help: 'Show only one type of task.' },
		everything: 'Everything',
		/* The one choice for every kind this version of Sift no longer runs: rows an older release
		   left behind, which have no name of their own to offer. */
		older: 'Older tasks',
		noneOfType: 'No tasks of this type.',
		noneOfTypeInState: 'No tasks of this type in this state.'
	},
	/* Beside the running count while background work uses a share of this device because somebody
	   is using it (Settings > Performance): the sidebar leaf's words. */
	steppingBack: usingShareOf,
	/* More of a family's steps than the first page held. */
	moreSteps: 'Show more steps',
	/* A task that failed: why, on the hover of what says so, which opens the failed ones in the
	   list below. */
	failedWhy: (why: string) =>
		`Why it failed: ${why.replace(/\.$/, '')}. Press to see it with the other failed tasks.`
} as const;
