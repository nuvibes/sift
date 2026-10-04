/*
 * The card that opens on a hovered link.
 *
 * What is worth asserting here is the part that is THIS component's rather than the library's: that
 * the trigger stays the caller's own element, that the card is drawn on the app's shared floating
 * surface, that it is portalled out of whatever it was written inside, and that a screen FILLING the
 * window moves it rather than leaving it in a document the browser has stopped painting.
 *
 * The hover delay, the safe travel into the card and the focus handling are the library's, are
 * timers with no layout under them in jsdom, and are deliberately not re-proved here.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import Probe from './LinkPreviewProbe.test.svelte';

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true });
});

function put(): HTMLElement {
	const host = document.createElement('div');
	document.body.appendChild(host);
	instance = mount(Probe, { target: host });
	flushSync();
	return host;
}

function link(): HTMLAnchorElement {
	const found = document.querySelector('a.who');
	if (!(found instanceof HTMLAnchorElement)) throw new Error('the trigger is not there');
	return found;
}

/** Rest on it. A mouse event rather than a touch one: the library ignores a touch hover. */
async function hover(): Promise<void> {
	link().dispatchEvent(new MouseEvent('pointerenter', { bubbles: false }));
	await vi.waitFor(() => {
		flushSync();
		if (!document.querySelector('.preview')) throw new Error('no card yet');
	});
}

describe('the card on a hovered link', () => {
	it('leaves the trigger as the element the caller wrote', () => {
		// The whole reason the trigger is a snippet handed `props` rather than a wrapper: the thing
		// being hovered is nearly always an anchor somebody has already written, with its own class
		// and its own address, and wrapping it would put a box in the middle of their layout.
		put();

		expect(link().getAttribute('href')).toBe('/people/example');
		expect(link().className).toContain('who');
		expect(link().hasAttribute('data-link-preview-trigger')).toBe(true);
	});

	it('does not draw the card until the link is hovered', () => {
		put();

		expect(document.querySelector('.preview')).toBeNull();
	});

	it('draws the card on the shared floating surface, portalled out of its caller', async () => {
		const host = put();
		await hover();

		const card = document.querySelector('.preview');
		if (!(card instanceof HTMLElement)) throw new Error('there is no card');
		expect(card.textContent).toContain('Four files');
		// Out of the box it was written in, or every `overflow: hidden` between here and the trigger
		// would clip it, which is the fault portalling exists for.
		expect(host.contains(card)).toBe(false);
		expect(document.body.contains(card)).toBe(true);
	});

	it('tells a caller when it opens, so the caller can fetch what goes in it', async () => {
		const told = vi.fn();
		const host = document.createElement('div');
		document.body.appendChild(host);
		instance = mount(Probe, { target: host, props: { onOpenChange: told } });
		flushSync();

		await hover();

		expect(told).toHaveBeenCalledWith(true);
	});

	it('keeps the card on the page while it is leaving, so it can be seen going', async () => {
		/*
		 * The half a CSS animation could never do. The card is force-mounted and the branch that
		 * draws it is this component's own, which puts it under a transition, the one mechanism
		 * that holds an element on the page long enough to play its way out.
		 *
		 * Asserted as "still there a tick after the pointer left" rather than by measuring
		 * movement: jsdom has no layout and runs no frames, so what is provable is that the element
		 * outlives the close.
		 */
		put();
		await hover();

		link().dispatchEvent(new MouseEvent('pointerleave', { bubbles: false }));
		flushSync();

		expect(document.querySelector('.preview')).not.toBeNull();
	});

	it('draws the card INSIDE a screen that is filling the window', async () => {
		// A browser filling the screen paints that element and its subtree and nothing else in the
		// document. A card at the end of `body` is then not hidden: it is not drawn at all, which
		// reads as the card simply never appearing on a filled wall.
		const filled = document.createElement('div');
		document.body.appendChild(filled);
		Object.defineProperty(document, 'fullscreenElement', { value: filled, configurable: true });

		put();
		await hover();

		const card = document.querySelector('.preview');
		if (!(card instanceof HTMLElement)) throw new Error('there is no card');
		expect(filled.contains(card)).toBe(true);
	});
});
