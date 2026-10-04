// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Importing pane's words, and what somebody can type to find them.
 *
 * ONE COPY MODULE PER PANE. `Importing.svelte` draws every word it adds from `COPY`, and the search
 * entries are built from the same objects, so a stage cannot be called one thing on the screen and
 * another in a search result. The switches on each stage's Edit page are registered settings: the registry is their
 * copy and feeds the search by itself. What is declared here is the three stages themselves, so a
 * search for what a stage DOES ("rescan", "thumbnails", "faces") lands on the stage rather than on
 * one of its rows.
 *
 * Nothing on this pane starts work or chooses when it runs: both are on Tasks, and a stage's row
 * says its When as a link there. A stage's sentence is its task's own line, the one Tasks draws, so
 * it is not written here: a search entry carries only the words it is found by. */
import { counted } from '$lib/entity/entity-counts';
import { COPY as PERFORMANCE, MEASURE_ANCHOR } from './Performance.search';
import type { Searchable } from './search';

export const COPY = {
	/* The benchmark not run yet, and the row it runs from, drawn as a settings link after the
	   sentence. The breadcrumb is built from the Performance
	   pane's own words, so a renamed row renames the link. */
	notMeasured: {
		said: "This device hasn't been benchmarked yet. Adding your first folder benchmarks it, and so does the first run if it still hasn't been. It takes a few minutes, so Sift can make the best use of this device. You can run it from",
		link: `Settings > Performance > ${PERFORMANCE.measure.row}`,
		section: 'performance',
		setting: MEASURE_ANCHOR
	},
	edit: 'Edit',
	/** When a stage runs, said before the link to Tasks where it is chosen: "As files arrive,
	 *  chosen on Tasks". */
	chosenOn: (when: string) => `${when}, chosen on`,
	/** An Edit button's accessible name: which stage it opens, so the two are not both "Edit". */
	editStage: (stage: string) => `Edit ${stage}`,
	scan: {
		heading: 'Scan',
		pageTitle: 'Scan settings',
		nothingWaiting: 'Nothing waiting to be scanned',
		waiting: (n: string) => `${n} waiting to be scanned`
	},
	/**
	 * The head of a line of files a product gave up on: "24 files", drawn as a link to them (the
	 * Files wall filtered `left_out:<product>`, each file saying why under its tile). The rest of
	 * the sentence is the stage's `cannot`, so the two read as one: "24 files couldn't have
	 * thumbnails generated and are left out."
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
		key: 'importing.scan-stage',
		section: 'importing',
		keywords:
			'scan rescan refresh reindex find new changed removed files size length type missing import again folders'
	},
	{
		name: COPY.generate.heading,
		key: 'importing.generate-stage',
		section: 'importing',
		keywords: 'generate thumbnails previews sprites scrubber fingerprints missing build make'
	},
	{
		name: COPY.identify.heading,
		key: 'importing.identify-stage',
		section: 'importing',
		keywords: 'identify faces recognize describe smart search watermarks look inside'
	},
	{
		name: COPY.scan.pageTitle,
		key: 'importing.scan-stage',
		section: 'importing',
		keywords: 'scan settings edit'
	},
	{
		name: COPY.generate.pageTitle,
		key: 'importing.generate-stage',
		section: 'importing',
		keywords: 'generate settings edit'
	},
	{
		name: COPY.identify.pageTitle,
		key: 'importing.identify-stage',
		section: 'importing',
		keywords: 'identify settings edit'
	}
];
