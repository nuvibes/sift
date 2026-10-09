/* What a look at the queue reports, and why it reports a landing rather than an event. */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Changes, Sounds } from './sounds.svelte';

const stored = vi.hoisted(() => ({ values: {} as Record<string, unknown> }));
vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettings: async () => [
		{ settings: Object.entries(stored.values).map(([key, value]) => ({ key, value })) }
	]
}));

function row(id: string, status: string, over: { asset_id?: string; filename?: string } = {}) {
	return { id, status, asset_id: over.asset_id ?? null, filename: over.filename ?? null };
}

describe('what settled since the last look', () => {
	/* Opening the screen is not the moment to be told about everything that ever happened. */
	it('says nothing on the first look', () => {
		const changes = new Changes();
		expect(changes.since([row('a', 'done'), row('b', 'failed')])).toEqual([]);
	});

	it('reports a download that finished, with the file it made', () => {
		const changes = new Changes();
		// One still going, so what is reported is the finish alone and not the queue emptying too.
		changes.since([row('a', 'running'), row('b', 'running')]);
		const landed = changes.since([
			row('a', 'done', { asset_id: 'asset-1', filename: 'clip.mp4' }),
			row('b', 'running')
		]);
		expect(landed).toEqual([{ kind: 'done', count: 1, assetId: 'asset-1', filename: 'clip.mp4' }]);
	});

	/* The same row read again is not a new landing. */
	it('says nothing the second time it reads the same row', () => {
		const changes = new Changes();
		changes.since([row('a', 'running')]);
		changes.since([row('a', 'done', { asset_id: 'asset-1' })]);
		expect(changes.since([row('a', 'done', { asset_id: 'asset-1' })])).toEqual([]);
	});

	it('counts several that finished together and offers no single file', () => {
		const changes = new Changes();
		changes.since([row('a', 'running'), row('b', 'running'), row('c', 'running')]);
		const landed = changes.since([
			row('a', 'done', { asset_id: 'asset-1', filename: 'one.mp4' }),
			row('b', 'done', { asset_id: 'asset-2', filename: 'two.mp4' }),
			row('c', 'running')
		]);
		expect(landed).toEqual([{ kind: 'done', count: 2, assetId: null, filename: null }]);
	});

	it('reports a failure and something waiting for cookies apart from a finish', () => {
		const changes = new Changes();
		changes.since([row('a', 'running'), row('b', 'running')]);
		const kinds = changes.since([row('a', 'failed'), row('b', 'blocked')]).map((one) => one.kind);
		expect(kinds).toContain('failed');
		expect(kinds).toContain('blocked');
	});

	/* The queue emptying is the moment somebody can walk away, and it is the one most worth
	   hearing. */
	it('says the queue emptied once nothing is left to do', () => {
		const changes = new Changes();
		changes.since([row('a', 'running')]);
		const kinds = changes.since([row('a', 'done')]).map((one) => one.kind);
		expect(kinds).toEqual(['done', 'empty']);
	});

	it('says nothing about emptying on a screen that was already idle', () => {
		const changes = new Changes();
		changes.since([row('a', 'done')]);
		expect(changes.since([row('a', 'done')]).map((one) => one.kind)).toEqual([]);
	});

	/* A file that has since been deleted leaves a landing with nowhere to go, and the message
	   then names it without offering a door. */
	it('offers no file for a download that produced none', () => {
		const changes = new Changes();
		changes.since([row('a', 'running'), row('b', 'running')]);
		expect(changes.since([row('a', 'done'), row('b', 'running')])[0]).toEqual({
			kind: 'done',
			count: 1,
			assetId: null,
			filename: null
		});
	});
});

/* `Settings > Downloads`: whether a finished download makes a sound, and how loud. */
describe('the sound a finished download makes', () => {
	const levels: number[] = [];
	let built = 0;

	class FakeContext {
		currentTime = 0;
		destination = {};
		constructor() {
			built += 1;
		}
		createOscillator() {
			return {
				type: '',
				frequency: { setValueAtTime() {}, linearRampToValueAtTime() {} },
				connect: (next: unknown) => next,
				start() {},
				stop() {}
			};
		}
		createGain() {
			return {
				gain: {
					setValueAtTime() {},
					linearRampToValueAtTime: (level: number) => levels.push(level)
				},
				connect: (next: unknown) => next
			};
		}
	}

	afterEach(() => {
		vi.unstubAllGlobals();
		levels.length = 0;
		built = 0;
	});

	it('plays at the stored volume when the switch is on', async () => {
		vi.stubGlobal('AudioContext', FakeContext);
		stored.values = { 'download.sound': true, 'download.sound_volume': 17 };
		const sounds = new Sounds();

		await sounds.load();
		sounds.play('done');

		expect(built).toBe(1);
		expect(Math.max(...levels)).toBeCloseTo(0.17);
	});

	it('makes no sound at all while the switch is off, whatever the volume', async () => {
		vi.stubGlobal('AudioContext', FakeContext);
		stored.values = { 'download.sound': false, 'download.sound_volume': 90 };
		const sounds = new Sounds();

		await sounds.load();
		sounds.play('done');

		expect(built).toBe(0);
		expect(levels).toEqual([]);
	});
});
