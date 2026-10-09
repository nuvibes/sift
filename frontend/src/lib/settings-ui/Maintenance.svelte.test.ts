/* The groups on Maintenance, drawn. */
import { flushSync, mount, tick, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import Maintenance from './Maintenance.svelte';
import { words as wordsOn } from '$lib/design/testing.svelte';

const ran = vi.hoisted(() => ({ optimized: null as { freed: number; now: number } | null }));

vi.mock('./tidy.svelte', () => ({
	Tidy: class {
		loading = false;
		loaded = true;
		anythingToDo = true;
		lastRun = null;
		lastSurveyed = null;
		surveying = false;
		busy = false;
		problem = null;
		optimized = ran.optimized;
		rebuildable = 3;
		restyleable = 0;
		rebuilding = null;
		restyling = false;
		worthDoing = [
			{
				name: 'orphans',
				title: 'Orphaned thumbnails',
				detail: 'Left behind.',
				noun: 'file',
				nouns: 'files',
				count: 4
			},
			{
				name: 'stale',
				title: 'Stale previews',
				noun: 'file',
				nouns: 'files',
				detail:
					'Out of date. Nothing reads them. Sift made them for an older setting. Removing them changes nothing.',
				count: 0
			}
		];
		load = async () => {};
		survey = async () => {};
	}
}));

vi.mock('$lib/jobs/tasks.svelte', () => ({
	taskList: {
		row: () => undefined,
		pressing: {},
		failed: false,
		ensure: async () => {},
		setWhen: async () => {}
	},
	pressTask: vi.fn(async () => true)
}));

vi.mock('$lib/settings-ui/settings-view', () => ({ showSettingsSection: vi.fn() }));

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: vi.fn(async () => [
		{
			name: 'Maintenance',
			settings: [
				{
					key: 'quarantine.keep_days',
					value: 0,
					default: 0,
					label: 'Delete quarantined files after',
					help: "Files Sift couldn't import are kept in a folder of their own, so you can review them.",
					minimum: 0,
					maximum: 3650,
					unit: 'days'
				}
			]
		}
	]),
	saveSettings: vi.fn()
}));

let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	ran.optimized = null;
	document.body.innerHTML = '';
});

/** Each group on the page, as its heading (or none) and the names of the rows it holds. */
async function groups(): Promise<{ heading: string | null; rows: string[] }[]> {
	drawn = mount(Maintenance, { target: document.body });
	flushSync();
	await tick();
	flushSync();
	return [...document.querySelectorAll('section.group')].map((group) => ({
		heading: group.querySelector(':scope > .section-heading')?.textContent?.trim() ?? null,
		rows: [...group.querySelectorAll('.row .name')].map((name) => name.textContent?.trim() ?? '')
	}));
}

describe('the groups on Maintenance', () => {
	it('keeps the count row and the tidyings it counts in one group', async () => {
		const cleanup = (await groups()).find((group) => group.heading === 'Cleanup');
		expect(cleanup?.rows).toEqual([
			'Count thumbnails and previews on disk',
			'Orphaned thumbnails',
			'Stale previews'
		]);
	});

	it('opens with the acts that delete nothing, and gives every later group a heading', async () => {
		const drawnGroups = await groups();
		expect(drawnGroups[0]).toEqual({
			heading: null,
			rows: [
				'Optimize the database',
				'Generate all thumbnails again',
				'Generate all hover previews again'
			]
		});
		expect(drawnGroups.slice(1).map((group) => group.heading)).not.toContain(null);
	});
});

describe('the Optimize row', () => {
	const note = async () => {
		await groups();
		const row = [...document.querySelectorAll('.row')].find(
			(one) => one.querySelector('.name')?.textContent?.trim() === 'Optimize the database'
		);
		return row?.querySelector('.note')?.textContent?.trim();
	};

	it('says what the database is now when a run freed nothing, a grown file included', async () => {
		ran.optimized = { freed: 0, now: 1_062_264_784 };
		expect(await note()).toMatch(/^Nothing freed\. The database is now 1\.\d+ GB\.$/);
	});

	it('says what was freed and what the database is now', async () => {
		ran.optimized = { freed: 59_000_000, now: 1_003_000_000 };
		expect(await note()).toMatch(
			/^Freed \d+(\.\d+)? MB\. The database is now \d+(\.\d+)? (MB|GB)\.$/
		);
	});
});

describe('the count row', () => {
	it('says when it last counted on its foot line, never stacked over its button', async () => {
		await groups();
		const row = document.querySelector<HTMLElement>('[id="maintenance.survey"]');
		expect(row?.querySelector('.foot')?.textContent?.trim()).toBe('Not counted yet');
		expect(row?.querySelector('.control .note'), 'the figure is back beside the button').toBeNull();
	});
});

describe('the rows that delete', () => {
	it('say how many as their value, and offer Delete, disabled at none', async () => {
		/* The count is the row's value and the verb is always the verb; a disabled button worded
		   as a sentence beside "None" would say the same thing twice. */
		await groups();
		const rows = [...document.querySelectorAll<HTMLElement>('.row')];
		const stale = rows.find((one) => one.querySelector('.name')?.textContent === 'Stale previews');
		const orphans = rows.find(
			(one) => one.querySelector('.name')?.textContent === 'Orphaned thumbnails'
		);
		expect(stale?.querySelector('.note')?.textContent).toBe('None');
		// The count says what it counts, in the noun its tidying declares.
		expect(orphans?.querySelector('.note')?.textContent).toBe('4 files');
		expect(wordsOn(stale?.querySelector('button'))).toBe('Delete');
		expect(stale?.querySelector('button')?.disabled).toBe(true);
		expect(orphans?.querySelector('button')?.disabled).toBe(false);
		expect(document.body.textContent).not.toContain('Nothing to delete');
	});

	it('keep a long description to two sentences, the rest under More about this', async () => {
		await groups();
		const stale = [...document.querySelectorAll<HTMLElement>('.row')].find(
			(one) => one.querySelector('.name')?.textContent === 'Stale previews'
		);
		expect(stale?.querySelector('.help')?.textContent).toBe('Out of date. Nothing reads them.');
		const more = stale?.querySelector('details');
		expect(more?.querySelector('summary')?.textContent).toBe('More about this');
		expect(more?.textContent).toContain('Sift made them for an older setting. Removing them');
	});

	it('keeps how long quarantined files are kept, and no When for the sweep', async () => {
		/* The sweep is upkeep nobody times; the number of days is all a person sets about it. */
		await groups();
		await vi.waitFor(() =>
			expect(document.querySelector('[id="quarantine.keep_days"]')).not.toBeNull()
		);
		expect(document.querySelector('[id="tasks.quarantine-prune.when"]')).toBeNull();
	});

	it('says what quarantine is once, and ends on its own rows', async () => {
		/* The row's help says what the folder is; the group's sentence says what to do about it,
		   never the same sentence twice. */
		await groups();
		const row = await vi.waitFor(() => {
			const found = document.querySelector<HTMLElement>('[id="quarantine.keep_days"]');
			expect(found).not.toBeNull();
			return found!;
		});
		const group = row.closest('section.group');
		const lede = group?.querySelector('.lede')?.textContent ?? '';
		expect(row.textContent).toContain('kept in a folder of their own');
		expect(lede).not.toContain('kept in a folder of their own');
		expect(document.body.textContent).not.toContain('Every copy saved to a device');
	});
});
