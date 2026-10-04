/*
 * A menu is divided into its parts by its groups, and the line between two parts is the group's.
 *
 * Drawn for real: the declared list through `VerbMenuItems`, opened through the three-dot door,
 * with the group's own stylesheet in the document, so what is asserted is which lines a person
 * would actually see rather than which elements happen to exist.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import Probe from './ContextMenuGroupProbe.test.svelte';
import source from './ContextMenuGroup.svelte?raw';
import type { Verb } from './verbs';

for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	removeStyles();
	document.body.innerHTML = '';
});

const run = () => {};

/** Declared out of order on purpose: the menu's order is the groups', never the declaration's. */
const VERBS: Verb[] = [
	{ id: 'delete', label: 'Delete', icon: 'delete', destructive: true, run },
	{ id: 'share', label: 'Share', icon: 'group', group: 'share', run },
	{ id: 'rate', label: 'Rating', icon: 'star', group: 'file', run },
	{ id: 'hide', label: 'Hide', icon: 'visibility_off', group: 'share', run },
	{ id: 'add', label: 'Add to', icon: 'add', group: 'file', run },
	{ id: 'save', label: 'Save to device', icon: 'download', group: 'keep', run }
];

const words = (row: Element) => (row.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim();

async function open(props: { verbs: Verb[]; extra?: 'rows' | 'none' }) {
	const host = document.createElement('div');
	document.body.appendChild(host);
	instance = mount(Probe, { target: host, props });
	flushSync();
	applyStyles(source);
	host.querySelector('button')?.click();
	await vi.waitFor(() => expect(document.querySelector('[role="menuitem"]')).not.toBeNull(), {
		timeout: 5000
	});
}

const rows = () => [...document.querySelectorAll('[role="menuitem"]')].map(words);

/** The lines a person sees: separators the stylesheet has not taken away. */
const lines = () =>
	[...document.querySelectorAll('[role="menu"] .menu-separator')].filter(
		(line) => getComputedStyle(line).display !== 'none'
	);

describe('a menu drawn from a declared list', () => {
	it('draws the groups in their one order, alphabetical inside each, Delete last', async () => {
		await open({ verbs: VERBS });

		expect(rows()).toEqual(['Add to', 'Rating', 'Save to device', 'Hide', 'Share', 'Delete']);
	});

	it('draws one line between each two groups and none above the first', async () => {
		await open({ verbs: VERBS });

		// Four groups (file, keep, share, and Delete alone), so three lines.
		expect(lines()).toHaveLength(3);
		const groups = [...document.querySelectorAll('[role="menu"] .menu-group')];
		expect(
			groups.map((group) => [...group.querySelectorAll('[role="menuitem"]')].map(words))
		).toEqual([['Add to', 'Rating'], ['Save to device'], ['Hide', 'Share'], ['Delete']]);
		expect(getComputedStyle(groups[0].querySelector('.menu-separator')!).display).toBe('none');
	});

	it("puts the surface's own rows in a group of their own, in front of Delete", async () => {
		await open({ verbs: VERBS, extra: 'rows' });

		expect(rows().slice(-2)).toEqual(['Use as the cover', 'Delete']);
		expect(lines()).toHaveLength(4);
	});

	it('draws no line for a group of its own rows that turned out empty', async () => {
		await open({ verbs: VERBS, extra: 'none' });

		expect(lines()).toHaveLength(3);
	});

	it('draws a short list of one group with no line and no group at all', async () => {
		await open({
			verbs: [
				{ id: 'b', label: 'Beta', icon: 'add', run },
				{ id: 'a', label: 'Alpha', icon: 'add', run }
			]
		});

		// No group declared: the declaration's own order, and nothing fenced.
		expect(rows()).toEqual(['Beta', 'Alpha']);
		expect(document.querySelector('[role="menu"] .menu-separator')).toBeNull();
	});
});
