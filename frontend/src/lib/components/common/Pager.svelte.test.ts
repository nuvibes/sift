/* The bar at the foot of a wall: every turn lands at the top of the new page. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Pager from './Pager.svelte';
import { scrollingBody } from '$lib/components/shell/page-scroll';

let drawn: Record<string, unknown> | null = null;
let host: HTMLElement;
let box: HTMLElement;
let release: (() => void) | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	release?.();
	release = null;
	document.body.innerHTML = '';
});

function draw(props: Partial<Record<string, unknown>> = {}) {
	/* The scrolling body a screen registers, sitting where the last page left it. */
	box = document.createElement('div');
	document.body.append(box);
	release = scrollingBody(box);
	box.scrollTop = 640;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Pager, {
		target: host,
		props: {
			offset: 24,
			shown: 24,
			total: 200,
			onfirst: vi.fn(),
			onprevious: vi.fn(),
			onnext: vi.fn(),
			onlast: vi.fn(),
			onjump: vi.fn(),
			...props
		}
	}) as Record<string, unknown>;
	flushSync();
}

function press(label: string): void {
	host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)?.click();
	flushSync();
}

describe('a page turn from the bar at the foot', () => {
	it('turns the page AND puts the scrolling body back at the top', () => {
		/*
		 * The next page must not arrive under a box still scrolled to the bottom of the last. The
		 * pager owns the press; the shell's scroll helper knows which box is scrolling.
		 */
		const onnext = vi.fn();
		draw({ onnext });

		press('Next page');

		expect(onnext).toHaveBeenCalledTimes(1);
		expect(box.scrollTop).toBe(0);
	});

	it('does the same for the way back, the first and the last page', () => {
		for (const label of ['Previous page', 'First page', 'Last page']) {
			draw();
			press(label);
			expect(box.scrollTop, label).toBe(0);
			unmount(drawn as unknown as Record<string, unknown>);
			drawn = null;
			release?.();
			document.body.innerHTML = '';
		}
	});
});

describe('the readout says what it counts', () => {
	const readout = () =>
		host
			.querySelector('button[aria-label="Go to a position"]')
			?.textContent?.replace(/\s+/g, ' ')
			.trim();

	it('names the rows after the total, on every wall', () => {
		draw({ offset: 0, shown: 16, total: 246, noun: 'Sites' });
		expect(readout()).toBe('1-16 of 246 Sites');
	});

	it('says files where the wall names nothing', () => {
		draw({ offset: 0, shown: 32, total: 100000 });
		expect(readout()).toBe(`1-32 of ${(100000).toLocaleString()} files`);
	});

	it('says one of a thing in the singular', () => {
		draw({ offset: 0, shown: 1, total: 1, noun: 'people' });
		expect(readout()).toBe('1-1 of 1 person');
		unmount(drawn as unknown as Record<string, unknown>);
		drawn = null;
		release?.();
		draw({ offset: 0, shown: 1, total: 1, noun: 'Photo Sets' });
		expect(readout()).toBe('1-1 of 1 Photo Set');
		unmount(drawn as unknown as Record<string, unknown>);
		drawn = null;
		release?.();
		draw({ offset: 0, shown: 1, total: 1, noun: 'matches', one: 'match' });
		expect(readout()).toBe('1-1 of 1 match');
	});
});
