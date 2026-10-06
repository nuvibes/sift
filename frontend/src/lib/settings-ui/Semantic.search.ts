// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Smart Search pane's own words, and its search entries built from the same objects, so a row
 * on the pane and the result a search finds cannot say two different things. The switch, the models
 * and the device are registered settings and find themselves; the page they sit on and deleting the
 * index are not. */
import type { Searchable } from './search';
import { counted } from '$lib/entity/entity-counts';
import type { SemanticStatus } from '$lib/search/semantic.svelte';

/** Deleting every description, so the next run describes every file again. */
export const DELETE_INDEX = {
	name: 'Smart Search index',
	help: 'Delete every description Sift has made. Turning Smart Search off keeps them.'
};

/**
 * The words the pane writes itself: the status line, the page one level in, and what the download
 * and a stopped run say. The registered rows carry their own words.
 */
export const COPY = {
	status: {
		off: 'Turned off.',
		offKept: 'Turned off. The descriptions Sift made are kept, so turning it back on is instant.',
		notReady: "On, but the models aren't downloaded yet.",
		ready: (device: string) => `Ready. Running on the ${device}.`,
		counts: (described: number, waiting: number, unread = 0) =>
			`${described.toLocaleString()} ${described === 1 ? 'file' : 'files'} described, ${waiting.toLocaleString()} not described yet${unread > 0 ? `, and ${counted(unread)} more waiting to be scanned` : ''}.`
	},
	more: {
		label: 'More settings',
		help: 'Which models Sift uses, what it runs them on, and a fresh copy of the models.',
		action: 'Edit'
	},
	models: {
		heading: 'Models',
		help: "Sift doesn't include the models. It downloads them once, from their publisher.",
		download: 'Download the models',
		downloading: 'Downloading\u2026',
		bar: 'Downloading the models',
		again: 'Download the models again',
		/** The press beside that row: the row already names what it downloads. */
		againPress: 'Download',
		againHelp: 'The models are on this device. Download them again only if a file is damaged.',
		size: 'The download is a few hundred megabytes. You can leave this screen and follow or cancel it in Activity. A canceled download keeps what arrived, so starting again downloads only the rest.',
		couldNotStart:
			"Couldn't start the download. Check that this device is connected to the internet."
	},
	previous: (count: number) =>
		`${count.toLocaleString()} of those were described by the previous model and are left out of Smart Search until Sift describes them again.`,
	stopped: (left: number) =>
		`${left.toLocaleString()} files were not described. Turning Smart Search off cancels the queued work, and turning it back on doesn't resume it.`,
	stoppedHow: 'To continue where it stopped, press Run now on its row in',
	stoppedWhere: 'Tasks',
	leave: 'This runs in the background, so you can leave this screen.',
	forget: {
		help: 'Deletes every description Sift made, so describing the library again opens every file again.',
		action: 'Delete index'
	}
} as const;

/**
 * Where Smart Search stands, in one line under its switch, shared with the Recognition switches on
 * Importing. `counts` is a run being followed now, fresher than the status.
 */
export function semanticStatusLine(
	status: SemanticStatus | null,
	enabled: boolean,
	device: string,
	counts?: { done: number; left: number }
): string | null {
	if (status === null) return null;
	if (!status.supported) return status.problem ?? null;
	if (!enabled) return status.indexed_frames > 0 ? COPY.status.offKept : COPY.status.off;
	if (!status.ready) return status.problem ?? COPY.status.notReady;
	return `${COPY.status.ready(device)} ${COPY.status.counts(
		counts?.done ?? status.described_files,
		counts?.left ?? status.waiting_files,
		status.unread_files
	)}`;
}

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.more.label,
		key: 'semantic.more',
		section: 'semantic',
		help: COPY.more.help,
		keywords: 'smart search models device gpu cpu download again advanced expert'
	},
	/* On the page behind More settings, filed under it: the page claims it, so it opens the page
	   and rings the row. */
	{
		name: COPY.models.again,
		key: 'semantic.models',
		section: 'semantic',
		page: COPY.more.label,
		help: COPY.models.againHelp,
		keywords: 'smart search models download again fresh copy damaged repair'
	},
	{
		...DELETE_INDEX,
		key: 'semantic.forget',
		section: 'semantic',
		keywords: 'reset clear delete index descriptions smart search start over'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.models.heading,
		section: 'semantic',
		keywords: 'models smart search download clip'
	}
];
