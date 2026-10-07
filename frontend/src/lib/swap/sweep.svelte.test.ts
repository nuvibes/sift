/*
 * A sweep across a wall in swap mode picks for the swap: the hand's rules are `TileGesture`'s,
 * what they paint is the swap's picks, and the wall's own selection is never touched.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Selection } from '$lib/components/common/selection.svelte';
import { TILE_ID, TileGesture } from '$lib/components/common/tile-gesture.svelte';
import { swapMode } from './mode.svelte';
import { SwapSelection, type SwapRow } from './sweep.svelte';

const ROWS: SwapRow[] = [
	{ id: 'r1', file: 'f1', name: 'harbor-walk.mp4', concealed: false },
	{ id: 'r2', file: 'f2', name: 'ridge-light.jpg', concealed: false },
	{ id: 'r3', file: 'f3', name: 'quiet-hours.mp4', concealed: true },
	{ id: 'r4', file: 'f4', name: 'late-ferry.mp4', concealed: false }
];
const order = () => ROWS.map((row) => row.id);
const heldFiles = () => swapMode.picks.filter((one) => one.kind === 'asset').map((one) => one.id);

/* The tile under the pointer, by x: one tile per hundred pixels. */
let tiles: HTMLElement[] = [];
beforeEach(() => {
	vi.useFakeTimers();
	swapMode.enter();
	tiles = ROWS.map((row) => {
		const tile = document.createElement('div');
		tile.setAttribute(TILE_ID, row.id);
		document.body.append(tile);
		return tile;
	});
	document.elementFromPoint = (x: number) => tiles[Math.floor(x / 100)] ?? null;
});

afterEach(() => {
	vi.useRealTimers();
	swapMode.leave();
	document.body.innerHTML = '';
});

function press(gesture: TileGesture, id: string, x: number, picks: SwapSelection) {
	picks.takeUp();
	gesture.pressStart(id, new PointerEvent('pointerdown', { button: 0, clientX: x, clientY: 5 }));
}
const move = (x: number) =>
	window.dispatchEvent(new PointerEvent('pointermove', { clientX: x, clientY: 5 }));
const lift = () => window.dispatchEvent(new PointerEvent('pointerup'));

describe('a sweep in swap mode', () => {
	it('picks every file it crosses for the swap, and never the wall selection', () => {
		const wall = new Selection();
		const picks = new SwapSelection(() => ROWS);
		const gesture = new TileGesture(picks, order);

		press(gesture, 'r1', 50, picks);
		vi.advanceTimersByTime(400);
		move(150);
		move(350);
		lift();

		// r3 is Hidden: crossed, and not picked, as a press on it never is.
		expect(heldFiles()).toEqual(['f1', 'f2', 'f4']);
		expect(wall.count).toBe(0);
	});

	it('gives back an overshoot, as a sweep outside the mode does', () => {
		const picks = new SwapSelection(() => ROWS);
		const gesture = new TileGesture(picks, order);

		press(gesture, 'r1', 50, picks);
		vi.advanceTimersByTime(400);
		move(350);
		move(150);
		lift();

		expect(heldFiles()).toEqual(['f1', 'f2']);
	});

	it('unpicks what it crosses when it began on a picked file, and leaves picks off screen alone', () => {
		swapMode.toggle({ kind: 'asset', id: 'elsewhere', name: 'from-another-wall.mp4' });
		swapMode.toggle({ kind: 'asset', id: 'f1', name: 'harbor-walk.mp4' });
		swapMode.toggle({ kind: 'asset', id: 'f2', name: 'ridge-light.jpg' });
		const picks = new SwapSelection(() => ROWS);
		const gesture = new TileGesture(picks, order);

		// Something on screen is picked, so the press sweeps immediately, with no hold.
		press(gesture, 'r1', 50, picks);
		move(150);
		lift();

		expect(heldFiles()).toEqual(['elsewhere']);
	});

	it('does not paint back a pick taken out in the drawer since the last press', () => {
		swapMode.toggle({ kind: 'asset', id: 'f1', name: 'harbor-walk.mp4' });
		const picks = new SwapSelection(() => ROWS);
		const gesture = new TileGesture(picks, order);
		press(gesture, 'r2', 150, picks);
		move(350);
		lift();
		expect(heldFiles()).toEqual(['f1', 'f2', 'f4']);

		swapMode.drop('asset', 'f1');
		press(gesture, 'r4', 350, picks);
		lift();
		picks.toggle('r2');
		expect(heldFiles()).toEqual(['f4']);
	});

	it('picks a file once when a wall draws it twice', () => {
		const twice: SwapRow[] = [
			{ id: 'm1', file: 'f1', name: 'harbor-walk.mp4', concealed: false },
			{ id: 'm2', file: 'f1', name: 'harbor-walk.mp4', concealed: false }
		];
		const picks = new SwapSelection(() => twice);
		picks.takeUp();
		picks.toggle('m2');
		expect(heldFiles()).toEqual(['f1']);
		picks.toggle('m2');
		expect(heldFiles()).toEqual([]);
	});
});
