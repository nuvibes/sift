/* A wall in an order that hangs on opinions re-reads when one moves a file; while this tab's own
   player is open over it, the re-read waits for the player to go, so nothing reorders under it. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';

const shown = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock('$app/state', () => ({
	page: {
		get state() {
			return shown.state;
		}
	}
}));
vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async () => ({})),
		post: vi.fn(async () => ({})),
		put: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	}
}));

import { WallCatchUp } from './wall-catch-up.svelte';
import { mini } from '$lib/player/mini.svelte';

let stop: (() => void) | null = null;

/** A catch-up over a wall whose page read is a spy. */
function wall() {
	const loadAt = vi.fn(async () => {});
	const parts = {
		grid: {
			reading: false,
			loading: false,
			newer: 0,
			newerArrived: false,
			items: [{ id: 'a' }],
			rows: [],
			offset: 0,
			askedAt: 0,
			loadAt
		},
		order: { byMeaning: false, fullQuery: {} },
		source: () => ({ anchored: true }),
		media: () => ({ onScreen: new Set(['a']), rearm: vi.fn() }),
		remember: vi.fn()
	};
	let made: WallCatchUp | null = null;
	stop = $effect.root(() => {
		made = new WallCatchUp(parts as unknown as ConstructorParameters<typeof WallCatchUp>[0]);
	});
	flushSync();
	return { current: made as unknown as WallCatchUp, loadAt };
}

afterEach(() => {
	stop?.();
	stop = null;
	mini.close();
	shown.state = {};
});

describe('an opinion that moves a file', () => {
	it('re-reads the page immediately with no player over the wall', () => {
		const { current, loadAt } = wall();
		current.opinionMoved();
		expect(loadAt).toHaveBeenCalledTimes(1);
	});

	it('waits while the corner panel is open, and re-reads once it closes', () => {
		const { current, loadAt } = wall();
		mini.open({ id: 'x', mediaType: 'video' }, { width: 1400, height: 900 });
		flushSync();

		current.opinionMoved();
		flushSync();
		expect(loadAt, 'the wall reordered under the player').not.toHaveBeenCalled();

		mini.close();
		flushSync();
		expect(loadAt, 'the held re-read was never made').toHaveBeenCalledTimes(1);
	});

	it('waits while the popout is open over the wall', () => {
		const { current, loadAt } = wall();
		shown.state = { asset: 'x' };

		current.opinionMoved();
		expect(loadAt).not.toHaveBeenCalled();
	});
});

describe('files that moved up rather than arrived', () => {
	it('are never taken in by themselves at the top of the wall', () => {
		const { current, loadAt } = wall();
		const grid = (current as unknown as { wall: { grid: Record<string, unknown> } }).wall.grid;
		grid.newer = 40;
		current.letThemIn();
		expect(loadAt, 'files that moved were taken in with nothing pressed').not.toHaveBeenCalled();

		grid.newerArrived = true;
		current.letThemIn();
		expect(loadAt, 'arrivals at the top stopped coming in').toHaveBeenCalledTimes(1);
	});
});

describe('a page somebody turned to', () => {
	it('is never moved by arrivals while it is still on its way', () => {
		const { current, loadAt } = wall();
		const grid = (current as unknown as { wall: { grid: Record<string, unknown> } }).wall.grid;
		grid.newer = 40;
		grid.newerArrived = true;
		grid.loading = true;
		current.letThemIn();
		expect(loadAt, 'arrivals came in over a page on its way').not.toHaveBeenCalled();

		grid.loading = false;
		current.letThemIn();
		expect(loadAt).toHaveBeenCalledTimes(1);
	});
});

describe('several bells in one live message', () => {
	it('are one read, and a bell in a later turn is another', async () => {
		const { current, loadAt } = wall();
		// `library` and `arrivals` in one message: a read asked after both covers each.
		current.catchUp();
		current.catchUp();
		expect(loadAt, 'one message read the page twice').toHaveBeenCalledTimes(1);

		await new Promise((done) => setTimeout(done, 0));
		current.catchUp();
		expect(loadAt).toHaveBeenCalledTimes(2);
	});
});
