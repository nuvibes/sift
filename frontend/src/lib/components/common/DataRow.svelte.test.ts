import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount } from 'svelte';
import DataRow from './DataRow.svelte';
import { COLUMNS, type Declared } from './DataRows.svelte';
import SOURCE from './DataRow.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import type { Verb } from './verbs';

/* What a plain press on a row means, and what it deliberately does not mean.
 *
 * The row carries three gestures that all look alike from the outside (a press, a right-click and
 * the dots), and two of them doing the same thing while the third does the one anybody wanted
 * is easy to arrive at. The rule is worth a test of its own because nothing about that arrangement looks
 * broken: every gesture works, they simply lead to the wrong places.
 */

const children = createRawSnippet(() => ({
	render: () => `<span>Pictures <button type="button" class="inside">Rename</button></span>`
}));

const verbs: readonly Verb[] = [{ id: 'share', label: 'Share', icon: 'group', run: () => {} }];

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function render(props: Record<string, unknown>) {
	host = document.createElement('ul');
	document.body.append(host);
	mount(DataRow, { target: host, props: { children, ...props } });
	// Mounting queues the effects that attach the handlers. Without this the row is on screen and
	// is not listening yet, which looks exactly like a row that ignores a press.
	flushSync();
	return host.querySelector('.row') as HTMLElement;
}

describe('a row that was given something for a press to do', () => {
	it('does it when the press lands on the row itself', () => {
		const pressed = vi.fn();
		const row = render({ onpress: pressed });

		row.click();

		expect(pressed).toHaveBeenCalledTimes(1);
	});

	it('leaves a press on a control inside the row to that control', () => {
		const pressed = vi.fn();
		const row = render({ onpress: pressed });

		(row.querySelector('button.inside') as HTMLElement).click();

		expect(pressed).not.toHaveBeenCalled();
	});

	it('answers Enter and Space, so the keyboard reaches the same act', () => {
		const pressed = vi.fn();
		const row = render({ onpress: pressed });

		for (const key of ['Enter', ' ']) {
			row.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
		}

		expect(pressed).toHaveBeenCalledTimes(2);
	});

	it('ignores a key that is not one of those two', () => {
		const pressed = vi.fn();
		const row = render({ onpress: pressed });

		row.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }));

		expect(pressed).not.toHaveBeenCalled();
	});

	it('is a tab stop, because a press nobody can reach by keyboard is half a control', () => {
		expect(render({ onpress: () => {} }).tabIndex).toBe(0);
	});

	it('does not open its own verb menu: that is the right button and the dots', () => {
		const row = render({ onpress: () => {}, verbs, ids: ['one'], menuLabel: 'More for Pictures' });

		row.click();
		flushSync();

		expect(document.querySelector('[role="menu"]')).toBeNull();
	});
});

describe('a row that was given nothing for a press to do', () => {
	it('is not a tab stop and is not drawn as pressable', () => {
		const row = render({});

		expect(row.tabIndex).toBe(-1);
		expect(row.classList.contains('pressable')).toBe(false);
	});
});

describe('a row given its own keys (onkeys)', () => {
	/*
	 * The keys a caller hands the row are heard wherever the keyboard sits in it, including the
	 * row's own tab stop where Tab lands first, not only on one of the caller's elements.
	 */
	const field = createRawSnippet(() => ({
		render: () =>
			`<span>Pictures <button type="button" class="inside">Rename</button><input class="name" /></span>`
	}));

	function key(target: Element, name: string) {
		target.dispatchEvent(
			new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true })
		);
	}

	it('hears a key typed on the row itself', () => {
		const keys = vi.fn();
		const row = render({ onkeys: keys });

		key(row, 'Delete');

		expect(keys).toHaveBeenCalledTimes(1);
		expect(keys.mock.calls[0][0].key).toBe('Delete');
	});

	it('hears one typed on a control inside the row', () => {
		const keys = vi.fn();
		const row = render({ onkeys: keys });

		key(row.querySelector('button.inside') as Element, 'p');

		expect(keys).toHaveBeenCalledTimes(1);
	});

	it('does not hear a letter written into a text field inside the row', () => {
		const keys = vi.fn();
		const row = render({ onkeys: keys, children: field });

		key(row.querySelector('input.name') as Element, 'p');

		expect(keys).not.toHaveBeenCalled();
	});

	it('makes the row a tab stop even with nothing for a press to do', () => {
		expect(render({ onkeys: () => {} }).tabIndex).toBe(0);
	});

	it('keeps Enter from opening the row when it takes the key itself', () => {
		const pressed = vi.fn();
		const row = render({
			onpress: pressed,
			onkeys: (event: KeyboardEvent) => event.preventDefault()
		});

		key(row, 'Enter');

		expect(pressed).not.toHaveBeenCalled();
	});

	it('leaves Enter to the press when it does not take it', () => {
		const pressed = vi.fn();
		const row = render({ onpress: pressed, onkeys: () => {} });

		key(row, 'Enter');

		expect(pressed).toHaveBeenCalledTimes(1);
	});
});

