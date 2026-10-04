import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import Select from './Select.svelte';
import { fromBar } from '$lib/shell/motion.svelte';

/* The bar's motion, watched rather than replaced, so a list from the top bar can be seen asking
   for it. */
vi.mock('$lib/shell/motion.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/shell/motion.svelte')>();
	return { ...real, fromBar: vi.fn(real.fromBar) };
});
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import source from './Select.svelte?raw';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { SelectOption } from './Select.svelte';

/* An option that means "no answer".
 *
 * Written as the empty string by every caller that has one ("Any sharing", "Follow the default",
 * "Default download folder") because that is the natural way to say it. The underlying Select
 * reads "" as NOTHING CHOSEN rather than as a choice, so left alone the control would come up blank
 * and could not be moved back onto that option once something else was in it. Both halves are
 * asserted here: what
 * the control says, and what the caller is told when the option is picked.
 */

let showing: Record<string, unknown> | null = null;
let host: HTMLElement;

const OPTIONS = [
	{ value: '', label: 'Follow the default' },
	{ value: 't1', label: 'Sweden' }
];

function show(props: Record<string, unknown>) {
	host = document.createElement('div');
	document.body.append(host);
	showing = mount(Select, {
		target: host,
		props: { options: OPTIONS, label: 'Way out', ...props }
	});
	flushSync();
}

/** The whole document, because the open list is portalled out of the host. */
function everything(): string {
	return document.body.textContent ?? '';
}

/* jsdom has no pointer capture, and the primitive releases it on the way down. Absent here it
   throws mid-handler and the menu never opens: a gap in the test environment, not in the app. */
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

function open() {
	// The primitive opens on pointerdown, not on click, so a plain click() opens nothing.
	const trigger = host.querySelector<HTMLElement>('.ui-select');
	trigger?.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
	trigger?.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
	trigger?.click();
	flushSync();
}

function pick(label: string) {
	const item = [...document.querySelectorAll<HTMLElement>('.ui-select-item')].find(
		(one) => one.textContent?.trim() === label
	);
	expect(item, `no row labelled ${label}`).toBeTruthy();
	item?.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
	item?.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
	item?.click();
	flushSync();
}

afterEach(() => {
	if (showing) unmount(showing);
	showing = null;
	host?.remove();
	document.body.innerHTML = '';
});

describe('the option meaning no answer', () => {
	it('says its label rather than drawing a blank control', () => {
		show({ value: '' });

		expect(host.textContent).toContain('Follow the default');
	});

	it('is offered in the list beside the real choices', () => {
		show({ value: 't1' });
		open();

		expect(everything()).toContain('Follow the default');
		expect(everything()).toContain('Sweden');
	});

	it('tells the caller the empty string, never the stand-in it uses inside', () => {
		// The stand-in exists only to get past the primitive. A caller seeing it would store it, and
		// a stored stand-in is a route, a filter or a folder that nothing on the server knows.
		let told: string | null = null;
		show({ value: 't1', onValueChange: (next: string) => (told = next) });
		open();
		pick('Follow the default');

		expect(told).toBe('');
	});

	it('moves the control off what was chosen before', () => {
		show({ value: 't1' });
		open();
		pick('Follow the default');

		expect(host.textContent).toContain('Follow the default');
		expect(host.textContent).not.toContain('Sweden');
	});
});

describe('an empty value with no option answering to it', () => {
	/*
	 * The other reading of "". A caller whose list holds no empty-valued option and who passes ""
	 * means "nothing picked yet", so the trigger must draw the placeholder rather than an unmatched
	 * value (the stand-in is a sentence, which would otherwise reach the screen).
	 */
	const REAL_ONLY = [
		{ value: 't1', label: 'Sweden' },
		{ value: 't2', label: 'Norway' }
	];

	it('draws the placeholder rather than an internal stand-in', () => {
		show({ options: REAL_ONLY, value: '', placeholder: 'Pick a country' });

		expect(host.textContent).toContain('Pick a country');
		expect(everything()).not.toContain('no answer was chosen');
	});

	it('never puts the stand-in on screen, placeholder or not', () => {
		show({ options: REAL_ONLY, value: '' });
		open();

		expect(everything()).not.toContain('no answer was chosen');
	});
});

describe('an ordinary option', () => {
	it('is reported by its own value', () => {
		let told: string | null = null;
		show({ value: '', onValueChange: (next: string) => (told = next) });
		open();
		pick('Sweden');

		expect(told).toBe('t1');
		expect(host.textContent).toContain('Sweden');
	});
});

