/*
 * The one row of controls above every entity wall.
 *
 * What is tested is the shape: the box says which wall it filters, nothing is pressable beside it,
 * and Add names the thing it makes. A wall that went back to a Find button would still render and
 * pass every other test.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import WallControls from './WallControls.svelte';
import wallControlsSource from './WallControls.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { screenBar } from '$lib/components/shell/screen-bar.svelte';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	removeStyles();
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(props: Record<string, unknown>): void {
	drawn = mount(WallControls, {
		target: host,
		props: { noun: 'site', plural: 'sites', term: '', ...props }
	}) as Record<string, unknown>;
	flushSync();
}

function buttons(): string[] {
	/* Only the letters. The Add wears an icon, and an icon is a LIGATURE inside the button's own
	   text, so its glyph and the spacing around it are in `textContent` and `trim` does not touch
	   them. The same reading the repository's own note about anchored icon text arrived at. */
	return [...host.querySelectorAll('button')].map((one) =>
		(one.textContent ?? '')
			.replace(/[^\p{L}\p{N} ]/gu, '')
			.replace(/\s+/g, ' ')
			.trim()
	);
}

it('says which wall the box narrows', () => {
	draw({});
	expect(host.querySelector('input')?.getAttribute('placeholder')).toBe('Search sites');
});

it('has nothing to press beside the box', () => {
	/*
	 * An empty box: the cross inside it appears only with words to take away (see the cross,
	 * below), and there is no Find beside it.
	 */
	draw({});
	expect(buttons()).toEqual([]);
});

it('names the thing Add makes', () => {
	draw({ onadd: () => {} });
	expect(buttons()).toEqual(['Add site']);
});

it('tells the wall on every keystroke, so typing is what narrows', () => {
	const oninput = vi.fn();
	draw({ oninput });
	const box = host.querySelector('input') as HTMLInputElement;
	box.value = 'qu';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	expect(oninput).toHaveBeenCalled();
});

/*
 * The one settle timer every wall uses: the walls hand `onsettled` here rather than each keeping a
 * copy. Settled once typing pauses, trimmed; never written over words that reached the box some
 * other way in the pause.
 */
it('settles once typing pauses, and never over words that arrived in the pause', () => {
	vi.useFakeTimers();
	try {
		const onsettled = vi.fn();
		const held = $state({ term: '' });
		drawn = mount(WallControls, {
			target: host,
			props: {
				noun: 'site',
				plural: 'sites',
				get term() {
					return held.term;
				},
				set term(value: string) {
					held.term = value;
				},
				onsettled
			}
		}) as Record<string, unknown>;
		flushSync();
		const box = host.querySelector('input') as HTMLInputElement;

		box.value = 'qu ';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		vi.advanceTimersByTime(179);
		expect(onsettled).not.toHaveBeenCalled();
		vi.advanceTimersByTime(1);
		expect(onsettled).toHaveBeenCalledTimes(1);
		expect(onsettled).toHaveBeenCalledWith('qu');

		box.value = 'quo';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		held.term = 'from the address';
		flushSync();
		vi.advanceTimersByTime(500);
		expect(onsettled).toHaveBeenCalledTimes(1);
	} finally {
		vi.useRealTimers();
	}
});

it('opens the add screen when Add is pressed', () => {
	const onadd = vi.fn();
	draw({ onadd });
	(host.querySelector('button') as HTMLButtonElement).click();
	expect(onadd).toHaveBeenCalledOnce();
});

/*
 * The cross: the box is `NarrowBox`, so the walls get the same one Settings, Downloads and the
 * pickers have, and emptying it must filter the wall again, not leave the wall filtered under an
 * empty box.
 */
it('empties the box with the cross, and tells the wall', () => {
	const oninput = vi.fn();
	draw({ oninput, term: 'qu' });
	const cross = host.querySelector('button.field-clear') as HTMLButtonElement | null;
	expect(cross).not.toBeNull();

	cross?.click();
	flushSync();

	expect((host.querySelector('input') as HTMLInputElement).value).toBe('');
	expect(oninput).toHaveBeenCalledTimes(1);
});

it('empties the box on Escape, and tells the wall', () => {
	const oninput = vi.fn();
	draw({ oninput, term: 'qu' });
	const box = host.querySelector('input') as HTMLInputElement;

	box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
	flushSync();

	expect(box.value).toBe('');
	expect(oninput).toHaveBeenCalledTimes(1);
});

it('stands at the page-header height, beside the Add', () => {
	draw({ onadd: () => {} });
	expect(host.querySelector('input')?.classList.contains('medium')).toBe(true);
});

it('may narrow to ten letters on a short row, so a row of tabs beside it keeps one line', () => {
	draw({});
	applyStyles(wallControlsSource, host.querySelector('.wall-controls'));
	expect(getComputedStyle(host.querySelector('.find') as HTMLElement).minInlineSize).toBe(
		'var(--tab-box-floor)'
	);
});

/*
 * At a phone's width the Add keeps its place on the line and the box gives way.
 *
 * The unit environment answers only a plain `screen` rule, so the phone rule is read with its
 * condition swapped for it.
 */
it('keeps the Add whole on a phone, the box taking what it leaves', () => {
	draw({ onadd: () => {} });
	const row = host.querySelector('.wall-controls') as HTMLElement;
	const media = '@media (max-width: 767px)';
	expect(wallControlsSource, 'the row has no phone rule').toContain(media);
	applyStyles(wallControlsSource.replaceAll(media, '@media screen'), row);

	expect(getComputedStyle(row).flexGrow).toBe('1');
	const box = host.querySelector('.find') as HTMLElement;
	expect(getComputedStyle(box).minInlineSize).toBe('0px');
	expect(getComputedStyle(box).flexGrow).toBe('1');
	const add = [...row.children].find((one) => one.localName === 'button') as HTMLElement;
	expect(getComputedStyle(add).flexShrink).toBe('0');
});

it('tells the bar the screen has a box of its own while it is drawn, and takes it back when it goes', () => {
	/*
	 * The top search box follows `q`, and this box writes its words there. While this box is drawn
	 * the top box must read nothing from the address, or typing "nat" here writes "nat" there too.
	 */
	expect(screenBar.ownBox).toBe(false);
	draw({});
	expect(screenBar.ownBox).toBe(true);
	if (drawn) unmount(drawn);
	drawn = null;
	flushSync();
	expect(screenBar.ownBox).toBe(false);
});
