/*
 * The wall's one bar, at the sizes and in the states a wall actually reaches: nine cells, a cell
 * with nothing playing, and the wall changing shape while the bar is up.
 *
 * The last can go wrong quietly. The bar is drawn from `wall.cells`, and the strip underneath is
 * held clear by the bar's own measured height, so a bar that did not follow a change of shape would
 * leave numbers for cells that are not there.
 *
 * Not here, because jsdom cannot answer it: whether nine numbers wrap or the row scrolls, and the
 * bar's height. Both are layout, measured in a browser; a unit test pretending to answer them would
 * report `0` and call it a pass.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(async () => ({ items: [], total: 0 })), post: vi.fn(async () => ({})) }
}));

import StallBar from './StallBar.svelte';
import { Wall } from '$lib/theater/wall.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

function draw(wall: Wall) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(StallBar, {
		target: host,
		props: { wall, up: true }
	}) as Record<string, unknown>;
	flushSync();
	return wall;
}

/** The numbered pickers, in the order they are drawn. `All` is one of them and comes first. */
function pickers(): string[] {
	return [
		...host.querySelectorAll('[role="group"][aria-label="Which cell to control"] button')
	].map((one) => one.getAttribute('aria-label') ?? '');
}

beforeEach(() => {
	vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
	vi.restoreAllMocks();
});

it('draws a number for every cell of the biggest wall there is', () => {
	const wall = new Wall();
	wall.setLayout('grid');
	for (let more = wall.cells.length; more < 9; more += 1) wall.addPreview();
	draw(wall);

	expect(wall.cells, 'nine is the wall').toHaveLength(9);
	expect(pickers()).toEqual([
		'Controls for every cell',
		...Array.from({ length: 9 }, (_, at) => `Controls for cell ${at + 1}`)
	]);
});

it('follows the wall changing shape while it is up', () => {
	/* The bar is not torn down and rebuilt when the layout changes: it is the same bar over a
	   different wall. Numbers for cells that are no longer there would point at nothing. */
	const wall = new Wall();
	wall.setLayout('grid');
	draw(wall);
	expect(pickers()).toHaveLength(5);

	wall.setLayout('single');
	flushSync();

	expect(pickers(), 'the row still offered four cells over a wall of one').toEqual([
		'Controls for every cell',
		'Controls for cell 1'
	]);
});

it('is drawn for a cell with nothing playing at all', () => {
	/* A wall opens with every cell empty and the bar comes up with it. It draws one cell's clock and
	   one cell's transport, and neither exists yet, so this is the state the bar is in first. */
	const wall = new Wall();
	wall.setLayout('single');
	draw(wall);

	expect(wall.cells[0].playing, 'the fixture was not the empty case after all').toBeNull();
	expect(
		host.querySelector('.stage-bar'),
		'the bar drew nothing over an empty wall'
	).not.toBeNull();
	expect(pickers()).toEqual(['Controls for every cell', 'Controls for cell 1']);
});

it('is not drawn at all while there is no wall to control', () => {
	// The positive control for the three above: a bar that always drew would satisfy every one.
	const wall = new Wall();
	wall.setLayout('single');
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(StallBar, {
		target: host,
		props: { wall, up: false }
	}) as Record<string, unknown>;
	flushSync();

	expect(host.querySelector('.stage-bar')).toBeNull();
});

it('marks the wall at the head of its numbers while a phone drives it, and only then', async () => {
	const { screenOffer } = await import('$lib/remote/offer.svelte');
	draw(new Wall());
	const mark = () => host.querySelector('.numbers .controlled [aria-label]');
	expect(mark(), 'nobody is driving it').toBeNull();

	screenOffer.controlledSurface = 'theater';
	screenOffer.controlledBy = ['Safari on an iPhone or iPad'];
	flushSync();
	expect(mark()?.getAttribute('aria-label')).toBe(
		'Remote controlled from Safari on an iPhone or iPad'
	);

	/* A phone driving a player in this tab is not driving the wall. */
	screenOffer.controlledSurface = 'player';
	flushSync();
	expect(mark(), 'the phone is on a player').toBeNull();

	screenOffer.controlledSurface = 'theater';
	screenOffer.controlledBy = [];
	flushSync();
	expect(mark(), 'the phone let go').toBeNull();
	screenOffer.controlledSurface = null;
});
