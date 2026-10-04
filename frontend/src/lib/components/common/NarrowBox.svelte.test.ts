/*
 * The one box a list is filtered with, and the three things about it that are not decoration.
 *
 * Its look is the stylesheet's, read by the design gates. What is tested is the contract its two
 * callers (the facet panel's columns and the shared Select's searchable list) depend on and would
 * not notice losing:
 *
 * - it binds, because `Select` filters on `bind:value` and a one-way box simply stops narrowing;
 * - it passes the caller's own attributes through, because `Select` hands it an `onkeydown` that
 *   stops the list treating a letter as a jump to a row, and `FacetPanel` hands it an `oninput` and
 *   no binding;
 * - it carries an accessible name, different per column ("Filter the People list"), since a row of
 *   identical unnamed search boxes is the fault the `label` prop exists to prevent.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import NarrowBox from './NarrowBox.svelte';
import boxSource from './NarrowBox.svelte?raw';

let host: HTMLElement;

afterEach(() => {
	removeStyles();
	host?.remove();
	document.body.innerHTML = '';
});

function draw(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	const reactive = reactiveProps({ value: '', label: 'Filter the People list', ...props });
	mount(NarrowBox, { target: host, props: reactive });
	flushSync();
	return reactive;
}

function box(): HTMLInputElement {
	const found = host.querySelector('input');
	if (!found) throw new Error('there is no box');
	return found;
}

function cross(): HTMLButtonElement | null {
	return host.querySelector<HTMLButtonElement>('button.field-clear');
}

function press(key: string): KeyboardEvent {
	const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
	box().dispatchEvent(event);
	flushSync();
	return event;
}

function type(text: string) {
	const input = box();
	input.value = text;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

describe('the box a list is narrowed with', () => {
	it('hands what was typed back to the caller', () => {
		/* `Select` filters its own rows on this value. One-way, the box fills with text and the list
		   under it never moves, which looks like a search that found everything. */
		const props = draw();

		type('ame');

		expect(props.value).toBe('ame');
	});

	it('shows what the caller put in it', () => {
		const props = draw({ value: 'already here' });
		expect(box().value).toBe('already here');

		props.value = 'set from outside';
		flushSync();
		expect(box().value).toBe('set from outside');
	});

	it('carries the name the caller gave it', () => {
		/* Every facet column draws one of these. Unnamed, a screen reader announces a row of
		   identical "search" boxes and there is no way to tell which list each filters. */
		draw({ label: 'Filter the Tags list' });
		expect(box().getAttribute('aria-label')).toBe('Filter the Tags list');
	});

	it('passes the caller-s own handlers through to the real box', () => {
		/* `Select` stops `keydown` here so the list beneath does not read a letter as a jump to a
		   row, and `FacetPanel` listens on `input` rather than binding. Both are `...rest`. */
		const onkeydown = vi.fn();
		const oninput = vi.fn();
		draw({ onkeydown, oninput });

		box().dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }));
		type('a');

		expect(onkeydown).toHaveBeenCalledTimes(1);
		expect(oninput).toHaveBeenCalledTimes(1);
	});
});

/*
 * The cross, inside the box, for every list the box filters. It must do exactly what deleting the
 * text by hand does, including for a caller that binds nothing and listens for `input` (the facet
 * panel, Downloads), whose list would otherwise stay filtered under an empty box.
 */
describe('where the words start', () => {
	it('starts them at a field inset by default, and where the rows under it start when asked', () => {
		draw();
		applyStyles(boxSource, box());
		expect(getComputedStyle(box()).paddingInlineStart).not.toBe('calc(var(--space-3) - 1px)');
		host.remove();

		draw({ inset: 'row' });
		applyStyles(boxSource, box());
		expect(getComputedStyle(box()).paddingInlineStart).toBe('calc(var(--space-3) - 1px)');
	});
});

