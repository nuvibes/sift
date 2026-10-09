/* The hover card: the trigger stays the caller's element, the card is the shared floating
 * surface, portalled, and moved into a screen filling the window; library timing is not re-proved.
 * */
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
		// The trigger is the caller's own anchor, never wrapped.
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
		// Portalled out of any `overflow: hidden`.
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
		/* It outlives the close, held by the transition. */
		put();
		await hover();

		link().dispatchEvent(new MouseEvent('pointerleave', { bubbles: false }));
		flushSync();

		expect(document.querySelector('.preview')).not.toBeNull();
	});

	it('draws the card INSIDE a screen that is filling the window', async () => {
		// A filled screen paints only its subtree.
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
