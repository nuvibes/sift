/* The next file found while this one plays: what the end of a clip will step to, held with its
 * record and plan, and dropped when the order or the end rule changes. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ pushState: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/state', () => ({
	page: { state: {}, route: { id: '/browse' }, url: new URL('http://sift.test/browse'), params: {} }
}));
const get = vi.fn(async (path: string) => ({
	id: path.split('/').pop(),
	media_type: path.endsWith('p') ? 'image' : 'video',
	concealed: false
}));
vi.mock('$lib/api/client', () => ({ api: { get: (path: string) => get(path) } }));
const planFor = vi.fn(async (id: string) => ({ route: 'direct', url: `/api/assets/${id}/stream` }));
vi.mock('$lib/player/playback', () => ({ planFor: (id: string) => planFor(id) }));

import {
	dropAhead,
	lookAhead,
	openAsset,
	playOn,
	takePlan,
	takeRecord,
	toggleShuffle
} from './asset-view';
import { run } from './run.svelte';
import { libraryChanges } from '$lib/library/changes.svelte';

const CLIP = (id: string) => ({ id, runs: true });
const PHOTO = (id: string) => ({ id, runs: false });

/** Let every answer in flight land. */
async function settle(): Promise<void> {
	for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

beforeEach(() => {
	get.mockClear();
	run.shuffle = false;
	openAsset('a', [CLIP('a'), PHOTO('bp'), CLIP('c')]);
	// The press asks its own plan (`planBeside`); what follows counts the look ahead's.
	planFor.mockClear();
});

describe('a record found ahead', () => {
	it('is fresh while no bell has rung since it was asked for, and stale after one', async () => {
		lookAhead('a', { pictures: false });
		await settle();
		expect(takeRecord('c')?.stale).toBe(false);

		dropAhead();
		lookAhead('a', { pictures: false });
		await settle();
		libraryChanges.changed();
		expect(takeRecord('c')?.stale).toBe(true);
	});
});

describe('a file pressed', () => {
	it('has its plan asked beside its record, taken once', async () => {
		openAsset('c', [CLIP('a'), PHOTO('bp'), CLIP('c')]);
		expect(planFor).toHaveBeenCalledWith('c');
		expect((await takePlan('c'))?.url).toBe('/api/assets/c/stream');
		expect(takePlan('c')).toBeNull();
	});

	it('asks nothing for a still, which has no plan', () => {
		planFor.mockClear();
		openAsset('bp', [CLIP('a'), PHOTO('bp'), CLIP('c')]);
		expect(planFor).not.toHaveBeenCalled();
		expect(takePlan('bp')).toBeNull();
	});
});

describe('looking ahead from a clip that is playing', () => {
	it('holds the file the end will go to, its record and its plan, each taken once', async () => {
		lookAhead('a', { pictures: false });
		await settle();

		expect(get).toHaveBeenCalledWith('/assets/c');
		expect(await playOn('a', { pictures: false })).toBe('c');
		expect(takeRecord('c')?.record.id).toBe('c');
		expect(takeRecord('c')).toBeNull();
		expect((await takePlan('c'))?.url).toBe('/api/assets/c/stream');
		expect(takePlan('c')).toBeNull();
		expect(takeRecord('a')).toBeNull();
	});

	it('asks the end once: a second look and the end itself reuse the answer', async () => {
		lookAhead('a', { pictures: false });
		lookAhead('a', { pictures: false });
		await settle();
		await playOn('a', { pictures: false });
		expect(get).toHaveBeenCalledTimes(1);
		expect(planFor).toHaveBeenCalledTimes(1);
	});

	it('fetches no plan for a picture', async () => {
		lookAhead('a', { pictures: true });
		await settle();
		expect(await playOn('a', { pictures: true })).toBe('bp');
		expect(takeRecord('bp')?.record.id).toBe('bp');
		expect(planFor).not.toHaveBeenCalled();
	});

	it('is not the answer once the end rule changes', async () => {
		lookAhead('a', { pictures: false });
		await settle();
		expect(await playOn('a', { pictures: true })).toBe('bp');
	});

	it('is dropped by a new list, by Shuffle and on close', async () => {
		lookAhead('a', { pictures: false });
		await settle();
		openAsset('a', [CLIP('a'), CLIP('x')]);
		expect(takeRecord('c')).toBeNull();
		expect(await playOn('a', { pictures: false })).toBe('x');

		lookAhead('a', { pictures: false });
		await settle();
		dropAhead();
		expect(takeRecord('x')).toBeNull();

		lookAhead('a', { pictures: false });
		await settle();
		const asked = get.mock.calls.length;
		toggleShuffle('a');
		lookAhead('a', { pictures: false });
		await settle();
		expect(get.mock.calls.length).toBe(asked + 1);
	});
});

describe('looking ahead under Shuffle', () => {
	it('peeks the walk without moving it, and the end steps it to the same file', async () => {
		toggleShuffle('a');
		lookAhead('a', { pictures: false });
		await settle();
		expect(run.walk?.current).toBe('a');
		expect(get).toHaveBeenCalledWith('/assets/c');

		expect(await playOn('a', { pictures: false })).toBe('c');
		expect(run.walk?.current).toBe('c');
		expect(takeRecord('c')?.record.id).toBe('c');
		expect((await takePlan('c'))?.url).toBe('/api/assets/c/stream');
	});

	it('finds what the end will find at every step, round the wrap and back again', async () => {
		openAsset('a', ['a', 'b', 'c', 'd'].map(CLIP));
		toggleShuffle('a');
		let on = 'a';
		for (let step = 0; step < 6; step += 1) {
			lookAhead(on, { pictures: false });
			await settle();
			const cursor = run.walk?.cursor;
			const to = await playOn(on, { pictures: false });
			expect(to).not.toBe(on);
			expect(takeRecord(to!)?.record.id).toBe(to);
			expect(run.walk?.cursor).not.toBe(cursor);
			on = to!;
		}
	});
});