describe('the cross that empties the box', () => {
	it('is there only while there is something to empty', () => {
		draw();
		expect(cross()).toBeNull();

		type('ame');
		expect(cross()).not.toBeNull();
	});

	it('empties the box for a caller that binds it, and puts the caret back', () => {
		const props = draw({ value: 'ame' });

		cross()?.click();
		flushSync();

		expect(props.value).toBe('');
		expect(box().value).toBe('');
		expect(document.activeElement).toBe(box());
		expect(cross()).toBeNull();
	});

	it('tells a caller that only listens, the same way typing does', () => {
		/* Read inside the handler: `currentTarget` is null again once the event has finished. */
		const heard: string[] = [];
		const oninput = vi.fn((event: Event) => {
			heard.push((event.currentTarget as HTMLInputElement).value);
		});
		draw({ value: 'ame', oninput });

		cross()?.click();
		flushSync();

		expect(heard).toEqual(['']);
	});

	it('says what it does to a screen reader', () => {
		draw({ value: 'ame' });
		expect(cross()?.getAttribute('aria-label')).toBe('Clear the search');
	});
});

/*
 * ESCAPE: empties a box with text in it and goes no further; does nothing to an empty one.
 *
 * The order is the point for a box inside something that closes on Escape (`Select`, `PickMenu`,
 * the Settings panel): the first press takes the words away and leaves the list up, the second
 * reaches whatever owns Escape above. A document listener stands in for "whatever is above".
 */
describe('Escape in the box', () => {
	it('empties a box with text in it, and the key goes no further', () => {
		const above = vi.fn();
		document.addEventListener('keydown', above);
		const onkeydown = vi.fn();
		const props = draw({ value: 'ame', onkeydown });

		const event = press('Escape');
		document.removeEventListener('keydown', above);

		expect(props.value).toBe('');
		expect(event.defaultPrevented).toBe(true);
		expect(above).not.toHaveBeenCalled();
		expect(onkeydown).not.toHaveBeenCalled();
	});

	it('does nothing to an empty box, and hands the key on to the caller and above', () => {
		const above = vi.fn();
		document.addEventListener('keydown', above);
		const onkeydown = vi.fn();
		draw({ onkeydown });

		const event = press('Escape');
		document.removeEventListener('keydown', above);

		expect(event.defaultPrevented).toBe(false);
		expect(onkeydown).toHaveBeenCalledTimes(1);
		expect(above).toHaveBeenCalledTimes(1);
	});
});

describe('the two heights', () => {
	it('is the small box unless asked, and the page-header height when it is', () => {
		draw();
		expect(box().classList.contains('medium')).toBe(false);
		host.remove();

		draw({ size: 'medium' });
		expect(box().classList.contains('medium')).toBe(true);
	});

	it("puts the caller's class on the whole box, words and cross together", () => {
		/* Downloads caps the box's width with a class. On the text field alone, the cross would be
		   positioned against a wrapper the class never reached. */
		draw({ class: 'find' });
		expect(box().parentElement?.classList.contains('find')).toBe(true);
		expect(box().parentElement?.classList.contains('narrow-box')).toBe(true);
	});
});

/*
 * ONE CROSS, DRAWN BY ONE COMPONENT. `.field-clear` is the look; this box and the top bar's search
 * are the only two that wear it. The top bar keeps its own because its cross clears the CHIPS as
 * well as the words, which is a different act. Anywhere else, a box that wants a cross is a box that
 * should be this one, never a hand-made copy.
 */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

function everySvelte(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...everySvelte(path));
		else if (entry.endsWith('.svelte')) found.push(path);
	}
	return found;
}

describe('one cross in the application', () => {
	it('is worn by this box and the top bar, and by nothing else', () => {
		const wearing = everySvelte(SOURCE)
			.filter((path) => /class="field-clear"/.test(readFileSync(path, 'utf8')))
			.map((path) => relative(SOURCE, path).split('\\').join('/'))
			.sort();

		expect(
			wearing,
			'a box that narrows a list is NarrowBox, which draws the cross itself. See its header'
		).toEqual(['lib/components/common/NarrowBox.svelte', 'lib/components/shell/SearchBox.svelte']);
	});
});
