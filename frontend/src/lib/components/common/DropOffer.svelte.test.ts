/*
 * The whole-window offer to take a drop. What is held: it draws nothing until shown; shown, it says
 * where the thing goes, lets the pointer through to the page (a drag is in flight), and stays out
 * of the accessibility tree.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import DropOffer from './DropOffer.svelte';
import source from './DropOffer.svelte?raw';

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	removeStyles();
	document.body.innerHTML = '';
});

function draw(shown: boolean): HTMLElement {
	const host = document.createElement('div');
	document.body.append(host);
	instance = mount(DropOffer, { target: host, props: { shown, words: 'Drop to add' } });
	flushSync();
	return host;
}

describe('the drop offer', () => {
	it('draws nothing while nothing is held over the window', () => {
		expect(draw(false).querySelector('.overlay')).toBeNull();
	});

	it('says where the thing goes, and lets the drop through to the page', () => {
		const host = draw(true);
		const overlay = host.querySelector<HTMLElement>('.overlay');
		expect(overlay?.textContent?.trim()).toBe('Drop to add');
		expect(overlay?.getAttribute('aria-hidden')).toBe('true');

		applyStyles(source, overlay);
		const style = getComputedStyle(overlay!);
		expect(style.pointerEvents).toBe('none');
		expect(style.position).toBe('fixed');
	});
});