describe('a list hanging from the top bar', () => {
	/* Sort by and a screen's own menus come out from under the bar as the Filter panel does, and
	   go back up under it, rather than rising as a small surface and vanishing. */
	it('comes out from under the bar, and still chooses', () => {
		vi.mocked(fromBar).mockClear();
		let told: string | null = null;
		show({ value: '', fromTheBar: true, onValueChange: (next: string) => (told = next) });
		open();

		expect(document.querySelector('.ui-select-content.from-bar')).not.toBeNull();
		expect(vi.mocked(fromBar)).toHaveBeenCalled();
		pick('Sweden');
		expect(told).toBe('t1');
	});

	it('and a list anywhere else rises as a small surface', () => {
		vi.mocked(fromBar).mockClear();
		show({ value: '' });
		open();

		expect(document.querySelector('.ui-select-content')).not.toBeNull();
		expect(document.querySelector('.ui-select-content.from-bar')).toBeNull();
		expect(vi.mocked(fromBar)).not.toHaveBeenCalled();
	});
});

describe('the box that narrows a long list', () => {
	/*
	 * Not opt-in: how long the list is is a fact about the OPTIONS, and a caller handing over
	 * twenty download folders has no reason to think about it. The list decides, and a caller may
	 * only say NO. See the box's own note in `Select.svelte`.
	 */
	function many(count: number) {
		return Array.from({ length: count }, (_, index) => ({
			value: `f${index}`,
			label: `Folder ${index}`
		}));
	}

	function narrower(): HTMLInputElement | null {
		return document.querySelector('.ui-select-filter input');
	}

	it('appears on its own once the list is longer than the threshold', () => {
		show({ options: many(11), value: 'f0' });
		open();

		expect(narrower()).toBeTruthy();
	});

	it('stays away on a list short enough to read', () => {
		show({ options: many(10), value: 'f0' });
		open();

		expect(narrower()).toBeNull();
	});

	/*
	 * `searchable` cannot force the box on: whether reading is slower than typing is a fact about
	 * how many rows there are, which the component knows. So a short list gets no box even when a
	 * caller asks for one.
	 */
	it('is NOT drawn on a short list even where the caller asks for it', () => {
		show({ options: many(3), value: 'f0', searchable: true });
		open();

		expect(narrower()).toBeNull();
	});

	it('is kept away where the caller says so, however long the list is', () => {
		show({ options: many(40), value: 'f0', searchable: false });
		open();

		expect(narrower()).toBeNull();
	});

	it('narrows the rows to what was typed', () => {
		show({ options: many(11), value: 'f0' });
		open();

		const box = narrower();
		expect(box).toBeTruthy();
		if (box) {
			box.value = 'Folder 7';
			box.dispatchEvent(new Event('input', { bubbles: true }));
			flushSync();
		}

		const rows = [...document.querySelectorAll('.ui-select-item')].map((one) =>
			one.textContent?.trim()
		);
		expect(rows).toEqual(['Folder 7']);
	});

	/*
	 * Escape, twice, in this order. The first press takes the words away and the list stays up with
	 * every row back; the second finds the box empty and closes the list.
	 */
	it('empties the box on the first Escape and closes the list on the second', () => {
		show({ options: many(11), value: 'f0' });
		open();

		const box = narrower();
		expect(box).toBeTruthy();
		if (!box) return;
		box.focus();
		box.value = 'Folder 7';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		const escape = () => {
			const target = narrower();
			target?.dispatchEvent(
				new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
			);
			flushSync();
		};
		const expanded = () =>
			host.querySelector('.ui-select')?.getAttribute('aria-expanded') ?? 'missing';

		escape();
		expect(narrower()?.value).toBe('');
		expect(document.querySelectorAll('.ui-select-item')).toHaveLength(11);
		expect(expanded()).toBe('true');

		escape();
		expect(expanded()).toBe('false');
	});
});

/*
 * A list longer than the box has to scroll, and has to say that it can.
 *
 * The open list is a flex column (the library writes that inline), and the scrolling region inside
 * must not be sized by a percentage of an indefinite height, or it grows to the whole list, the
 * library mounts no scrollbar, and the wheel moves nothing.
 *
 * There is no layout in this harness, so what is asserted is the two structural facts: the rows are
 * inside the shared scrolling region, and the region grows an arrow at whichever end it can still
 * travel to.
 */
