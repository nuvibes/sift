// SPDX-License-Identifier: AGPL-3.0-or-later
/* The watermark pane's own words, and its search entries built from the same objects, so a row on
 * the pane and the result a search finds cannot say two different things. The switch and the device
 * are registered settings and find themselves; the feature as a whole, the page one level in and
 * deleting the results are not. */
import type { Searchable } from './search';
import { counted, filesSaid } from '$lib/entity/entity-counts';
import type { WatermarkStatus } from '$lib/library/watermarks.svelte';

/** The feature itself: the switch, where it stands, and when it runs. */
const READ_WATERMARKS = {
	name: 'Read watermarks',
	help: 'Checks your files for a Site watermark, so the Sites on your files stay up to date.'
};

/** Deleting what the scan found, so the next scan checks every file again. */
export const WATERMARK_RESULTS = {
	name: 'Watermark results',
	help: 'Delete the watermark results, so the next scan checks every file again.'
};

/**
 * The words the pane writes itself: the status line, the page one level in, and the download. The
 * registered rows carry their own words.
 */
export const COPY = {
	status: {
		off: 'Turned off.',
		offKept: 'Turned off. The watermark results are kept.',
		notReady: "On, but the models aren't downloaded yet.",
		ready: (device: string) => `Ready. Running on the ${device}.`,
		counts: (read: number, marks: number) =>
			`${read.toLocaleString()} ${read === 1 ? 'file' : 'files'} scanned, ${marks.toLocaleString()} with a watermark.`,
		waiting: (count: number, unread = 0) =>
			`At least ${count.toLocaleString()} not scanned yet${unread > 0 ? `, and ${counted(unread)} more waiting to be scanned` : ''}.`,
		unread: (count: number) => `${filesSaid(count)} waiting to be scanned.`,
		done: 'Every file is scanned.'
	},
	about: 'Sift scans a file again only if its contents change.',
	more: {
		label: 'More settings',
		help: 'What Sift runs the scan on, and a fresh copy of the models.',
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
		couldNotStart:
			"Couldn't start the download. Check that this device is connected to the internet."
	},
	forget: {
		help: 'Deletes the watermark results, so the next scan checks every file again.',
		action: 'Delete results'
	}
} as const;

/**
 * Where reading stands, in one line under its switch, shared with the Recognition switches on
 * Importing. The count still to read stops at a page, so it says "at least".
 */
export function watermarksStatusLine(
	status: WatermarkStatus | null,
	enabled: boolean,
	device: string
): string | null {
	if (status === null) return enabled ? null : COPY.status.off;
	if (!enabled) return status.read_files > 0 ? COPY.status.offKept : COPY.status.off;
	if (!status.ready) return status.problem ?? COPY.status.notReady;
	const unread = status.unread_files;
	return [
		COPY.status.ready(device),
		COPY.status.counts(status.read_files, status.marks_found),
		status.waiting_files > 0
			? COPY.status.waiting(status.waiting_files, unread)
			: unread > 0
				? COPY.status.unread(unread)
				: COPY.status.done
	].join(' ');
}

export const SEARCHABLE: Searchable[] = [
	{
		...READ_WATERMARKS,
		key: 'watermarks.status',
		section: 'watermarks',
		keywords: 'watermark onlyfans fansly logo mark ocr read text Site scan read the library run now'
	},
	{
		name: COPY.more.label,
		key: 'watermarks.more',
		section: 'watermarks',
		help: COPY.more.help,
		keywords: 'watermark device gpu cpu models download again advanced expert'
	},
	/* On the page behind More settings, filed under it: the page claims it, so it opens the page
	   and rings the row. */
	{
		name: COPY.models.again,
		key: 'watermarks.models',
		section: 'watermarks',
		page: COPY.more.label,
		help: COPY.models.againHelp,
		keywords: 'watermark models download again fresh copy damaged repair'
	},
	{
		...WATERMARK_RESULTS,
		key: 'watermarks.forget',
		section: 'watermarks',
		keywords: 'reset clear delete forget start over watermark reread'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.models.heading,
		section: 'watermarks',
		keywords: 'models watermark detection download'
	}
];
