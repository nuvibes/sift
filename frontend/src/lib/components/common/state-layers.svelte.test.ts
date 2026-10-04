/*
 * The state matrix, held on each primitive's compiled stylesheet: a hover is the state layer on the
 * control's own ground, a press the stronger layer, disabled one strength with no layer under it,
 * and selected a look of its own that a hover does not repeat.
 *
 * jsdom cannot point at anything, so each stylesheet is compiled with `:hover`, `:active` and
 * `:focus-visible` spelled as attributes a test can set, the same move the gallery makes to draw
 * those states. jsdom also leaves `var()` unresolved in a computed value, which is what lets these
 * assert WHICH token a state reads rather than a colour that would change with every theme.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { compile } from 'svelte/compiler';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { heldStates } from '$lib/design/testing-states';
import Button from './Button.svelte';
import buttonSource from './Button.svelte?raw';
import Checkbox from './Checkbox.svelte';
import checkboxSource from './Checkbox.svelte?raw';
import Chip from './Chip.svelte';
import chipSource from './Chip.svelte?raw';
import DataRow from './DataRow.svelte';
import dataRowSource from './DataRow.svelte?raw';
import MenuButton from './MenuButton.svelte';
import menuButtonSource from './MenuButton.svelte?raw';
import Pressable from './Pressable.svelte';
import pressableSource from './Pressable.svelte?raw';
import Switch from './Switch.svelte';
import switchSource from './Switch.svelte?raw';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	removeStyles();
});

/**
 * The stylesheet with the three states only a pointer or a keyboard can hold ALSO spelled as
 * attributes a test can set. See `$lib/design/testing-states`.
 */
const held = heldStates;

function draw<P extends Record<string, unknown>>(component: unknown, props: P): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(component as never, { target: host, props }) as Record<string, unknown>;
	flushSync();
	return host;
}

const words = createRawSnippet(() => ({ render: () => '<span>Save</span>' }));

describe('a button', () => {
	function button(props: Record<string, unknown> = {}): HTMLButtonElement {
		draw(Button, { tone: 'secondary', children: words, ...props });
		const element = host.querySelector('button') as HTMLButtonElement;
		applyStyles(held(buttonSource), element);
		return element;
	}

	it('takes the hover layer on its own ground under the pointer', () => {
		const element = button();
		element.setAttribute('data-hover', '');
		const style = getComputedStyle(element);
		expect(style.backgroundColor).toContain('var(--layer-hover)');
		expect(style.backgroundColor).toContain('var(--btn-ground)');
		expect(style.getPropertyValue('--btn-ground').trim()).toBe('var(--sift-surface-2)');
	});

	it('takes the pressed layer and gives under a press, over its hover', () => {
		const element = button({ tone: 'ghost' });
		element.setAttribute('data-hover', '');
		element.setAttribute('data-active', '');
		const style = getComputedStyle(element);
		expect(style.backgroundColor).toContain('var(--layer-pressed)');
		expect(style.scale).toBe('var(--press-scale)');
	});

	it('is one strength while disabled, with no layer under the pointer', () => {
		const element = button({ disabled: true });
		element.setAttribute('data-hover', '');
		const style = getComputedStyle(element);
		expect(style.opacity).toBe('var(--disabled-opacity)');
		expect(style.backgroundColor).not.toContain('var(--layer-hover)');
	});

	it('is not dimmed while it is busy', () => {
		const element = button({ busy: true });
		expect(getComputedStyle(element).opacity).toBe('1');
		expect(element.getAttribute('aria-busy')).toBe('true');
	});

	it('when switched on, grounds its layers on the accent tint', () => {
		const element = button({ tone: 'ghost', pressed: true });
		expect(getComputedStyle(element).getPropertyValue('--btn-ground').trim()).toBe(
			'var(--sift-accent-bg)'
		);
	});
});

describe('a checkbox', () => {
	it('takes the hover layer on its own ground, ticked or not', () => {
		draw(Checkbox, { state: 'on', label: 'Included' });
		const box = host.querySelector('.box') as HTMLElement;
		applyStyles(held(checkboxSource), box);
		box.setAttribute('data-hover', '');
		const style = getComputedStyle(box);
		expect(style.backgroundColor).toContain('var(--layer-hover)');
		expect(style.getPropertyValue('--box-ground').trim()).toBe('var(--sift-accent)');
	});
});

describe('a switch', () => {
	it('takes the hover layer on its track, and the one disabled strength', () => {
		draw(Switch, { label: 'A setting', disabled: true });
		const track = host.querySelector('.switch') as HTMLElement;
		applyStyles(held(switchSource));
		expect(getComputedStyle(track).opacity).toBe('var(--disabled-opacity)');

		unmount(drawn as Record<string, unknown>);
		drawn = null;
		host.remove();
		draw(Switch, { label: 'A setting' });
		const live = host.querySelector('.switch') as HTMLElement;
		live.setAttribute('data-hover', '');
		expect(getComputedStyle(live).backgroundColor).toContain('var(--layer-hover)');
	});
});