/* ------------------------------------------------------------------------------------------------
 * THE ARROW, in one place for every list that folds: the end of the row, always visible.
 * ---------------------------------------------------------------------------------------------- */

describe('a row that folds', () => {
	const arrow = (row: HTMLElement) => row.querySelector<HTMLButtonElement>('.disclose button');

	it('draws its arrow as the LAST thing on the row, after the figures and the actions', () => {
		const row = render({
			expanded: false,
			ontoggle: () => {},
			toggleLabel: 'the steps of clip.mp4',
			figure: '84.2 MB',
			verbs
		});

		const last = row.lastElementChild as HTMLElement;
		expect(last.classList.contains('disclose'), 'the arrow is not at the end').toBe(true);
		// The END in the flex row as well as in the markup: the flex row draws its parts by `order`,
		// so the arrow's must be the highest of the three.
		const order = (selector: string) =>
			Number(
				new RegExp(`\\n\\t${selector.replace('.', '\\.')} \\{[^}]*?order: (\\d+);`).exec(
					SOURCE
				)?.[1]
			);
		expect(order('.disclose')).toBeGreaterThan(order('.trailing'));
		expect(order('.disclose')).toBeGreaterThan(order('.actions'));
	});

	it('says which way it is, and names what it opens', () => {
		const shut = arrow(render({ expanded: false, ontoggle: () => {}, toggleLabel: 'Photos' }));
		expect(shut?.getAttribute('aria-expanded')).toBe('false');
		expect(shut?.getAttribute('aria-label')).toBe('Show more: Photos');
		host.remove();

		const open = arrow(render({ expanded: true, ontoggle: () => {}, toggleLabel: 'Photos' }));
		expect(open?.getAttribute('aria-expanded')).toBe('true');
		expect(open?.getAttribute('aria-label')).toBe('Show less: Photos');
	});

	it('opens the row on a press of the arrow, and only once', () => {
		const toggled = vi.fn();
		const pressed = vi.fn();
		const row = render({ ontoggle: toggled, onpress: pressed });

		arrow(row)?.click();

		expect(toggled).toHaveBeenCalledTimes(1);
		expect(pressed).not.toHaveBeenCalled();
	});

	it('draws no arrow on a row that does not fold', () => {
		expect(arrow(render({}))).toBeNull();
	});
});

/* ------------------------------------------------------------------------------------------------
 * CELLS, placed by the list's declaration and never by the row.
 * ---------------------------------------------------------------------------------------------- */

const cell = (text: string) => createRawSnippet(() => ({ render: () => `<span>${text}</span>` }));

const DECLARED: Declared = {
	columns: [
		{ id: 'name', width: 'minmax(0, 3fr)' },
		{ id: 'bar', width: 'minmax(0, 1fr)' },
		{ id: 'count', width: '7rem', align: 'end' }
	],
	actions: '8rem'
};

function renderIn(declared: Declared | undefined, props: Record<string, unknown>) {
	host = document.createElement('ul');
	document.body.append(host);
	mount(DataRow, {
		target: host,
		props: { children, ...props },
		context: new Map<unknown, unknown>([[COLUMNS, declared]])
	});
	flushSync();
	return host.querySelector('.row') as HTMLElement;
}

