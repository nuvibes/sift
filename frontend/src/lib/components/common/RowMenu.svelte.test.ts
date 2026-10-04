/*
 * The three-dot button is the same menu by another door, and a data row answers both.
 *
 * A door built by hand on a screen would carry its own copy of the button's style rules and its
 * own rows written into markup. What is asserted
 * here is the property that makes one door safe: the rows come from the shared declaration, so a
 * verb cannot exist behind the dots and not on the row's right-click, or the other way round.
 */
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

/* At the top of render as well as in afterEach: a test that renders twice would otherwise leave the
   first component mounted and answering, with only the second handle kept. */
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
		// A list of twenty rows announced as twenty "More" buttons tells somebody using a screen
		// reader nothing about which is which.
		render({ verbs: VERBS, ids: ['r1'], label: 'More for Photos' });
		const button = host.querySelector('button');
		expect(button?.getAttribute('aria-label')).toBe('More for Photos');
	});

	it('wears the one class the button is dressed by', () => {
		// The dressing lives in this component. Copies on each screen would come apart: one
		// dimming on hover while disabled and another not.
		render({ verbs: VERBS, ids: ['r1'], label: 'More' });
		expect(host.querySelector('button')?.className).toContain('more');
	});

	it('draws the CONTEXT menu rows inside a dropdown, which is the whole design', async () => {
		/*
		 * The claim this component rests on, proved rather than reasoned about.
		 *
		 * `VerbMenuItems` renders `ContextMenuItem`, which is `bits-ui`'s `ContextMenu.Item`. In
		 * bits-ui 2.18 that is re-exported from the same `menu/` internals as `DropdownMenu.Item`:
		 * only Root, Content and Trigger differ, which is the part about how a menu is OPENED. If
		 * that ever stops being true, a third renderer is needed and this is what says so.
		 */
		render({ verbs: VERBS, ids: ['r1'], label: 'More' });
		host.querySelector('button')?.click();
		await vi.waitFor(() => {
			// The icon is a LIGATURE, so its glyph is a character of the row's own text: a private-use
			// codepoint sitting in front of the words. `trim()` does not touch it, which is why a
			// plain comparison against the label reads as the row being wrong when it is right.
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
		// Not "it contains a menu item": that a DECLARATION is what becomes the rows is the property.
		// A file that wrote its own rows here would pass any assertion about the rows existing.
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
		// Eight of the nine lists built out of DataRow have not declared any. None of them may grow
		// a button or a wrapper because the capability was added.
		const source = readFileSync('src/lib/components/common/DataRow.svelte', 'utf8');
		expect(source).toContain('const offered = $derived(verbs !== undefined && verbs.length > 0)');
		expect(source).toContain('{#if offered}');
	});

	it('leaves no screen holding its own copy of the door', () => {
		// No screen draws the trigger, the portal, the content and the rows by hand, and a
		// screen that tries fails here.
		for (const path of [
			'src/lib/settings-ui/Users.svelte',
			'src/lib/library/LibraryScreen.svelte'
		]) {
			expect(readFileSync(path, 'utf8'), path).not.toContain('DropdownMenu');
		}
	});

	it('reaches both screens the same way, through the row', () => {
		/*
		 * A list whose rows carry verbs must grow them through `DataRow`, which grows both doors
		 * (the right-click and the three dots) from one declaration. A hand-placed button grows
		 * only one, and a hand-written row is invisible to a gate that counts shared ones.
		 */
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

/*
 * The worded trigger, for the one surface that is not a row.
 *
 * A file's own screen has one thing being looked at and no row for three dots to belong to. What is
 * asserted here is that saying so gets the SHARED button rather than a second one dressed to look
 * like it (the exact drift this whole component was written to end) and that it is still the
 * library's trigger, not a plain button that happens to carry the word.
 */
describe('the worded trigger', () => {
	it('carries the words instead of the glyph', () => {
		render({ verbs: VERBS, ids: ['r1'], label: 'Options for this file', words: 'Options' });
		const button = host.querySelector('button');
		expect(button?.textContent).toContain('Options');
		expect(button?.querySelector('[data-icon="more_vert"]')).toBeNull();
	});

	it('is the shared button, not a copy of its dressing', () => {
		// `btn` is what `Button` puts on every one of them. Without the `child` snippet handing the
		// library's props to the real component, this is a bare element wearing `more` instead,
		// which looks close enough to pass a glance and carries none of the button's own states.
		render({ verbs: VERBS, ids: ['r1'], label: 'Options for this file', words: 'Options' });
		const button = host.querySelector('button');
		expect(button?.className).toContain('btn');
		expect(button?.className).not.toContain('more');
	});

	it('is still the menu trigger, so the rows are reachable', () => {
		// The `child` snippet is easy to get wrong in a way nothing else notices: drop `{...props}`
		// and the button renders perfectly and opens nothing at all.
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
