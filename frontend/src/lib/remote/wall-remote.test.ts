/*
 * A Theater wall answering the phone's presses that no key makes, and saying where its drawer
 * stands.
 *
 * What would be silent if it broke: a press the Remote draws for a wall that the wall does not
 * answer (the phone would say the screen never acted), a choice landing on a different layout or
 * preset from the one the phone named, a press reaching the wrong cells, and the drawer or the
 * lists reported as something other than what the desk shows.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { commanded } from '$lib/shell/shortcuts';
import { LAYOUTS } from '$lib/theater/layouts';
import type { Cell } from '$lib/theater/cell.svelte';
import type { Wall } from '$lib/theater/wall.svelte';

const kept = [
	{ id: 'p1', name: 'Evening' },
	{ id: 'p2', name: 'Rainy day' }
];

vi.mock('$lib/theater/presets.svelte', () => ({
	presets: { items: kept, ensure: vi.fn(async () => {}) }
}));

const clipped = vi.fn(async (..._args: unknown[]) => ({ made: true as const }));
vi.mock('$lib/edit/edit.svelte', () => ({
	clipTheStretch: (...args: unknown[]) => clipped(...args)
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

const { wallPresses, wallState } = await import('./wall-remote');

/** A cell with just what the wall's presses read and write. */
function aCell(id: string | null) {
	return {
		playing: id === null ? null : { id },
		position: 10,
		duration: 60,
		volume: 50,
		paused: false,
		muted: true,
		endBehaviour: 'loop_all',
		sort: null as string | null,
		get ordering() {
			return this.sort === 'random' ? 'shuffle' : 'in_order';
		},
		orderBy(next: string | null) {
			this.sort = next;
		},
		reorder: vi.fn(async () => {}),
		restart: vi.fn(async () => {}),
		timerSeconds: null as number | null,
		plan: {
			url: '/big',
			qualities: [
				{ url: '/big', label: '1080p' },
				{ url: '/small', label: '480p' }
			]
		},
		quality: null as { url: string } | null,
		changeQuality: vi.fn(),
		somethingElse: vi.fn(async () => {}),
		sought: [] as number[],
		seek(to: number) {
			this.sought.push(to);
		},
		loop: {
			a: 2 as number | null,
			b: 8 as number | null,
			running: true,
			owns: (owner: string) => owner === id
		}
	};
}

type FakeCell = ReturnType<typeof aCell>;

function aWall(cells: FakeCell[]) {
	return {
		cells,
		focused: 1,
		everyCell: false,
		layout: 'grid' as string | null,
		get addressed() {
			return this.everyCell ? this.cells : [this.cells[this.focused]];
		},
		setLayout: vi.fn(),
		adopt: vi.fn(),
		solo: vi.fn()
	};
}

let cells: FakeCell[];
let wall: ReturnType<typeof aWall>;

beforeEach(() => {
	vi.clearAllMocks();
	cells = [aCell('f1'), aCell('f2'), aCell(null)];
	wall = aWall(cells);
});

const presses = () => wallPresses(wall as unknown as Wall);
const state = () => wallState(wall as unknown as Wall);

describe("a wall's presses from the phone", () => {
	it('lands a layout and a preset on the one the phone named, by its place', () => {
		expect(commanded(presses(), 'theater.layout', 1)).toBe(true);
		expect(wall.setLayout).toHaveBeenCalledWith(LAYOUTS[1].id);
		expect(commanded(presses(), 'theater.preset', 1)).toBe(true);
		expect(wall.adopt).toHaveBeenCalledWith(kept[1]);
		// Past either list is nobody's: refused, so the phone hears nothing happened.
		expect(commanded(presses(), 'theater.layout', LAYOUTS.length)).toBe(false);
		expect(commanded(presses(), 'theater.preset', 2)).toBe(false);
	});

	it('acts on the cell being talked to, or on every cell once the wall addresses them all', () => {
		expect(commanded(presses(), 'theater.volumeTo', 30)).toBe(true);
		expect(cells.map((one) => one.volume)).toEqual([50, 30, 50]);
		expect(commanded(presses(), 'theater.random', null)).toBe(true);
		expect(cells[1].somethingElse).toHaveBeenCalledOnce();

		wall.everyCell = true;
		expect(commanded(presses(), 'theater.timer', 30)).toBe(true);
		expect(cells.map((one) => one.timerSeconds)).toEqual([30, 30, 30]);
		expect(commanded(presses(), 'theater.timer', 0)).toBe(true);
		expect(cells.map((one) => one.timerSeconds)).toEqual([null, null, null]);
	});

	it('takes the shuffle the phone wants, leaving a cell already there as it is', () => {
		expect(commanded(presses(), 'theater.shuffle', 0)).toBe(true);
		expect(cells[1].reorder).not.toHaveBeenCalled();
		expect(commanded(presses(), 'theater.shuffle', 1)).toBe(true);
		expect(cells[1].ordering).toBe('shuffle');
	});

	it('seeks, sizes, saves and solos the one clip the bar is drawing', () => {
		expect(commanded(presses(), 'theater.seekTo', 500)).toBe(true);
		expect(cells[1].sought).toEqual([60]);
		expect(commanded(presses(), 'theater.quality', 1)).toBe(true);
		expect(cells[1].changeQuality).toHaveBeenCalledWith(cells[1].plan.qualities[1]);
		expect(commanded(presses(), 'theater.saveLoop', null)).toBe(true);
		expect(clipped).toHaveBeenCalledWith('f2', 2000, 6000, { asLoop: true });
		expect(commanded(presses(), 'theater.solo', null)).toBe(true);
		expect(wall.solo).toHaveBeenCalledWith(1);
	});

	it('refuses a save with no loop marked, as the drawer dims it', () => {
		cells[1].loop.b = null;
		cells[1].loop.running = false;
		expect(commanded(presses(), 'theater.saveLoop', null)).toBe(false);
		expect(clipped).not.toHaveBeenCalled();
	});
});

describe('what the wall reports about its drawer', () => {
	it("says the drawn cell's drawer, the bar's lists and what each cell shows", () => {
		cells[1].sort = 'random';
		cells[1].quality = { url: '/small' };

		expect(state()).toMatchObject({
			repeat: 'loop_all',
			shuffle: true,
			loop_marks: 2,
			qualities: ['1080p', '480p'],
			quality: 1,
			timer: null,
			every_cell: false,
			cell_held: false,
			cell_muted: true,
			layouts: LAYOUTS.map((one) => one.id),
			layout: LAYOUTS.findIndex((one) => one.id === 'grid'),
			presets: ['Evening', 'Rainy day'],
			cell_files: ['f1', 'f2', null]
		});
	});
});