describe('a row in a list that declared columns', () => {
	it('puts each cell in its column, in the declared order, aligned as the column says', () => {
		const row = renderIn(DECLARED, {
			cells: { count: cell('9 of 10'), name: cell('Thumbnails') }
		});
		const cells = [...row.querySelectorAll(':scope > .cell')];

		// Three data columns and the actions track, whatever order the row handed them over in.
		expect(cells.map((one) => one.textContent?.trim())).toEqual(['Thumbnails', '', '9 of 10', '']);
		expect(cells[2].classList.contains('end')).toBe(true);
		expect(cells[0].classList.contains('end')).toBe(false);
	});

	it('runs a cell across the columns its span names, and draws nothing in the ones it covers', () => {
		const row = renderIn(DECLARED, {
			cells: { name: cell('Duplicates'), bar: cell('2h ago, 4 min') },
			spans: { bar: 'count' }
		});
		const cells = [...row.querySelectorAll<HTMLElement>(':scope > .cell')];

		expect(cells.map((one) => one.textContent?.trim())).toEqual([
			'Duplicates',
			'2h ago, 4 min',
			''
		]);
		expect(cells[1].style.gridColumnEnd).toBe('span 2');
	});

	it('draws a row of no cells across every column: a group heading', () => {
		const row = renderIn(DECLARED, {});

		expect(row.classList.contains('across')).toBe(true);
		expect(row.querySelectorAll('.cell')).toHaveLength(1);
	});

	it('draws its arrow in the fold track, at its end', () => {
		const row = renderIn(
			{ ...DECLARED, folds: true },
			{ cells: { name: cell('clip.mp4') }, ontoggle: () => {} }
		);
		const track = row.querySelector(':scope > .cell.do') as HTMLElement;

		expect(track.classList.contains('folds')).toBe(true);
		expect(track.lastElementChild?.classList.contains('disclose')).toBe(true);
	});

	it('refuses an arrow in a list that kept no track for it', () => {
		expect(() => renderIn(DECLARED, { cells: { name: cell('x') }, ontoggle: () => {} })).toThrow(
			/track for the arrow/
		);
	});

	it('refuses cells in a list that declared no columns: it would be laying them out itself', () => {
		expect(() => renderIn(undefined, { cells: { name: cell('clip.mp4') } })).toThrow(
			/declared no columns/
		);
	});

	it('refuses actions in a list that declared no track for them', () => {
		expect(() =>
			renderIn(
				{ ...DECLARED, actions: undefined },
				{ cells: { name: cell('x') }, actions: cell('Retry') }
			)
		).toThrow(/actions track/);
	});

	it('is a flex row in a list that declared nothing: the known positive', () => {
		const row = renderIn(undefined, {});

		expect(row.classList.contains('columned')).toBe(false);
		expect(row.querySelector('.subject')?.textContent).toContain('Pictures');
	});
});

describe('the line under a group heading', () => {
	it('draws no top line on the line that holds the heading, so a heading after a row is one line', () => {
		const rule = SOURCE.replace(/\s+/g, ' ');
		expect(rule).toContain(
			':global(.line) + .line:has(> .row.across, > .row-trigger > .row.across) { border-block-start: 0; }'
		);
	});
});

