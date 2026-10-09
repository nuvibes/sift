/* The three dots are the same menu by another door: the rows come from the shared declaration. */
import { readFileSync } from 'node:fs';
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import RowMenu from './RowMenu.svelte';
import type { Verb } from './verbs';

let host: HTMLElement;
let instance: ReturnType<typeof mount> | null = null;

function takeDown() {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
}

/* At the top of render too, so a second render leaves no first mounted. */
type RowMenuProps = {
	verbs: readonly Verb[];
	ids: string[];
	label: string;
	disabled?: boolean;
	words?: string;
};

function render(props: RowMenuProps) {
	takeDown();
	host = document.createElement('div');
	document.body.appendChild(host);
	instance = mount(RowMenu, { target: host, props });
	flushSync();
}

afterEach(takeDown);

const RAN = vi.fn();

const VERBS: Verb[] = [
	{ id: 'scan', label: 'Scan now', icon: 'cached', run: RAN },
	{ id: 'remove', label: 'Remove', icon: 'delete', destructive: true, run: RAN }
];

describe('the three-dot button', () => {
	it('says whose row it belongs to', () => {
		// A name saying whose row, not twenty "More"s.
		render({ verbs: VERBS, ids: ['r1'], label: 'More for Photos' });
		const button = host.querySelector('button');
		expect(button?.getAttribute('aria-label')).toBe('More for Photos');
	});

	it('wears the one class the button is dressed by', () => {
		// The dressing lives in this component.
		render({ verbs: VERBS, ids: ['r1'], label: 'More' });
		expect(host.querySelector('button')?.className).toContain('more');
	});

	it('draws the CONTEXT menu rows inside a dropdown, which is the whole design', async () => {
		/* VerbMenuItems' rows work inside a DropdownMenu, as bits-ui shares the menu internals. */
		render({ verbs: VERBS, ids: ['r1'], label: 'More' });
		host.querySelector('button')?.click();
		await vi.waitFor(() => {
			// The icon ligature is a character of the row's text, which trim() keeps.
			const rows = [...document.querySelectorAll('[role="menuitem"]')].map((row) =>
				(row.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim()
			);
			expect(rows).toEqual(['Scan now', 'Remove']);
		});
	});

	it('can be told there is nothing to be done just now', () => {
		render({ verbs: VERBS, ids: ['r1'], label: 'More', disabled: true });
		expect(host.querySelector('button')?.disabled).toBe(true);
	});
});

describe('the two doors cannot come apart', () => {
	it('renders its rows through the shared renderer', () => {
		// The declaration becomes the rows.
		const source = readFileSync('src/lib/components/common/RowMenu.svelte', 'utf8');
		expect(source).toContain('<VerbMenuItems');
		expect(source).not.toMatch(/<DropdownMenu\.Item/);
	});

	it('gives a data row both doors from one declaration', () => {
		const source = readFileSync('src/lib/components/common/DataRow.svelte', 'utf8');
		// The right-click, the dots, and one list feeding both.
		expect(source).toMatch(/<ContextMenu[\s>]/);
		expect(source).toContain('<RowMenu');
		expect(source).toContain('<VerbMenuItems');
	});

	it('leaves a row that declared no verbs exactly as it was', () => {
		// A list that declared no verbs grows no button or wrapper.
		const source = readFileSync('src/lib/components/common/DataRow.svelte', 'utf8');
		expect(source).toContain('const offered = $derived(verbs !== undefined && verbs.length > 0)');
		expect(source).toContain('{#if offered}');
	});

	it('leaves no screen holding its own copy of the door', () => {
		// No screen draws a door by hand.
		for (const path of [
			'src/lib/settings-ui/Users.svelte',
			'src/lib/library/LibraryScreen.svelte'
		]) {
			expect(readFileSync(path, 'utf8'), path).not.toContain('DropdownMenu');
		}
	});

	it('reaches both screens the same way, through the row', () => {
		/* A list with verbs grows both doors through DataRow. */
		for (const path of [
			'src/lib/settings-ui/Users.svelte',
			'src/lib/library/LibraryScreen.svelte'
		]) {
			expect(readFileSync(path, 'utf8'), path).toMatch(/<DataRow[\s\S]*?verbs=/);
			expect(readFileSync(path, 'utf8'), path).not.toContain('<RowMenu');
		}
	});

	it('leaves no screen holding its own copy of the button dressing', () => {
		for (const path of [
			'src/lib/settings-ui/Users.svelte',
			'src/lib/library/LibraryScreen.svelte'
		]) {
			expect(readFileSync(path, 'utf8'), path).not.toContain('button.more');
		}
	});
});

/* The worded trigger gets the shared button, still the library's trigger. */
describe('the worded trigger', () => {
	it('carries the words instead of the glyph', () => {
		render({ verbs: VERBS, ids: ['r1'], label: 'Options for this file', words: 'Options' });
		const button = host.querySelector('button');
		expect(button?.textContent).toContain('Options');
		expect(button?.querySelector('[data-icon="more_vert"]')).toBeNull();
	});

	it('is the shared button, not a copy of its dressing', () => {
		// `btn` proves the `child` snippet hands the props to the real Button.
		render({ verbs: VERBS, ids: ['r1'], label: 'Options for this file', words: 'Options' });
		const button = host.querySelector('button');
		expect(button?.className).toContain('btn');
		expect(button?.className).not.toContain('more');
	});

	it('is still the menu trigger, so the rows are reachable', () => {
		// Without `{...props}` the button renders and opens nothing.
		render({ verbs: VERBS, ids: ['r1'], label: 'Options for this file', words: 'Options' });
		const button = host.querySelector('button');
		expect(button?.getAttribute('aria-haspopup')).toBe('menu');
		expect(button?.getAttribute('aria-expanded')).toBe('false');
	});

	it('takes the three dots when no words are given', () => {
		render({ verbs: VERBS, ids: ['r1'], label: 'More for Photos' });
		expect(host.querySelector('button')?.className).toContain('more');
	});
});
