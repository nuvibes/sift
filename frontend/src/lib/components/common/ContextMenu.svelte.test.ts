/*
 * Where a long right-click menu opens. jsdom lays nothing out, so what is read is what the menu
 * hands the positioning library and what its stylesheet makes of the answer: the room the library
 * offers (the window's whole height less the padding, because it may slide the menu along the
 * pointer's side) and the ceiling the menu takes from that room.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import source from './ContextMenu.svelte?raw';
import Probe from './ContextMenuTallProbe.test.svelte';

/* jsdom has no pointer capture and the primitives release it on the way down. */
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

/* A short window: the library reads the viewport from the root element's client box. */
const WINDOW = { width: 1600, height: 1000 };

let instance: ReturnType<typeof mount> | null = null;

beforeEach(() => {
	const root = document.documentElement;
	Object.defineProperty(root, 'clientWidth', { configurable: true, value: WINDOW.width });
	Object.defineProperty(root, 'clientHeight', { configurable: true, value: WINDOW.height });
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	removeStyles();
	document.body.innerHTML = '';
	const root = document.documentElement;
	delete (root as unknown as Record<string, unknown>).clientWidth;
	delete (root as unknown as Record<string, unknown>).clientHeight;
});

async function openNearTheBottom(): Promise<HTMLElement> {
	const host = document.createElement('div');
	document.body.append(host);
	instance = mount(Probe, { target: host, props: {} });
	flushSync();
	const trigger = host.querySelector('[data-context-menu-trigger]');
	expect(trigger, 'no right-click target').toBeTruthy();
	trigger?.dispatchEvent(
		new MouseEvent('contextmenu', { bubbles: true, clientX: 800, clientY: 900 })
	);
	let menu: HTMLElement | null = null;
	await vi.waitFor(
		() => {
			menu = document.querySelector<HTMLElement>('[data-bits-floating-content-wrapper] > .ui-menu');
			expect(menu, 'the menu never opened').toBeTruthy();
			const room = menu?.parentElement?.style.getPropertyValue('--bits-floating-available-height');
			expect(room).toMatch(/^\d+px$/);
		},
		{ timeout: 5000 }
	);
	applyStyles(source);
	return menu as unknown as HTMLElement;
}

/** A `var()` read from `vars` (or its fallback), then a `min()` of plain pixel lengths. */
function resolved(value: string, vars: Record<string, string>): number {
	let text = value.trim();
	const innermost = /var\((--[\w-]+)(?:,\s*([^()]*))?\)/;
	for (let found = innermost.exec(text); found; found = innermost.exec(text)) {
		text = text.replace(found[0], vars[found[1]] ?? found[2] ?? '');
	}
	const min = /^min\(([^()]*)\)$/.exec(text);
	const parts = min ? min[1].split(',') : [text];
	return Math.min(...parts.map((part) => parseFloat(part)));
}

/** The shared ceiling every floating list is held to, as the token layer declares it. */
const tokens = readFileSync(resolve('src/app.css'), 'utf8');
const SHARED = /--menu-max-height:\s*([^;]+);/.exec(tokens)?.[1];

describe('a menu longer than the room below the pointer', () => {
	it('is offered the whole window less the padding, not only the room under the pointer', async () => {
		const menu = await openNearTheBottom();
		/* Opened 100 px above the bottom edge, and offered the window's whole height: the library
		   may slide it up along the pointer's side, which is what it does with a menu this long. */
		const room = menu.parentElement?.style.getPropertyValue('--bits-floating-available-height');
		expect(room).toBe(`${WINDOW.height - 2 * 8}px`);
	});

	it('takes that room as its ceiling, so it opens whole rather than scrolling at the shared one', async () => {
		const menu = await openNearTheBottom();
		const vars = {
			'--bits-floating-available-height':
				menu.parentElement?.style.getPropertyValue('--bits-floating-available-height') ?? '',
			'--menu-max-height': SHARED ?? ''
		};
		const ceiling = resolved(getComputedStyle(menu).getPropertyValue('max-block-size'), vars);
		expect(ceiling).toBe(WINDOW.height - 2 * 8);
		expect(getComputedStyle(menu).getPropertyValue('grid-template-rows')).toBe('minmax(0, 1fr)');
	});
});
