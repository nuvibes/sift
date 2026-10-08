// SPDX-License-Identifier: AGPL-3.0-or-later
/* The words `JobsScreen.svelte` adds to Tasks and Activity; each tab's contents bring their own. */

import { currentShare, turboModeSays } from '$lib/components/shell/turbo-mode';

export const COPY = {
	tabs: { tasks: 'Tasks', history: 'App History', log: 'Logs' },
	tabsLabel: 'Tasks and what Sift has been doing',
	/* A bar's press, its pause, resume and cancel; and the whole queue's, on Options. */
	runNow: 'Run now',
	pause: 'Pause',
	resume: 'Resume',
	cancel: 'Cancel',
	pauseAll: 'Pause all tasks',
	resumeAll: 'Resume all tasks',
	pausedAll: 'All tasks are paused: none starts until you resume them.',
	cancelPass: (title: string) => `Cancel ${title}?`,
	cancelPassSays:
		"Every task of it that hasn't finished is canceled. Work already done is kept, and you can run it again afterwards.",
	/* The heads of the Activity tab's columns, drawn once over both groups. */
	columns: { status: 'Status', left: 'Time left', progress: 'Progress', done: 'Done' },
	/* The group headings inside the one list, as the Tasks tab names its own groups. */
	groups: { passes: 'Library tasks', housekeeping: 'Other tasks' },
	/* The one list's name, for a screen reader. */
	summaryLabel: 'Library tasks and other tasks',
	/* The list under the two groups: its heading, and its narrowing by kind (History's Type). */
	list: {
		heading: 'Task Queue',
		type: { label: 'Type', help: 'Show only one type of task.' },
		everything: 'Everything',
		/* Every kind this version no longer runs, as one choice. */
		older: 'Older tasks',
		noneOfType: 'No tasks of this type.',
		noneOfTypeInState: 'No tasks of this type in this state.'
	},
	/* Beside the running count in eco mode or turbo mode: the sidebar leaf's words. */
	steppingBack: (state: 'less' | 'full') => `${turboModeSays(state, currentShare())}.`,
	/* More of a family's steps than the first page held. */
	moreSteps: 'Show more steps',
	/* Why a task failed, on the hover of what says so. */
	failedWhy: (why: string) =>
		`Why it failed: ${why.replace(/\.$/, '')}. Press to see it with the other failed tasks.`
} as const;