describe('a list longer than the box', () => {
	function many(count: number) {
		return Array.from({ length: count }, (_, index) => ({
			value: `f${index}`,
			label: `Folder ${index}`
		}));
	}

	/** The element that actually scrolls, and the numbers a box with more list than room reports. */
	function theScrollingBox(): HTMLElement {
		const box = document.querySelector<HTMLElement>(
			'.ui-select-content [data-scroll-area-viewport]'
		);
		expect(box, 'the rows are not inside the shared scrolling region').toBeTruthy();
		return box as HTMLElement;
	}

	function measuring(box: HTMLElement, at: number) {
		for (const [name, value] of [
			['scrollHeight', 400],
			['clientHeight', 200],
			['scrollTop', at]
		] as const) {
			Object.defineProperty(box, name, { value, configurable: true });
		}
		box.dispatchEvent(new Event('scroll'));
		flushSync();
	}

	/* Which end each arrow is at, by its place in the region rather than by its glyph: an icon is a
	   ligature, so the element's text is a private-use codepoint and not the name of the arrow. */
	function arrows(): string[] {
		const root = document.querySelector('.ui-select-content .scroll-root');
		const children = [...(root?.children ?? [])];
		const viewport = children.findIndex((one) => one.hasAttribute('data-scroll-area-viewport'));
		return children
			.filter((one) => one.classList.contains('scroll-nudge'))
			.map((one) => (children.indexOf(one) < viewport ? 'up' : 'down'));
	}

	it('puts its rows in the shared scrolling region', () => {
		show({ options: many(40), value: 'f0' });
		open();

		expect(theScrollingBox().querySelectorAll('.ui-select-item').length).toBeGreaterThan(0);
	});

	it('offers a way down at the top and a way up at the bottom', () => {
		show({ options: many(40), value: 'f0' });
		open();
		const box = theScrollingBox();

		measuring(box, 0);
		expect(arrows()).toEqual(['down']);

		measuring(box, 100);
		expect(arrows()).toEqual(['up', 'down']);

		measuring(box, 200);
		expect(arrows()).toEqual(['up']);
	});

	it('offers neither on a list that fits', () => {
		show({ options: many(40), value: 'f0' });
		open();
		const box = theScrollingBox();

		Object.defineProperty(box, 'scrollHeight', { value: 200, configurable: true });
		Object.defineProperty(box, 'clientHeight', { value: 200, configurable: true });
		box.dispatchEvent(new Event('scroll'));
		flushSync();

		expect(arrows()).toEqual([]);
	});
});

describe('a list whose names repeat', () => {
	it('draws the quiet phrase that tells two rows apart', () => {
		show({
			options: [
				{ value: '1', label: 'Images', detail: 'Juniper/2025' },
				{ value: '2', label: 'Images', detail: 'Kestrel' }
			]
		});
		open();

		const details = [...document.querySelectorAll('.ui-select-item-detail')].map((one) =>
			one.textContent?.trim()
		);
		expect(details).toEqual(['Juniper/2025', 'Kestrel']);
		// The label is still the label: the phrase is beside the name, never part of it.
		const labels = [...document.querySelectorAll('.ui-select-item-label')].map((one) =>
			one.textContent?.trim()
		);
		expect(labels).toEqual(['Images', 'Images']);
	});

	it('draws no phrase at all for an option that has none', () => {
		show({ options: [{ value: '1', label: 'Sift Downloads' }] });
		open();

		expect(document.querySelectorAll('.ui-select-item-detail')).toHaveLength(0);
	});
});

describe('a row with a second line', () => {
	it('draws the note under the name, as its own line, and never beside it', () => {
		show({
			options: [
				{ value: 'a', label: 'Newest first' },
				{ value: 'b', label: 'Similarity', note: 'needs a file to compare with', disabled: true }
			]
		});
		open();

		const notes = [...document.querySelectorAll('.ui-select-item-note')];
		expect(notes.map((one) => one.textContent?.trim())).toEqual(['needs a file to compare with']);
		const text = notes[0]?.parentElement;
		// Stacked: the name first, the note after it, in a column of their own.
		expect(text?.classList.contains('noted')).toBe(true);
		expect(text?.firstElementChild?.textContent?.trim()).toBe('Similarity');
		expect(text?.lastElementChild).toBe(notes[0]);
		expect(document.querySelectorAll('.ui-select-item-detail')).toHaveLength(0);
	});

	it('takes the row width and adds none, so the list is one width whichever rows carry a note', () => {
		/* Drawn to its own width the note would widen the Sort menu by half again on the wall where
		   Similarity is dimmed. jsdom lays nothing out, so the rule that prevents it is read. */
		const rule = /\.ui-select-item-note\s*\{([^}]*)\}/.exec(source)?.[1] ?? '';
		expect(rule).toMatch(/inline-size:\s*0;/);
		expect(rule).toMatch(/min-inline-size:\s*100%;/);
		expect(rule).toMatch(/white-space:\s*normal;/);
	});

	it('hangs from the top bar at the bar width, so a note keeps to one line on every wall', () => {
		/* Sized by its widest name the Sort list would be so narrow that Similarity's reason wraps
		   to two lines and Random goes under the scroll. The floor is the token, not the names. */
		const rule = /:global\(\.ui-select-content\.from-bar\)\s*\{([^}]*)\}/.exec(source)?.[1] ?? '';
		expect(rule).toMatch(/min-inline-size:\s*max\([^;]*var\(--menu-bar-width\)\);/);
		const tokens = readFileSync(
			resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'app.css'),
			'utf8'
		);
		expect(tokens).toMatch(/--menu-bar-width:\s*240px;/);
	});
});

