import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import PresetPill from './PresetPill.svelte';
import type { Preset } from '$lib/theater/presets.svelte';

/*
 * One saved wall, as a pill with a picture of what it will make.
 *
 * A wall's name says nothing about what watching it is like: two walls can both be a 2x2 grid and
 * be completely different things. So the preview is the shape AND what each cell draws from, and
 * both halves are what this pins, along with the menu being built from the handlers actually
 * supplied, which is the same rule the filter pill beside it follows and for the same reason.
 *
 * The menu itself is the library's and cannot be opened in jsdom. What is asserted is what is
 * DECLARED; `theater.spec.ts` presses the pill for real.
 */

const KEPT: Preset = {
	id: 'w1',
	name: 'Runway wall',
	layout: 'side_by_side',
	shape: null,
	cells: [
		{
			source: 'people:jane',
			media_kind: 'video_gif',
			ordering: 'shuffle',
			end_behaviour: 'loop_all',
			timer_seconds: null,
			volume: 100,
			sort: null,
			aspect: 'dynamic'
		},
		{
			source: '   ',
			media_kind: 'video_gif',
			ordering: 'shuffle',
			end_behaviour: 'loop_all',
			timer_seconds: null,
			volume: 100,
			sort: null,
			aspect: 'dynamic'
		}
	]
} as unknown as Preset;

let host: HTMLDivElement;
let mounted: Record<string, unknown> | null = null;

function draw(props: Record<string, unknown>) {
	mounted = mount(PresetPill, {
		target: host,
		props: { kept: KEPT, onopen: () => {}, ...props }
	}) as Record<string, unknown>;
	flushSync();
}

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host.remove();
});

it('draws the name it was kept under', () => {
	draw({});

	expect(host.textContent).toContain('Runway wall');
});

/* WHAT THE BUBBLE DRAWS IS NOT REACHABLE HERE, and that is worth saying rather than leaving as a
 * gap. The preview (the shape, and what each cell draws from) is handed to `KeptPill` as the
 * `detail` of a `Tooltip`, which mounts its content only when it opens; opening one is `bits-ui`
 * and a layout, neither of which jsdom has. `theater.spec.ts` is where a wall pill is pointed at.
 */

it('offers only the verbs its caller can honour', () => {
	// A menu row that fails reads as broken rather than as not-here. The cell picker has no screen
	// to update from, so it supplies no `onupdate` and must not be offered one.
	draw({ onupdate: undefined, onrename: undefined, onremove: undefined });

	expect(host.textContent).not.toContain('Update from the wall on screen');
});

it('opens the wall when the pill itself is pressed', () => {
	const opened = vi.fn();
	draw({ onopen: opened });

	host.querySelector('button')?.click();
	flushSync();

	expect(opened).toHaveBeenCalledWith(KEPT);
});
