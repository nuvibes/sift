/*
 * A row that holds rows has to open, and what it opens has to be reachable.
 *
 * A menu's rows sit in the shared `Scroller` so a long menu does not run off the window, and a
 * scrolling region clips. A flyout is drawn beside that region, so if it were inside the region it
 * would be mounted at the right size and place and then clipped to nothing. So two assertions: that
 * the rows exist, and that the layer holding them is outside every scrolling region, the property a
 * portal buys.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import Probe from './ContextMenuItemProbe.test.svelte';
import DressingProbe from './ContextMenuDressingProbe.test.svelte';
import source from './ContextMenuItem.svelte?raw';

let host: HTMLElement;
let instance: ReturnType<typeof mount> | null = null;

/* jsdom has no pointer capture and the primitives release it on the way down; absent, the handler
   throws and no menu ever opens. A gap in the test environment, not in the app. */
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

function takeDown() {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
}

function render() {
	takeDown();
	host = document.createElement('div');
	document.body.appendChild(host);
	instance = mount(Probe, { target: host, props: {} });
	flushSync();
}

afterEach(takeDown);

/** The words of a row, with the icon ligature's private-use codepoints taken out. */
function words(row: Element): string {
	return (row.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim();
}

function rowsNamed(): string[] {
	return [...document.querySelectorAll('[role="menuitem"]')].map(words);
}

async function openTheMenu() {
	render();
	host.querySelector('button')?.click();
	/* Generous, and not because it is flaky: the first of these to run pays for mounting the
	   portal's own component tree, which on a cold module graph is the far side of a second. */
	await vi.waitFor(() => expect(rowsNamed()).toContain('Add to'), { timeout: 5000 });
}

function openTheFlyout() {
	const opener = theOpener();
	/* The way a pointer does it. bits-ui's sub trigger answers the pointer sequence, and a bare
	   `click()` alone is enough only once the module graph is warm, which is a test that passes
	   or fails by whether it ran first. */
	opener.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
	opener.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
	opener.click();
	flushSync();
}

function theOpener(): HTMLElement {
	const opener = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
		(row) => words(row) === 'Add to'
	);
	expect(opener, 'no row labelled "Add to"').toBeTruthy();
	return opener as HTMLElement;
}

/* The right-click door, so the flyout is measured against a real `ContextMenu` surface. */
async function openTheContextMenu(): Promise<void> {
	takeDown();
	host = document.createElement('div');
	document.body.appendChild(host);
	instance = mount(DressingProbe, { target: host, props: {} });
	flushSync();

	const trigger = host.querySelector('[data-context-menu-trigger]');
	expect(trigger, 'no right-click target').toBeTruthy();
	trigger?.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true }));
	await vi.waitFor(() => expect(rowsNamed()).toContain('Add to'), { timeout: 5000 });
}

describe('the surface a menu tells its rows', () => {
	it('draws the flyout as a menu surface of its own, like the menu that opened it', async () => {
		/*
		 * There is one `ui-menu` dressing, so what is proved is that a flyout wears it: the class
		 * is what the stylesheet dresses, and jsdom has no stylesheet to read a colour from.
		 */
		await openTheContextMenu();
		openTheFlyout();

		let flyout: Element | null = null;
		await vi.waitFor(() => {
			flyout =
				[...document.querySelectorAll('.ui-menu')].find((menu) =>
					[...menu.querySelectorAll('[role="menuitem"]')].some((row) => words(row) === 'Collection')
				) ?? null;
			expect(flyout, 'the flyout never opened').toBeTruthy();
		}, 3000);

		expect((flyout as unknown as Element).classList.contains('ui-menu')).toBe(true);
	});
});

describe('a row that holds rows', () => {
	it('opens its flyout, and the rows in it are there to be picked', async () => {
		await openTheMenu();
		openTheFlyout();

		await vi.waitFor(
			() => expect(rowsNamed()).toEqual(expect.arrayContaining(['Collection', 'Photo Set'])),
			{ timeout: 3000 }
		);
	});

	it('draws that flyout OUTSIDE every scrolling region', async () => {
		/*
		 * The fault in the one form a test without layout can see: the rows are in the document
		 * either way; what matters is where the box holding them sits.
		 */
		await openTheMenu();
		openTheFlyout();

		let flyout: Element | null = null;
		await vi.waitFor(() => {
			flyout =
				[...document.querySelectorAll('.ui-menu')].find((menu) =>
					[...menu.querySelectorAll('[role="menuitem"]')].some((row) => words(row) === 'Collection')
				) ?? null;
			expect(flyout, 'the flyout never opened').toBeTruthy();
		}, 3000);

		expect((flyout as unknown as Element).closest('.scroll-root')).toBeNull();
	});
});

describe('a row whose label names a thing rather than an act', () => {
	/*
	 * A row whose glyph is a ticked box must also say so to assistive technology. Both halves are
	 * asserted, because either alone passes on a broken row: the role without the value never says
	 * which way it is set, and a value without the role is an attribute nothing reads.
	 */
	it('is a checkbox, and says which way it is set', async () => {
		await openTheMenu();

		const boxes = [...document.querySelectorAll('[role="menuitemcheckbox"]')];
		const row = boxes.find((one) => words(one) === 'Compact rows');
		expect(row, 'the row with a state is not a checkbox').toBeTruthy();
		expect(row?.getAttribute('aria-checked')).toBe('false');

		// The known positive: a verb row beside it must NOT be one, or the rule is satisfied by
		// every row in the menu being announced as an unticked box.
		expect(document.querySelector('[role="menuitem"]')?.getAttribute('aria-checked')).toBeNull();
	});

	it('flips on a press, and the menu stays open to show it', async () => {
		await openTheMenu();

		const row = [...document.querySelectorAll<HTMLElement>('[role="menuitemcheckbox"]')].find(
			(one) => words(one) === 'Compact rows'
		);
		row?.click();

		await vi.waitFor(() => {
			const again = [...document.querySelectorAll('[role="menuitemcheckbox"]')].find(
				(one) => words(one) === 'Compact rows'
			);
			expect(again, 'the menu closed on the press').toBeTruthy();
			expect(again?.getAttribute('aria-checked')).toBe('true');
		}, 3000);
	});
});

describe('the row itself', () => {
	afterEach(removeStyles);

	it('is laid out as a menu row inside the menu it opens in', async () => {
		await openTheMenu();
		const row = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
			(one) => words(one) === 'Copy link'
		);
		applyStyles(source, row?.querySelector('.said'));

		expect(row?.classList.contains('item')).toBe(true);
		expect(getComputedStyle(row as HTMLElement).display).toBe('flex');
	});
});