describe('the actions of a row in a list that declared columns', () => {
	afterEach(() => removeStyles());

	it('lays them over the end of the row on its own ground, taking no track', () => {
		const row = renderIn(DECLARED, {
			cells: { name: cell('clip.mp4'), count: cell('9 of 10') },
			actions: cell('Retry')
		});
		applyStyles(SOURCE, row);
		const track = row.querySelector(':scope > .cell.do') as HTMLElement;
		const actions = track.querySelector('.actions') as HTMLElement;

		// No box of its own, so the last column runs to the list's end.
		expect(getComputedStyle(track).display).toBe('contents');
		expect(getComputedStyle(row).position).toBe('relative');
		expect(getComputedStyle(actions).position).toBe('absolute');
		expect(getComputedStyle(actions).insetInlineEnd).toBe('0px');
		// Nothing under the pointer at rest: the hidden buttons do not take a press meant for a cell.
		expect(getComputedStyle(actions).pointerEvents).toBe('none');
	});

	/* A count under the buttons would be cut through a digit ("12,0" beside Run in Tasks). The ground
	   fades in before the buttons, in the buttons' own ground, so a figure fades out instead. */
	it('fades their ground in over what they cover, in the ground they stand on', async () => {
		const row = renderIn(DECLARED, {
			cells: { name: cell('clip.mp4'), count: cell('12,000 of 12,000') },
			actions: cell('Run in Tasks')
		});
		applyStyles(SOURCE, row);
		const actions = row.querySelector('.cell.do .actions') as HTMLElement;
		expect(getComputedStyle(actions).backgroundColor).toBe('var(--actions-ground)');
		const { compile } = await import('svelte/compiler');
		const css = compile(SOURCE, { filename: 'DataRow.svelte', css: 'external' })
			.css!.code.replace(/\/\*[\s\S]*?\*\//g, '')
			.replace(/\.svelte-[\w-]+/g, '');
		// The drawn fade, not the one a touch screen switches off.
		const fades = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].filter(
			(one) => /\.actions(:where\(\))?::before$/.test(one[1].trim()) && one[2].includes('content')
		);
		expect(fades).toHaveLength(1);
		expect(fades[0][2]).toContain('inset-inline-end: 100%');
		expect(fades[0][2]).toContain('linear-gradient(to left, var(--actions-ground), transparent)');
	});

	/* A housekeeping row with no action must not draw its ground and fade on hover, over its own
	   "Yesterday, failed", which is the phrase to press. */
	it('draws no ground at all where the row has nothing to offer', async () => {
		const { compile } = await import('svelte/compiler');
		const css = compile(SOURCE, { filename: 'DataRow.svelte', css: 'external' })
			.css!.code.replace(/\/\*[\s\S]*?\*\//g, '')
			.replace(/\.svelte-[\w-]+/g, '');
		const empty = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].find((one) =>
			/^\.cell\.do \.actions(:where\(\))?:not\(:has\(\*\)\)$/.test(one[1].trim())
		);
		expect(empty?.[2]).toContain('display: none');
	});

	it('stands them just before the arrow where the list keeps a fold track', () => {
		const row = renderIn(
			{ ...DECLARED, folds: true },
			{ cells: { name: cell('clip.mp4') }, actions: cell('Retry'), ontoggle: () => {} }
		);
		applyStyles(SOURCE, row);
		const track = row.querySelector(':scope > .cell.do') as HTMLElement;
		const actions = track.querySelector('.actions') as HTMLElement;

		expect(getComputedStyle(track).display).toBe('flex');
		expect(getComputedStyle(track).position).toBe('relative');
		expect(getComputedStyle(actions).insetInlineEnd).toBe('calc(100% + var(--space-1))');
		// Sized by what it holds, never by the arrow's narrow track, so a worded action stays on
		// one line.
		expect(getComputedStyle(actions).inlineSize).toBe('max-content');
		expect(getComputedStyle(actions).flexWrap).toBe('nowrap');
	});

	it('lays them on the ground of the surface the list stands on', async () => {
		const sheet = (await import('$lib/components/SettingsModal.svelte?raw')).default;
		// The settings sheet is drawn on the first surface, not the page's ground.
		expect(sheet.replace(/\s+/g, ' ')).toMatch(
			/background: var\(--sift-surface-1\);[^}]*--data-row-base: var\(--sift-surface-1\);/
		);
		expect(SOURCE).toContain('var(--row-solid, var(--data-row-base, var(--sift-bg)))');
	});

	it('stay away from a row whose field is being answered, on a pointer that hovers', () => {
		const rule = SOURCE.replace(/\s+/g, ' ');
		const answering =
			/@media \(hover: hover\) \{ (\.row:has\([^{]+\)) \.actions \{ opacity: 0; pointer-events: none; \}/.exec(
				rule
			);
		expect(answering, 'no rule takes the hover actions off an answered row').not.toBeNull();
		// After the hover's own rule, so it wins where both match.
		expect(rule.indexOf(answering?.[0] ?? '')).toBeGreaterThan(
			rule.indexOf('.row:hover .actions,')
		);

		// The selector picks out the row with an open field focused, and not the row beside it.
		const list = document.createElement('div');
		list.innerHTML =
			'<div class="row" id="answered"><div class="cell"><form><input /></form></div><div class="cell do"><div class="actions"></div></div></div>' +
			'<div class="row" id="other"><div class="cell"></div><div class="cell do"><div class="actions"></div></div></div>';
		document.body.append(list);
		list.querySelector('input')?.focus();
		const selector = (answering?.[1] ?? '').replace(':global(', '').replace(/\)\)$/, ')');
		const hidden = [...document.querySelectorAll(`${selector} .actions`)].map(
			(one) => one.closest('.row')?.id
		);
		list.remove();
		expect(hidden).toEqual(['answered']);
	});

	it('gives them a track and shows them at rest where there is no hover', () => {
		const rule = SOURCE.replace(/\s+/g, ' ');
		expect(rule).toMatch(
			/@media \(hover: none\) \{ \.cell\.do \{ display: flex;[^}]*\} \.cell\.do \.actions, \.cell\.do\.folds \.actions \{ position: static;/
		);
	});
});
