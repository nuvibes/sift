/*
 * The bare form of the checkbox: the mark without its box.
 *
 * The pick menu's flyout rows say what a selection is already on with a tick or a bar at the end of
 * the row, where a 16px bordered square would read as a second control. The bare form is that
 * drawing in its one home. What is held: it is a picture and not a control, it says each state with
 * the same classes as the box (so one set of rules draws both), and the pick menu carries no
 * drawing of its own.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import Checkbox, { type CheckState } from './Checkbox.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
});

function render(state: CheckState): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Checkbox, { target: host, props: { bare: true, state } }) as Record<
		string,
		unknown
	>;
	flushSync();
	return host.firstElementChild as HTMLElement;
}

describe('a bare checkbox', () => {
	it('is a picture, not a control, and has no box', () => {
		const drawn = render('on');

		expect(host.querySelector('button, [role="checkbox"]')).toBeNull();
		expect(drawn.getAttribute('aria-hidden')).toBe('true');
		expect(drawn.classList.contains('bare')).toBe(true);
		expect(drawn.classList.contains('box'), 'the bordered square came back').toBe(false);
		expect(drawn.querySelector('.mark')).not.toBeNull();
	});

	it.each([
		['on', ['on']],
		['partly', ['partly']],
		['out', ['out']],
		['off', []]
	] as const)('says %s with the classes the box says it with', (state, classes) => {
		const drawn = render(state);
		const said = ['on', 'partly', 'out'].filter((name) => drawn.classList.contains(name));
		expect(said).toEqual(classes);
	});

	it('keeps its place when off, so a column of them does not shift', () => {
		// Present and invisible rather than absent: the element is there to hold the column.
		expect(render('off').querySelector('.mark')).not.toBeNull();
	});
});

describe('the pick menu draws its mark through it', () => {
	const source = readFileSync(resolve('src/lib/components/common/PickMenu.svelte'), 'utf8');

	it('uses the bare checkbox', () => {
		expect(source).toContain('<Checkbox bare state="partly" />');
		expect(source).toMatch(/<Checkbox bare state=\{on === 'all' \? 'on' : 'off'\} \/>/);
	});

	it('carries no tick or bar of its own', () => {
		expect(source, 'a second copy of the drawing').not.toMatch(/^\t\.on(\.[a-z]+)? \{/m);
		expect(source).not.toContain('rotate: 45deg');
	});
});
