// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Downloads pane's words, and what somebody can type to find them. */
import type { Searchable } from './search';

export const COPY = {
	/* The screen a row drawn there is sent on to: the rail's own word for it. */
	downloadsScreen: 'Downloads',
	groups: {
		downloading: 'What gets downloaded',
		limits: 'Limits',
		/* The message and the sound a finish makes: two settings, one moment. */
		finished: 'When a download finishes'
	},
	more: {
		label: 'More settings',
		title: 'More settings',
		help: 'How many downloads run at the same time, the speed limit and the wait between requests. It also sets retries, the wait after a rate limit, and how long Sift waits on a download that stopped responding.',
		open: 'Edit'
	},
	cannotLoad: "Couldn't load these settings. The rest of the screen still works.",
	notSaved: "Couldn't save that"
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.more.label,
		key: 'downloads.more',
		section: 'downloads',
		help: COPY.more.help,
		keywords:
			'at the same time speed limit bandwidth pace wait between requests timeout connection timeout stopped responding stalled retries rate limit back off advanced expert'
	}
];
