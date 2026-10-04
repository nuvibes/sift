/*
 * Every door can be told where its menu lands, and the kept pill tells it.
 *
 * A fullscreen browser paints only the fullscreened element's subtree, so a menu portalled to
 * `body` while a screen is filled is not drawn: it opens, takes the press and the keyboard, and
 * shows nothing. A kept filter's pill draws a `RowMenu` inside the filter panel, which drops out of
 * the bar inside `.screen-box`, so `MenuButton` must accept a target.
 *
 * Two halves are checked: the menu itself is portalled by `DropdownMenu.Portal`, and a row's flyout
 * by whatever the row reads out of `menu-portal`, which the door sets. Either alone lands half the
 * layers in the wrong subtree.
 */
import { readFileSync } from 'node:fs';
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import RowMenu from './RowMenu.svelte';
import type { Verb } from './verbs';

function source(file: string): string {
	return readFileSync(`src/lib/components/common/${file}`, 'utf8');
}

/* Every primitive that opens a floating layer of its own. `Select` and `Popover` are here because
   the prop is a family rule rather than one component's feature: a fifth door added without it is
   a door that cannot be opened on a filled screen, and nothing else would say so. */
const DOORS = [
	'ContextMenu.svelte',
	'MenuButton.svelte',
	'RowMenu.svelte',
	'Select.svelte',
	'Popover.svelte'
];

describe('the doors', () => {
	it.each(DOORS)('%s can be told where its layer is drawn', (file) => {
		expect(source(file)).toMatch(/portalTo\?: Element \| null;/);
	});

	it('hands the answer to the portal AND to the rows inside the menu', () => {
		// Both, in one test, because doing one of them reads as a fix and is half of one: the menu
		// arrives inside the filled box and every flyout opened from it goes back to the document.
		const menuButton = source('MenuButton.svelte');
		expect(menuButton).toContain('<DropdownMenu.Portal to={portalTo ?? undefined}>');
		expect(menuButton).toContain('ownsTheMenu({ where: () => portalTo })');
	});

	it('is handed down by the door that composes another', () => {
		// `RowMenu` answers nothing about where a layer goes: it knows what the rows are, and the
		// door under it knows where it lands.
		expect(source('RowMenu.svelte')).toMatch(/<MenuButton[^>]*\{portalTo\}/);
	});
});

describe('a kept pill, a door drawn inside a filled screen', () => {
	const pill = source('KeptPill.svelte');

	it('asks the shell which box is filling the window', () => {
		// The same source, in the same words, as the column chooser in the panel above it and the
		// menu on a Theater cell. Null while nothing is filled, which is the ordinary answer.
		expect(pill).toContain("import { stage } from '$lib/components/shell/stage.svelte'");
	});

	it('tells BOTH of its doors, because it has two', () => {
		// The right-click and the three dots are one list of verbs through two doors, and a pill
		// whose right-click worked on a filled wall while its dots did not would read as the verbs
		// being gone rather than as the menu being undrawn.
		expect(pill.match(/portalTo=\{stage\.whatFillsTheWindow\}/g)).toHaveLength(2);
	});
});

/*
 * AND IT REALLY LANDS THERE, which no reading of the source can say.
 *
 * jsdom draws nothing and fills no screen, so what is proved here is the plumbing: given a box, the
 * menu's rows are inside that box rather than at the end of the document. That is the whole of what
 * fullscreen needs: the browser paints a subtree, and this is what puts the menu in it.
 */
describe('where the rows actually arrive', () => {
	const VERBS: Verb[] = [{ id: 'scan', label: 'Scan now', icon: 'cached', run: () => {} }];

	let mounted: ReturnType<typeof mount> | null = null;

	afterEach(() => {
		if (mounted) unmount(mounted);
		mounted = null;
		document.body.innerHTML = '';
	});

	function open(portalTo: Element | null) {
		const host = document.createElement('div');
		document.body.append(host);
		mounted = mount(RowMenu, {
			target: host,
			props: { verbs: VERBS, ids: ['r1'], label: 'More', portalTo }
		});
		flushSync();
		host.querySelector('button')?.click();
		return host;
	}

	it('puts the menu inside the box it was given', async () => {
		const box = document.createElement('section');
		document.body.append(box);
		open(box);

		await vi.waitFor(() => {
			expect(box.querySelector('[role="menuitem"]')).not.toBeNull();
		});
	});

	it('leaves it at the end of the document when it was given nothing', async () => {
		// The ordinary answer, and the reason portalling exists at all: out of every scrolling
		// region and every stacking context between the menu and what opened it.
		const host = open(null);

		await vi.waitFor(() => {
			expect(document.querySelector('[role="menuitem"]')).not.toBeNull();
		});
		expect(host.querySelector('[role="menuitem"]')).toBeNull();
	});
});
