// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Downloads pane's words, and what somebody can type to find them.
 *
 * ONE COPY MODULE PER PANE. The pane is `DownloadsSection.svelte` and the part it draws that has
 * no module of its own: `Downloads.svelte` (the registered rows, in groups, and the More
 * settings page), and it draws the words it adds from `COPY` here, so the search entries below
 * cannot say something the pane does not. The naming template carries its own module, being its
 * own block with its own entry. The registered rows are the registry's copy and feed the search
 * by themselves.
 *
 * What is not on this pane keeps its words where it is drawn: where saved files go is
 * `StorageFolders.search.ts` (Folders), the download tools `DownloadTools.search.ts` (Updates),
 * the Sites Sift knows `SupportedSites.search.ts` (Sites). Settings, Sites draws the tunnels and
 * which Site takes which.
 */
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
