// SPDX-License-Identifier: AGPL-3.0-or-later
/* Import tasks' words, and what somebody can type to find them.
 *
 * ONE COPY MODULE PER PANE. `Importing.svelte` draws every word it adds from `COPY`, and the search
 * entries are built from the same objects. The switches on each stage's Edit page are registered
 * settings, which feed the search by themselves. What is declared here is the three stages, so a
 * search for what a stage DOES ("rescan", "thumbnails", "faces") lands on the stage's row, and each
 * stage's Edit. */
import { counted } from '$lib/entity/entity-counts';
import { COPY as PERFORMANCE, MEASURE_ANCHOR } from './Performance.search';
import type { Searchable } from './search';

export const COPY = {
	/* The benchmark not run yet, and the row it runs from, drawn as a settings link after the
	   sentence. The breadcrumb is built from the Performance
	   pane's own words, so a renamed row renames the link. */
	notMeasured: {
		said: "This device hasn't been benchmarked yet. Adding your first folder benchmarks it, and so does the first run if it still hasn't been. It takes up to 5 minutes, so Sift can make the best use of this device. You can run it from",
		link: `Settings > Performance > ${PERFORMANCE.measure.row}`,
		section: 'performance',
		setting: MEASURE_ANCHOR
	},
	edit: 'Edit',
	/** An Edit button's accessible name: which stage it opens, so the two are not both "Edit". */
	editStage: (stage: string) => `Edit ${stage}`,
	scan: {
		heading: 'Scan',
		pageTitle: 'Scan settings',
		nothingWaiting: 'Nothing waiting to be scanned',
		waiting: (n: string) => `${n} waiting to be scanned`
	},
	/**
	 * The head of a line of files a product gave up on: "24 files", the press that lists them, each
	 * with why. The rest of the sentence is the stage's `cannot`, so the two read as one: "24 files
	 * couldn't have thumbnails generated and are left out."
	 */
	leftOut: (n: number, one: boolean) => `${counted(n)} ${one ? 'file' : 'files'}`,
	generate: {
		heading: 'Generate',
		pageTitle: 'Generate settings',
		cannot: (one: boolean, what: string) =>
			`couldn't have ${what} generated and ${one ? 'is' : 'are'} left out.`
	},
	identify: {
		heading: 'Identify',
		pageTitle: 'Identify settings',
		cannot: (one: boolean, what: string) =>
			`couldn't be checked for ${what} and ${one ? 'is' : 'are'} left out.`
	},
	/* The page a left-out count opens: each file, and why. */
	leftOutPage: (what: string) => `Left out: ${what}`,
	leftOutDoor: 'Left out',
	leftOutAll: (n: number) => `Show all ${counted(n)} in Browse`,
	leftOutCannotLoad: "Couldn't load the files that were left out.",
	aFile: 'A file',
	noReason: 'Sift gave up on it.',
	tryAgain: 'Try again',
	tryingAgain: 'Trying again\u2026',
	nothingMissing: 'Nothing missing',
	withTasks: (n: number) => ` with ${counted(n)} tasks at the same time`,
	about: (parts: string, about: string, assuming: string) => `${parts}, ${about}${assuming}`,
	/* A count whose time cannot be told yet: the words of `sayWindow`, and what they are about. */
	untimed: (parts: string, said: string) => `${parts}. ${said} how long that takes.`
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.scan.heading,
		key: 'tasks.scan.when',
		section: 'tasks',
		keywords:
			'scan rescan refresh reindex find new changed removed files size length type missing import again folders'
	},
	{
		name: COPY.generate.heading,
		key: 'tasks.generate.when',
		section: 'tasks',
		keywords: 'generate thumbnails previews sprites scrubber fingerprints missing build make'
	},
	{
		name: COPY.identify.heading,
		key: 'tasks.identify.when',
		section: 'tasks',
		keywords: 'identify faces recognize describe smart search watermarks look inside'
	},
	{
		name: COPY.scan.pageTitle,
		key: 'importing.scan-settings',
		section: 'tasks',
		keywords: 'scan settings edit'
	},
	{
		name: COPY.generate.pageTitle,
		key: 'importing.generate-settings',
		section: 'tasks',
		keywords: 'generate settings edit'
	},
	{
		name: COPY.identify.pageTitle,
		key: 'importing.identify-settings',
		section: 'tasks',
		keywords: 'identify settings edit'
	}
];