describe('a list whose words have to be looked at rather than read', () => {
	/* The appearance pane's two font menus. A list of typeface names set in one face tells you
	   what they are called, which the reader already had; what the choice is about is what each one
	   looks like. The caller draws the words, and the TRIGGER has to draw them the same way or the
	   closed control and the open row say different things about one answer. */
	const optionLabel = createRawSnippet((option: () => SelectOption) => ({
		render: () => `<span class="dressed">${option().label}</span>`
	}));

	it("draws the caller's own words on every row", () => {
		show({ optionLabel });
		open();

		const dressed = [...document.querySelectorAll('.ui-select-item .dressed')].map((one) =>
			one.textContent?.trim()
		);
		expect(dressed).toEqual(['Follow the default', 'Sweden']);
	});

	it('draws them on the trigger as well, for the option that is chosen', () => {
		show({ optionLabel, value: 't1' });

		expect(host.querySelector('.ui-select-value .dressed')?.textContent?.trim()).toBe('Sweden');
	});

	it('leaves the trigger to the primitive while nothing is chosen', () => {
		// There is no chosen option to hand a snippet, and the placeholder is the primitive's answer.
		show({ optionLabel, value: undefined, placeholder: 'Pick one' });

		expect(host.querySelector('.ui-select-value .dressed')).toBeNull();
		expect(host.querySelector('.ui-select-value')?.textContent?.trim()).toBe('Pick one');
	});
});

/*
 * A row that is a press rather than an answer: "Shuffle again" on the sort menu. As an ordinary
 * option the first press would make it the chosen value, and the primitive will not deliver a row
 * that is already the value, so every later press would be swallowed.
 *
 * Asserted three presses deep, because two could pass on a control that merely alternates.
 */
describe('a row that is a press rather than an answer', () => {
	const WITH_A_PRESS: SelectOption[] = [
		{ value: 'newest', label: 'Newest first' },
		{ value: 'random', label: 'Random' },
		{ value: 'again', label: 'Shuffle again', action: true }
	];

	function withAPress(props: Record<string, unknown> = {}) {
		show({ options: WITH_A_PRESS, value: 'random', ...props });
	}

	it('fires every time it is pressed, not only the first', () => {
		const pressed: string[] = [];
		withAPress({ onAction: (next: string) => pressed.push(next) });

		for (let time = 0; time < 3; time++) {
			open();
			pick('Shuffle again');
		}

		expect(pressed).toEqual(['again', 'again', 'again']);
	});

	it('leaves the chosen value where it was, so the control still says it', () => {
		const chosen: string[] = [];
		withAPress({ onValueChange: (next: string) => chosen.push(next) });
		open();
		pick('Shuffle again');

		// A press is not a value: a caller told it there would store it, and nothing may store it.
		expect(chosen).toEqual([]);
		expect(host.textContent).toContain('Random');
		expect(host.textContent).not.toContain('Shuffle again');
	});

	it('never wears the tick, however often it is pressed', () => {
		withAPress();
		open();
		pick('Shuffle again');
		open();

		const marked = [...document.querySelectorAll('.ui-select-item')]
			.filter((one) => one.hasAttribute('data-selected'))
			.map((one) => one.getAttribute('data-value'));
		expect(marked).toEqual(['random']);
	});

	it('leaves an ordinary row on the same list working as it always did', () => {
		const chosen: string[] = [];
		withAPress({ onValueChange: (next: string) => chosen.push(next) });
		open();
		pick('Newest first');

		expect(chosen).toEqual(['newest']);
	});
});

