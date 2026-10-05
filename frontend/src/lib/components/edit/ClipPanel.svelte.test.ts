import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ClipPanel from './ClipPanel.svelte';

/* A ten-minute video on a track 1000 px wide: one pixel is 600 ms. */
const DURATION_MS = 600_000;

let shown: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (shown) unmount(shown);
	shown = null;
	document.body.innerHTML = '';
});

function draw(startMs: number, endMs: number): HTMLElement {
	shown = mount(ClipPanel, {
		target: document.body,
		props: { src: '', durationMs: DURATION_MS, startMs, endMs, kind: 'trim' }
	});
	flushSync();
	const track = document.querySelector('.timeline') as HTMLElement;
	track.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 44 }) as DOMRect;
	const kept = document.querySelector('.kept') as HTMLElement;
	kept.setPointerCapture = () => {};
	return kept;
}

function press(node: HTMLElement, from: number, to: number): void {
	node.dispatchEvent(new PointerEvent('pointerdown', { clientX: from, bubbles: true }));
	node.dispatchEvent(new PointerEvent('pointermove', { clientX: to, bubbles: true }));
	node.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
	flushSync();
}

function label(selector: string): string {
	return document.querySelector(selector)?.getAttribute('aria-label') ?? '';
}

describe('a press on the kept part', () => {
	it('moves the look-through mark to the pressed moment when it does not slide', () => {
		press(draw(60_000, 240_000), 250, 254);

		expect(label('.head')).toMatch(/2:30$/);
		expect(label('[data-end="start"]')).toMatch(/1:00$/);
	});

	it('slides the whole piece once the pointer travels past the press slop', () => {
		press(draw(60_000, 240_000), 250, 300);

		expect(label('[data-end="start"]')).toMatch(/1:30$/);
		expect(label('[data-end="end"]')).toMatch(/4:30$/);
	});
});
