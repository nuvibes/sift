/* The Audio player centres in the page beside the rail, not in the window: the page's box is
 * measured onto the bar, and measured again when the rail or the window moves it. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { inThePage } from './in-the-page';

let heard: (() => void) | null = null;
const disconnect = vi.fn();

beforeEach(() => {
	heard = null;
	disconnect.mockClear();
	vi.stubGlobal(
		'ResizeObserver',
		class {
			constructor(callback: () => void) {
				heard = callback;
			}
			observe() {}
			disconnect = disconnect;
		}
	);
});

afterEach(() => {
	vi.unstubAllGlobals();
	document.body.replaceChildren();
});

/** A shell with a rail, the page beside it at `left` and `width`, and the bar after them. */
function shell(left: number, width: number) {
	const root = document.createElement('div');
	root.className = 'shell';
	const rail = document.createElement('nav');
	const content = document.createElement('div');
	content.className = 'content';
	const bar = document.createElement('section');
	root.append(rail, content, bar);
	document.body.append(root);
	const box = { left, width };
	content.getBoundingClientRect = () => ({ left: box.left, width: box.width }) as DOMRect;
	return { bar, box };
}

it('writes the page box, beside the rail, onto the bar', () => {
	const { bar } = shell(208, 884);
	inThePage(bar);
	expect(bar.style.getPropertyValue('--frame-x')).toBe('208px');
	expect(bar.style.getPropertyValue('--frame-w')).toBe('884px');
});

it('measures again when the rail collapses, and lets go when the bar goes', () => {
	const { bar, box } = shell(208, 884);
	const stop = inThePage(bar);
	box.left = 64;
	box.width = 1028;
	heard?.();
	expect(bar.style.getPropertyValue('--frame-x')).toBe('64px');
	expect(bar.style.getPropertyValue('--frame-w')).toBe('1028px');

	stop();
	expect(disconnect).toHaveBeenCalledTimes(1);
	expect(bar.style.getPropertyValue('--frame-x')).toBe('');
});

it('writes nothing outside the shell, so the bar falls back to the window', () => {
	const bar = document.createElement('section');
	document.body.append(bar);
	inThePage(bar)();
	expect(bar.style.getPropertyValue('--frame-x')).toBe('');
});
