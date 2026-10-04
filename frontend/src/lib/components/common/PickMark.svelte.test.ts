import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import PickMark, { PICK_MARKS, type PickPurpose } from './PickMark.svelte';
import source from './PickMark.svelte?raw';

/*
 * The one look of a thing picked on a wall, whatever it is for: a wash over the picture that can
 * be found at a glance, and a filled mark in the middle saying what the pick is for.
 */

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	removeStyles();
});

function draw(purpose: PickPurpose): HTMLElement {
	host = document.createElement('div');
	host.style.position = 'relative';
	document.body.append(host);
	drawn = mount(PickMark, { target: host, props: { purpose } });
	flushSync();
	return host.querySelector('.pick') as HTMLElement;
}

describe('a pick on a wall', () => {
	it('covers the picture, takes no presses, and is not read out', () => {
		const pick = draw('swap');
		applyStyles(source, pick);
		const style = getComputedStyle(pick);
		expect(style.position).toBe('absolute');
		expect(style.pointerEvents).toBe('none');
		expect(pick.getAttribute('aria-hidden')).toBe('true');
	});

	it('washes the picture at a strength that can be found at a glance', () => {
		// The unit environment drops a `color-mix` it cannot compute, so the rule is read as written.
		expect(source).toMatch(/\.pick \{[^}]*background: var\(--sift-pick-wash\);/);
	});

	it('says what the pick is for with a filled mark, one for each purpose', () => {
		// Three purposes: a filter pick, a swap pick, and a thing that will not go in swap mode
		// (the same mark in the danger colour, so it is never also picked).
		expect(PICK_MARKS).toEqual({
			filter: 'filter_alt',
			swap: 'swap_horizontal_circle',
			refused: 'do_not_disturb_on'
		});
		for (const purpose of ['filter', 'swap', 'refused'] as const) {
			const pick = draw(purpose);
			expect(pick.dataset.purpose).toBe(purpose);
			const glyph = pick.querySelector('.mark .icon');
			expect(glyph?.classList.contains('filled')).toBe(true);
			expect(glyph?.classList.contains('size-34')).toBe(true);
			unmount(drawn!);
			drawn = undefined;
			host.remove();
		}
	});

	it('draws what will not go in the danger colour over its own wash', () => {
		expect(source).toMatch(
			/\.pick\[data-purpose='refused'\] \{[^}]*background: var\(--sift-refused-wash\);/
		);
		expect(source).toMatch(
			/\.pick\[data-purpose='refused'\] \.mark \{[^}]*color: var\(--sift-bad-text\);/
		);
	});
});