describe('the width of the closed control', () => {
	afterEach(removeStyles);

	const sizers = () =>
		[...host.querySelectorAll<HTMLElement>('.ui-select-sizer')].map((one) => ({
			words: one.dataset.words,
			hidden: one.getAttribute('aria-hidden')
		}));

	it('holds every answer unseen beside the chosen one, so the widest decides it', () => {
		show({
			options: [
				{ value: 'fast', label: 'Fast' },
				{ value: 'thorough', label: 'Thorough' },
				{ value: 'again', label: 'Shuffle again', action: true }
			],
			value: 'fast',
			placeholder: 'Pick one'
		});

		// A press never becomes the value, so it never decides the width.
		expect(sizers()).toEqual([
			{ words: 'Fast', hidden: 'true' },
			{ words: 'Thorough', hidden: 'true' },
			{ words: 'Pick one', hidden: 'true' }
		]);
		expect(host.querySelector('.ui-select-value')?.textContent?.trim()).toBe('Fast');
		// Drawn, not written: the trigger's text is still only the answer it shows.
		expect(host.querySelector('.ui-select')?.textContent).not.toContain('Thorough');
	});

	/* A column of choosers is one width, the widest answer any of them has, or the widest pushes
	   the press beside it (Run now) onto a second line. */
	it('is as wide as the words a column of choosers hands it, as well as its own', () => {
		show({
			options: [
				{ value: 'fast', label: 'Fast' },
				{ value: 'thorough', label: 'Thorough' }
			],
			value: 'fast',
			sizeTo: ['Every week on a quiet night', 'Fast', 'Every week on a quiet night']
		});

		expect(sizers().map((one) => one.words)).toEqual([
			'Fast',
			'Thorough',
			'Every week on a quiet night',
			'Fast'
		]);
		expect(sizers().every((one) => one.hidden === 'true')).toBe(true);
		expect(host.querySelector('.ui-select')?.textContent).not.toContain('quiet night');
	});

	it('stacks them in one cell, unseen, and never wider than the place it sits', () => {
		show({ value: 't1' });
		const trigger = host.querySelector<HTMLElement>('.ui-select')!;
		applyStyles(source, host.querySelector('.ui-select-answers'));

		expect(getComputedStyle(trigger).maxInlineSize).toBe('100%');
		expect(getComputedStyle(trigger).inlineSize).toBe('auto');
		expect(getComputedStyle(host.querySelector('.ui-select-answers')!).display).toBe('grid');
		const sizer = host.querySelector<HTMLElement>('.ui-select-sizer')!;
		expect(getComputedStyle(sizer).visibility).toBe('hidden');
		expect(getComputedStyle(sizer).gridArea).toContain('1 / 1');
		expect(getComputedStyle(host.querySelector('.ui-select-value')!).gridArea).toContain('1 / 1');
	});

	it('is the field height, with no block padding to push it past a press beside it', () => {
		show({ value: 't1' });
		const trigger = host.querySelector<HTMLElement>('.ui-select')!;
		applyStyles(source, host.querySelector('.ui-select-answers'));

		const drawn = getComputedStyle(trigger);
		expect(drawn.minBlockSize).toBe('var(--control-height)');
		expect(drawn.paddingTop).toBe('0');
		expect(drawn.paddingBottom).toBe('0');
	});

	it('sizes a long list by its chosen answer rather than a hidden copy of every row', () => {
		const many = Array.from({ length: 11 }, (_, at) => ({ value: `v${at}`, label: `Row ${at}` }));
		show({ options: many, value: 'v0' });

		expect(host.querySelectorAll('.ui-select-sizer')).toHaveLength(0);
	});
});

describe('a chooser a form refuses', () => {
	/* The red border is keyed on `data-invalid`, so a trigger saying only `aria-invalid` would
	   never redden when its Field refused it: the sentence under it would be the only sign. */
	const RULE =
		/:global\((\.ui-select\[data-invalid\])\)\s*\{[^}]*border-color:\s*var\(--sift-bad-text\)/;

	it('wears the mark its red border is drawn by', () => {
		const selector = RULE.exec(source)?.[1];
		expect(selector, 'the red border rule').toBeTruthy();
		show({ value: 't1', invalid: true });
		const trigger = host.querySelector<HTMLElement>('.ui-select');
		expect(trigger?.matches(selector as string)).toBe(true);
		expect(trigger?.getAttribute('aria-invalid')).toBe('true');
	});

	it('and an accepted one does not', () => {
		show({ value: 't1' });
		const trigger = host.querySelector<HTMLElement>('.ui-select');
		expect(trigger?.hasAttribute('data-invalid')).toBe(false);
		expect(trigger?.hasAttribute('aria-invalid')).toBe(false);
	});
});
