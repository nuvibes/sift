/* The phone's motion: what comes and goes at a phone's width, and how.
 *
 * A sheet from the foot rises out of that edge and sinks back into it, one pace quicker; a
 * Settings section pushed over More comes in from the trailing edge and goes back out to it; the
 * viewer grows out of the tile it was opened from and shrinks back into it; a step through the run
 * arrives from the side it was sent to. Under reduced motion every one of them is a fade in place.
 * The scripted halves are driven here; the stylesheet halves are read, since a test document has
 * no stylesheet to run them in.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fromEdge, fromPlace, motion, REDUCED_CAP_MS } from './motion.svelte';
import { STEP_TRAVEL, stepArrival } from '../components/player/swipe';

const animate = vi.hoisted(() => vi.fn(() => Promise.resolve()));
vi.mock('motion', () => ({ animate }));

const HERE = dirname(fileURLToPath(import.meta.url));
const source = (path: string) => readFileSync(join(HERE, '..', '..', path), 'utf8');

beforeEach(() => {
	animate.mockClear();
	motion.preference = 'full';
});

afterEach(() => {
	motion.preference = 'system';
});

const half = (made: ReturnType<typeof fromEdge>, direction: 'in' | 'out') => made({ direction });

describe('a surface standing on an edge', () => {
	it('rises the whole of its height out of the foot at the pace a sheet opens at', () => {
		const opening = half(fromEdge(document.createElement('div')), 'in');

		expect(opening.duration).toBe(200);
		expect(opening.css(0, 1)).toBe('transform: translateY(100%)');
		expect(opening.css(1, 0)).toBe('transform: translateY(0%)');
	});

	it('and sinks back into it one pace quicker, on the exit curve', () => {
		const closing = half(fromEdge(document.createElement('div')), 'out');

		expect(closing.duration).toBe(140);
		expect(closing.css(0.5, 0.5)).toBe('transform: translateY(50%)');
		expect(closing.easing(0.5), 'The exit curve starts slowly.').toBeLessThan(0.5);
	});

	it('comes in from the trailing edge for a page pushed over another', () => {
		const opening = half(fromEdge(document.createElement('div'), { edge: 'end' }), 'in');

		expect(opening.css(0, 1)).toBe('transform: translateX(100%)');
	});

	it('fades in place under reduced motion, in both directions', () => {
		motion.preference = 'reduce';
		const made = fromEdge(document.createElement('div'), { edge: 'end' });

		for (const direction of ['in', 'out'] as const) {
			const { css, duration } = made({ direction });
			expect(css(0.5, 0.5)).toBe('opacity: 0.5');
			expect(duration).toBeLessThanOrEqual(REDUCED_CAP_MS);
		}
	});
});

describe('the viewer and the tile it was opened from', () => {
	function placed(rect: Partial<DOMRect>): HTMLElement {
		const node = document.createElement('div');
		node.getBoundingClientRect = () =>
			({ left: 0, top: 0, width: 400, height: 800, right: 400, bottom: 800, ...rect }) as DOMRect;
		return node;
	}
	const tile = { left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100 } as DOMRect;

	it('starts at the size and the place of the tile, and ends as itself', () => {
		const opening = fromPlace(placed({}), { from: () => tile })({ direction: 'in' });

		expect(opening.duration, 'The one movement at the slow pace.').toBe(320);
		// The tile's centre is (50, 50); the viewer's is (200, 400).
		expect(opening.css(0, 1)).toBe('opacity: 0; transform: translate(-150px, -350px) scale(0.25)');
		expect(opening.css(1, 0)).toBe('opacity: 1; transform: translate(0px, 0px) scale(1)');
	});

	it('shrinks back into it one pace quicker', () => {
		const closing = fromPlace(placed({}), { from: () => tile })({ direction: 'out' });

		expect(closing.duration).toBe(200);
		expect(closing.css(0, 1)).toContain('scale(0.25)');
	});

	it('grows from a little smaller than itself in place with no tile to grow from', () => {
		const opening = fromPlace(placed({}), { from: () => null })({ direction: 'in' });

		expect(opening.css(0, 1)).toBe('opacity: 0; transform: translate(0px, 0px) scale(0.88)');
	});

	it('is a fade alone under reduced motion', () => {
		motion.preference = 'reduce';
		const opening = fromPlace(placed({}), { from: () => tile })({ direction: 'in' });

		expect(opening.css(0.5, 0.5)).toBe('opacity: 0.5');
	});
});

describe('a step through the run', () => {
	function viewer(): HTMLElement {
		const frame = document.createElement('div');
		const box = document.createElement('div');
		box.setAttribute('data-steps', '');
		frame.append(box);
		return frame;
	}

	it('brings the next file in from the right and the one before from the left', async () => {
		await stepArrival(viewer(), 'next');
		await stepArrival(viewer(), 'previous');

		const [first, second] = animate.mock.calls as unknown as [
			[Element, { x: number[] }],
			[Element, { x: number[] }]
		];
		expect(first[1].x).toEqual([STEP_TRAVEL, 0]);
		expect(second[1].x).toEqual([-STEP_TRAVEL, 0]);
	});

	it('takes its inline style off again once it has arrived', async () => {
		const frame = viewer();
		await stepArrival(frame, 'next');

		expect((frame.firstElementChild as HTMLElement).style.transform).toBe('');
	});

	it('does nothing where there is no box to step on', async () => {
		await stepArrival(document.createElement('div'), 'next');

		expect(animate).not.toHaveBeenCalled();
	});
});

describe('what the stylesheets say', () => {
	it('declares the sheet motions once, in the global stylesheet', () => {
		const css = source('app.css');

		expect(css).toMatch(/@keyframes sheet-in\s*\{\s*from\s*\{\s*translate: 0 100%;/);
		expect(css).toMatch(/@keyframes sheet-out\s*\{\s*to\s*\{\s*translate: 0 100%;/);
	});

	it('opens every menu sheet out of the foot and closes it back one pace quicker', () => {
		const menu = source('lib/components/common/ContextMenu.svelte');

		expect(menu).toMatch(
			/\.ui-menu\.menu-sheet\) \{[^}]*animation: sheet-in var\(--dur-base\) var\(--ease\);/
		);
		expect(menu).toMatch(
			/\.ui-menu\.menu-sheet\[data-state='closed'\]\) \{\s*animation: sheet-out var\(--dur-fast\) var\(--ease-in\) forwards;/
		);
	});

	it('fades a menu sheet in place under reduced motion', () => {
		const menu = source('lib/components/common/ContextMenu.svelte');

		expect(menu).toMatch(
			/:root\[data-motion='reduce'\] \.ui-menu\.menu-sheet\) \{\s*animation-name: appear;/
		);
		expect(menu).toMatch(
			/:root\[data-motion='reduce'\] \.ui-menu\.menu-sheet\[data-state='closed'\]\) \{\s*animation-name: leave;/
		);
	});

	it('takes the drawer back into its edge one pace quicker, and fades it under reduced motion', () => {
		const drawer = source('lib/components/common/Drawer.svelte');

		expect(drawer).toMatch(/\.drawer \{[^}]*transform var\(--dur-fast\) var\(--ease-in\)/);
		expect(drawer).toMatch(/\.drawer\.open \{[^}]*transform var\(--dur-base\) var\(--ease\)/);
		expect(drawer).toMatch(/data-motion='reduce'\]\) \.drawer \{\s*opacity: 0;\s*transform: none;/);
	});

	it('sinks the popover sheet it draws itself, and pushes a Settings section from the side', () => {
		expect(source('lib/components/common/Popover.svelte')).toMatch(
			/class="ui-menu menu-sheet"[^>]*out:fromEdge/
		);
		expect(source('lib/components/SettingsModal.svelte')).toMatch(
			/phoneWidth\.yes \? fromEdge\(node, \{ edge: 'end' \}\)/
		);
	});
});
