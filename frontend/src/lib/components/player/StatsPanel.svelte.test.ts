/*
 * The panel of facts over the picture, and the one control on it.
 *
 * What is worth asserting here is the thing a reader would notice losing and nothing else would
 * catch: that the Copy control hands over the READINGS (every line the panel is drawing, worded
 * the way it is drawn, in the order it is drawn) rather than a subset, a different wording or the
 * markup around them. The rows themselves are `Readout`'s and the wordings are `$lib/player/facts`'
 * and both are tested where they live.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import StatsPanel from './StatsPanel.svelte';

const COPIED = vi.fn(async (_text: string) => true);
vi.mock('$lib/shell/clipboard', () => ({ copyText: (text: string) => COPIED(text) }));

let host: HTMLElement;
let instance: ReturnType<typeof mount> | null = null;

beforeEach(() => {
	COPIED.mockClear();
	COPIED.mockResolvedValue(true);
	host = document.createElement('div');
	document.body.appendChild(host);
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
});

/** A photograph in a cell: a still, so the eight playback lines are not drawn. */
function drawIt() {
	instance = mount(StatsPanel, {
		target: host,
		props: {
			file: {
				width: 1600,
				height: 900,
				container: 'jpg',
				size_bytes: 204800,
				vcodec: null,
				acodec: null,
				fps: null,
				bit_depth: 8
			},
			position: 0,
			duration: 0,
			still: true,
			kind: 'Photo',
			source: 'Everything',
			state: 'Showing'
		}
	});
	flushSync();
}

function copy(): void {
	const control = host.ownerDocument.querySelector('.stats button');
	if (!(control instanceof HTMLElement)) throw new Error('there is no Copy control on the panel');
	control.click();
	flushSync();
}

describe('copying the readings', () => {
	it('hands over every line the panel is drawing, in the order it draws them', async () => {
		drawIt();

		copy();
		await Promise.resolve();

		// Off the same list the rows are built from, so what is copied cannot come to disagree with
		// what is on screen, including the lines a still leaves out.
		expect(COPIED).toHaveBeenCalledTimes(1);
		const text = COPIED.mock.calls[0][0];
		expect(text.split('\n')).toEqual([
			'Aspect ratio: 16:9',
			'Bit depth: 8-bit',
			'Container: jpg',
			'Dimensions: 1600 x 900',
			'Kind: Photo',
			'Size on disk: 205 kB',
			'Source: Everything',
			'State: Showing'
		]);
	});

	it('says so on the control once it has landed, and then goes back to its name', async () => {
		/* The glyph is a codepoint out of the icon font, so this compares it with ITSELF rather than
		   naming either mark: what matters is that the control answers the press and then stops
		   answering it. Left saying Copied, the next person to look at it is told what happened to
		   somebody else instead of what pressing it would do. */
		vi.useFakeTimers();
		drawIt();
		const mark = () => host.ownerDocument.querySelector('.stats button')?.textContent ?? '';
		const resting = mark();

		copy();
		await Promise.resolve();
		flushSync();
		expect(mark(), 'the control said nothing about the press').not.toBe(resting);

		await vi.advanceTimersByTimeAsync(2000);
		flushSync();
		expect(mark(), 'and it stayed said').toBe(resting);
		vi.useRealTimers();
	});
});

/*
 * Where the control is, which is a decision and not a detail: at the foot, at the start of its
 * line, a named exception to "actions on the right" argued in the component. Both halves are pinned
 * so a tidy-up does not undo it: it comes after the readings in the document, and its line starts
 * rather than ends.
 */
describe('where the Copy control sits', () => {
	it('is under the readings, not above them', () => {
		drawIt();

		const panel = host.ownerDocument.querySelector('.stats');
		const readings = panel?.querySelector('.foot')?.previousElementSibling;
		const control = panel?.querySelector('.foot button');

		expect(control).not.toBeNull();
		// The readings are whatever `Readout` drew; what matters is that they come first.
		expect(readings).not.toBeNull();
		expect(readings?.querySelector('button')).toBeNull();
	});

	it('starts its line rather than ending it', () => {
		drawIt();

		expect(host.ownerDocument.querySelector('.stats .foot')).not.toBeNull();
		/* Read off the component's own source: the rule is in a scoped stylesheet, and asking the
		   document for it would be asking whether this test runner injects styles. */
		const here = dirname(fileURLToPath(import.meta.url));
		const source = readFileSync(join(here, 'StatsPanel.svelte'), 'utf8').replace(/\t/g, '');
		expect(source).toContain('.foot {\ndisplay: flex;\njustify-content: flex-start;\n}');
	});
});

/*
 * CAPPED TO THE PICTURE IT SITS ON.
 *
 * A portrait cell on a wall is often narrower than 280 and shorter than the readings, and the stage
 * clips, so an uncapped panel would be cut off at the cell's edges with nothing to reach the rest.
 * jsdom lays nothing out, so what is pinned is the rule (read off the source, where the scoped
 * stylesheet lives) and the one piece of structure the rule depends on: the readings are in the shared
 * scrolling region, so they scroll under the ceiling rather than being clipped by it.
 */
describe('a panel on a picture smaller than it', () => {
	const here = dirname(fileURLToPath(import.meta.url));
	const source = readFileSync(join(here, 'StatsPanel.svelte'), 'utf8');

	it('is never wider or taller than the picture less its inset', () => {
		expect(source).toMatch(
			/^\s*max-inline-size: min\(280px, calc\(100% - 2 \* var\(--space-3\)\)\);$/m
		);
		expect(source).toMatch(/^\s*max-block-size: calc\(100% - 2 \* var\(--space-3\)\);$/m);
		// The track the scrolling region is bounded by: without it the cap is a line it paints through.
		expect(source).toMatch(/^\s*grid-template-rows: minmax\(0, 1fr\) auto;$/m);
	});

	it('scrolls its readings rather than clipping them, and keeps the control outside the scroll', () => {
		drawIt();

		const panel = host.ownerDocument.querySelector('.stats');
		const region = panel?.querySelector('.scroll-root');
		expect(region, 'the readings are not in a scrolling region').not.toBeNull();
		expect(region?.querySelector('dl, [role="list"], div')).not.toBeNull();
		expect(region?.contains(panel?.querySelector('.foot') ?? null)).toBe(false);
	});
});