describe('the three dots', () => {
	it('take the hover layer, and the pressed one while their menu is open', () => {
		draw(MenuButton, { label: 'More for this row', children: words });
		const dots = host.querySelector('button') as HTMLElement;
		applyStyles(held(menuButtonSource));
		dots.setAttribute('data-hover', '');
		expect(getComputedStyle(dots).backgroundColor).toContain('var(--layer-hover)');
		dots.removeAttribute('data-hover');
		dots.setAttribute('data-state', 'open');
		expect(getComputedStyle(dots).backgroundColor).toContain('var(--layer-pressed)');
	});
});

describe('a chip', () => {
	it('when selected, grounds the hover layer on the accent tint rather than turning its ink alone', () => {
		draw(Chip, { selected: true, onselect: () => {}, children: words });
		const chip = host.querySelector('.chip') as HTMLElement;
		applyStyles(held(chipSource), chip);
		expect(getComputedStyle(chip).getPropertyValue('--chip-ground').trim()).toBe(
			'var(--sift-accent-bg)'
		);
		(chip.querySelector('.body') as HTMLElement).setAttribute('data-hover', '');
		expect(getComputedStyle(chip).backgroundColor).toContain('var(--layer-hover)');
	});
});

describe('a row', () => {
	it('when selected, wears the accent tint, and the hover layer lies over it', () => {
		draw(DataRow, { selected: true, onpress: () => {}, children: words });
		const row = host.querySelector('.row') as HTMLElement;
		applyStyles(held(dataRowSource), row);
		const style = getComputedStyle(row);
		expect(style.getPropertyValue('--row-ground').trim()).toBe('var(--selected-row)');
		expect(style.color).toBe('var(--selected-row-ink)');
		row.setAttribute('data-hover', '');
		expect(getComputedStyle(row).backgroundColor).toContain('var(--row-ground)');
		expect(getComputedStyle(row).backgroundColor).toContain('var(--layer-hover)');
	});
});

describe('the line between rows', () => {
	/*
	 * Read as the selector the compiled stylesheet draws the line with, matched against two real
	 * rows: the environment computes no logical borders, and whether the rule reaches a row is the
	 * whole of the claim.
	 */
	it('is drawn by the lower of two rows, and never by the first or under the last', () => {
		const list = document.createElement('ul');
		document.body.append(list);
		const one = mount(DataRow, { target: list, props: { children: words } });
		const two = mount(DataRow, { target: list, props: { children: words } });
		flushSync();
		try {
			const lines = [...list.querySelectorAll<HTMLElement>('li.line')];
			const css =
				compile(dataRowSource, { filename: 'Row.svelte', css: 'external' }).css?.code ?? '';
			const written = css.match(/svelte-[a-z0-9]+/)?.[0] ?? '';
			const mounted = [...lines[0].classList].find((name) => /^svelte-[a-z0-9]+$/.test(name)) ?? '';
			const rule = css.match(/([^{}]*)\{[^{}]*border-block-start:\s*1px solid[^{}]*\}/);
			expect(rule, 'no rule draws the line between rows').not.toBeNull();
			expect(css).not.toMatch(/border-block-end:\s*1px solid/);
			/* The prelude, after any comment the compiler kept in front of it. */
			const selector =
				(rule?.[1] ?? '').split('*/').pop()?.trim().replaceAll(written, mounted) ?? '';
			expect(lines[0].matches(selector)).toBe(false);
			expect(lines[1].matches(selector)).toBe(true);
		} finally {
			unmount(one);
			unmount(two);
			list.remove();
		}
	});
});

describe('a pressable surface', () => {
	it('washes with the hover layer and, picked, wears the selected ring', () => {
		draw(Pressable, { feedback: 'wash', picked: true, children: words });
		const surface = host.querySelector('button') as HTMLElement;
		applyStyles(held(pressableSource), surface);
		expect(getComputedStyle(surface).boxShadow).toBe('var(--selected-ring)');
		surface.setAttribute('data-hover', '');
		expect(getComputedStyle(surface).backgroundColor).toContain('var(--layer-hover)');
	});
});

describe('the tokens the matrix reads', () => {
	const css = readFileSync(resolve('src/app.css'), 'utf8');
	const token = (name: string) => css.match(new RegExp(`^\\s*${name}:\\s*([^;]+);`, 'm'))?.[1];

	it('are one strength per state, disabled among them', () => {
		expect(token('--layer-hover')).toBe('8%');
		expect(token('--layer-pressed')).toBe('12%');
		expect(token('--layer-dragged')).toBe('16%');
		expect(token('--disabled-opacity')).toBe('0.38');
		expect(token('--press-scale')).toBe('0.98');
	});

	it('light a floating list row with the layer, not with a surface of its own', () => {
		expect(token('--menu-row-highlight')).toContain('var(--layer-hover)');
	});

	it('give a text box the layer under the pointer, and no weight to beat a box dressed for itself', () => {
		const rule = css.match(/:where\([^{]*textarea[^{]*\):hover:where\([^{]*\)\s*\{([^}]*)\}/);
		expect(rule, 'the text box hover rule is gone').not.toBeNull();
		expect(rule?.[1]).toContain('var(--layer-hover)');
	});
});
