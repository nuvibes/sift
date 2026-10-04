/*
 * The selection bar at a phone's width: four verbs and the door to the rest, never off the edge.
 *
 * Six named verbs in one sideways strip would run past the right edge of a phone's screen, so
 * everything after "Add to" would be reachable only by a sideways scroll nobody knows is there.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { words as readable } from '$lib/design/testing.svelte';
import Probe from './ActionBarPhoneProbe.test.svelte';
import { phoneWidth } from './phone-width.svelte';
import { barShape, PHONE_BAR_NAMED, type Verb } from './verbs';
import barSource from './ActionBar.svelte?raw';

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	phoneWidth.yes = false;
	document.body.innerHTML = '';
});

const run = () => {};
const named = (id: string, extra: Partial<Verb> = {}): Verb => ({
	id,
	label: id,
	icon: 'add',
	primary: true,
	run,
	...extra
});

describe('the split at a phone width', () => {
	const six = [
		named('one'),
		named('two'),
		named('three'),
		named('four'),
		named('five'),
		named('gone', { destructive: true }),
		{ id: 'quiet', label: 'quiet', icon: 'add', run } as Verb
	];

	it('names four, and the one that destroys something is one of them', () => {
		const shape = barShape(six, PHONE_BAR_NAMED);

		expect(shape.named.map((verb) => verb.id)).toEqual(['one', 'two', 'three', 'gone']);
		expect(shape.rest.map((verb) => verb.id).sort()).toEqual(['five', 'four', 'quiet']);
	});

	it('loses nothing: what it does not name is behind the door', () => {
		const shape = barShape(six, PHONE_BAR_NAMED);
		const reached = [...shape.named, ...shape.rest].map((verb) => verb.id).sort();

		expect(reached).toEqual(six.map((verb) => verb.id).sort());
	});

	it('reads the width itself, so no bar has to pass it', () => {
		expect(barShape(six).named).toHaveLength(6);
		phoneWidth.yes = true;
		expect(barShape(six).named).toHaveLength(PHONE_BAR_NAMED);
	});
});

function drawBar(): HTMLElement {
	const host = document.createElement('div');
	document.body.append(host);
	instance = mount(Probe, { target: host, props: {} });
	flushSync();
	const bar = host.querySelector<HTMLElement>('[role="region"][aria-label="Selection"]');
	if (!bar) throw new Error('no bar');
	return bar;
}

describe('the bar at a phone width', () => {
	it('draws the four as a row of columns that does not scroll, and More as the fifth', () => {
		phoneWidth.yes = true;
		const bar = drawBar();

		const columns = bar.querySelector('.columns');
		expect(columns, 'the verbs are still in the sideways strip').not.toBeNull();
		expect(bar.querySelector('[data-scroll-area-viewport]')).toBeNull();
		const words = [...columns!.querySelectorAll('button')].map((button) => readable(button));
		expect(words).toEqual(['Add to', 'Move', 'Rename', 'Remove']);

		const more = bar.querySelector<HTMLButtonElement>('.more button');
		expect(more?.getAttribute('aria-label')).toBe('More for 3 files');
		expect(readable(more!), 'the door is the one column with no word').toBe('More');
	});

	it('draws a group as a column like the rest, not as the worded dropdown button', () => {
		phoneWidth.yes = true;
		const add = [...drawBar().querySelectorAll<HTMLButtonElement>('.columns button')].find(
			(button) => readable(button) === 'Add to'
		);

		expect(add, 'no Add to column').toBeTruthy();
		expect(add!.classList.contains('btn'), 'the group is still the worded Button').toBe(false);
		expect(add!.firstElementChild?.classList.contains('icon'), 'no glyph over the word').toBe(true);
	});

	it('opens the rest as a sheet', async () => {
		phoneWidth.yes = true;
		const more = drawBar().querySelector<HTMLButtonElement>('.more button')!;
		for (const type of ['pointerdown', 'pointerup']) {
			const event = new PointerEvent(type, { bubbles: true, cancelable: true, button: 0 });
			Object.defineProperty(event, 'pointerType', { value: 'touch' });
			more.dispatchEvent(event);
		}
		flushSync();

		await vi.waitFor(() => expect(document.querySelector('.ui-menu.menu-sheet')).toBeTruthy());
		const rows = [...document.querySelectorAll('.ui-menu.menu-sheet [role="menuitem"]')].map(
			(row) => readable(row as HTMLElement)
		);
		expect(rows).toEqual(expect.arrayContaining(['Copy link', 'Enrich', 'Share']));
	});

	it('is two lines across the whole width, above the docked corner player', () => {
		const phone = barSource.slice(barSource.indexOf('@media (max-width: 767px)'));
		expect(phone).toMatch(/inset-inline: var\(--space-2\);/);
		expect(phone).toMatch(/translate: none;/);
		expect(phone).toMatch(/var\(--mini-docked, 0px\)/);
		expect(phone).toMatch(/'clear count all'\s*'verbs verbs verbs'/);
	});

	it('makes the four verbs and the door one row of equal columns, whatever wraps each', () => {
		/* As a shared flex the columns come out unequal: the rating's chooser wraps its button in an
		   element with no padding of its own, a flex share starts from the padding, and a door in a
		   track of its own width is its own width. */
		phoneWidth.yes = true;
		const bar = drawBar();
		const line = bar.querySelector('.line');
		expect(line, 'no row holding the verbs and the door').not.toBeNull();
		expect(line!.querySelector(':scope > .columns')).not.toBeNull();
		expect(
			line!.querySelector(':scope > .more'),
			"the door is not in the verbs' row"
		).not.toBeNull();

		const phone = barSource.slice(barSource.indexOf('@media (max-width: 767px)'));
		expect(phone).toMatch(
			/\.line \{\s*grid-area: verbs;\s*display: grid;\s*grid-auto-flow: column;\s*grid-auto-columns: minmax\(0, 1fr\);/
		);
		expect(phone).toMatch(/\.line > \.columns \{\s*display: contents;/);
	});

	it('keeps the wide bar as it was on a wide window', () => {
		const bar = drawBar();

		expect(bar.querySelector('.columns')).toBeNull();
		expect(bar.querySelectorAll('.actions > button, .actions button').length).toBeGreaterThan(4);
	});
});
