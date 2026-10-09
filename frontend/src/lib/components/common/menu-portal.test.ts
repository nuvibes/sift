/* Every door can be told where its menu lands (a filled screen paints only its subtree), and the
 * kept pill tells it: the menu through `DropdownMenu.Portal`, a row's flyout through `menu-portal`.
 * */
import { readFileSync } from 'node:fs';
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import RowMenu from './RowMenu.svelte';
import type { Verb } from './verbs';

function source(file: string): string {
	return readFileSync(`src/lib/components/common/${file}`, 'utf8');
}

/* Every primitive that opens a floating layer; the prop is a family rule. */
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
		// Both halves together, or the flyouts go back to the document.
		const menuButton = source('MenuButton.svelte');
		expect(menuButton).toContain('<DropdownMenu.Portal to={portalTo ?? undefined}>');
		expect(menuButton).toContain('ownsTheMenu({ where: () => portalTo })');
	});

	it('is handed down by the door that composes another', () => {
		// RowMenu hands the target to its door.
		expect(source('RowMenu.svelte')).toMatch(/<MenuButton[^>]*\{portalTo\}/);
	});
});

describe('a kept pill, a door drawn inside a filled screen', () => {
	const pill = source('KeptPill.svelte');

	it('asks the shell which box is filling the window', () => {
		// The same source as the column chooser and a Theater cell's menu.
		expect(pill).toContain("import { stage } from '$lib/components/shell/stage.svelte'");
	});

	it('tells BOTH of its doors, because it has two', () => {
		// The right-click and the dots alike.
		expect(pill.match(/portalTo=\{stage\.whatFillsTheWindow\}/g)).toHaveLength(2);
	});
});

/* And it really lands there: given a box, the rows are inside it. */
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
		// The ordinary answer: the end of the document.
		const host = open(null);

		await vi.waitFor(() => {
			expect(document.querySelector('[role="menuitem"]')).not.toBeNull();
		});
		expect(host.querySelector('[role="menuitem"]')).toBeNull();
	});
});
