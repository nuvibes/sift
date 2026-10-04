/*
 * One long press does one thing: it selects. The menu on a touch screen is behind the three dots,
 * and at a phone's width every menu is a sheet from the foot of the screen.
 *
 * Left to the library, a finger held on a tile would select it at 300 ms and then the menu
 * library's own touch hold would open a desktop dropdown over it at 700: two answers to one
 * press. The hold is `TileGesture`'s; these prove the menu does not answer it, by either of the
 * two ways a touch screen asks, and that a mouse and the keyboard still reach
 * it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import contextMenuSource from './ContextMenu.svelte?raw';
import menuButtonSource from './MenuButton.svelte?raw';
import TallProbe from './ContextMenuTallProbe.test.svelte';
import ItemProbe from './ContextMenuItemProbe.test.svelte';
import { phoneWidth } from './phone-width.svelte';
import { keepFocusOnPress, refusesTheHold, touchPress } from './menu-touch';

/* jsdom has no pointer capture and the primitives release it on the way down. */
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

let instance: ReturnType<typeof mount> | null = null;

beforeEach(() => {
	phoneWidth.yes = false;
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	vi.useRealTimers();
	removeStyles();
	phoneWidth.yes = false;
	window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
	document.body.innerHTML = '';
});

/** A pointer event of a given kind. `pointerType` is set on the instance, which every engine and
 *  jsdom's constructor agree on, whatever each does with the init dictionary. */
function pointer(type: string, kind: string, init: MouseEventInit = {}): PointerEvent {
	const event = new PointerEvent(type, { bubbles: true, cancelable: true, button: 0, ...init });
	Object.defineProperty(event, 'pointerType', { value: kind });
	return event;
}

function tallMenu(): HTMLElement {
	const host = document.createElement('div');
	document.body.append(host);
	instance = mount(TallProbe, { target: host, props: {} });
	flushSync();
	const trigger = host.querySelector<HTMLElement>('[data-context-menu-trigger]');
	if (!trigger) throw new Error('no right-click target');
	return trigger;
}

const openMenus = () => document.querySelectorAll('[role="menu"]').length;

/* A finger drawn down (or up, given a negative travel) a sheet's head, quickly, as a stroke is. */
function drawDown(target: Element, dy: number): void {
	const at = (type: string, y: number) => {
		const event = new PointerEvent(type, {
			bubbles: true,
			cancelable: true,
			button: 0,
			isPrimary: true,
			pointerId: 6,
			clientX: 200,
			clientY: y
		});
		Object.defineProperty(event, 'pointerType', { value: 'touch' });
		target.dispatchEvent(event);
	};
	at('pointerdown', 300);
	at('pointerup', 300 + dy);
	flushSync();
}

describe('a finger held on something with a menu', () => {
	it('does not open the menu when the library would have (its 700 ms hold)', () => {
		vi.useFakeTimers();
		const trigger = tallMenu();

		trigger.dispatchEvent(pointer('pointerdown', 'touch', { clientX: 40, clientY: 40 }));
		vi.advanceTimersByTime(1500);
		flushSync();

		expect(openMenus(), 'a hold opened the menu over what it selected').toBe(0);
	});

	it('refuses the contextmenu a phone raises from a long press', () => {
		const trigger = tallMenu();
		const raised = pointer('contextmenu', 'touch');

		trigger.dispatchEvent(raised);
		flushSync();

		expect(openMenus()).toBe(0);
		expect(raised.defaultPrevented, "the browser's own menu would come up instead").toBe(true);
	});

	it('refuses one with no pointer type while a finger is still down', () => {
		/* An engine that raises `contextmenu` as a plain mouse event: the press in progress answers. */
		const trigger = tallMenu();
		trigger.dispatchEvent(pointer('pointerdown', 'touch'));
		const raised = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });

		trigger.dispatchEvent(raised);
		flushSync();

		expect(openMenus()).toBe(0);
	});
});

describe('a mouse and the keyboard', () => {
	it('still open the menu on a right-click', async () => {
		const trigger = tallMenu();
		trigger.dispatchEvent(pointer('pointerdown', 'mouse', { button: 2 }));
		trigger.dispatchEvent(pointer('contextmenu', 'mouse', { clientX: 40, clientY: 40 }));

		await vi.waitFor(() => expect(openMenus()).toBe(1));
	});

	it('still open it from the menu key once a finger has let go', async () => {
		const trigger = tallMenu();
		trigger.dispatchEvent(pointer('pointerdown', 'touch'));
		/* Let go somewhere else entirely: the press ends on the window, not the trigger. */
		window.dispatchEvent(pointer('pointerup', 'touch'));

		trigger.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));

		await vi.waitFor(() => expect(openMenus()).toBe(1));
	});

	it('leave a pen barrel button alone, which is a right click', () => {
		const press = touchPress();
		press.down(pointer('pointerdown', 'pen', { button: 2 }));
		const barrel = pointer('contextmenu', 'pen');

		expect(refusesTheHold(barrel, press.kind)).toBe(false);
		expect(barrel.defaultPrevented).toBe(false);
	});

	it('refuse a pen held down with its tip, which is a hold', () => {
		const press = touchPress();
		press.down(pointer('pointerdown', 'pen'));

		expect(refusesTheHold(pointer('contextmenu', 'pen'), press.kind)).toBe(true);
	});
});

describe('at a phone width every menu is a sheet', () => {
	it('opens the right-click menu from the foot of the screen, headed with whose it is', async () => {
		phoneWidth.yes = true;
		const trigger = tallMenu();
		trigger.dispatchEvent(pointer('contextmenu', 'mouse'));

		let sheet: HTMLElement | null = null;
		await vi.waitFor(() => {
			sheet = document.querySelector<HTMLElement>('.ui-menu.menu-sheet');
			expect(sheet, 'no sheet opened').toBeTruthy();
		});
		expect(sheet!.closest('[data-bits-floating-content-wrapper]'), 'it was floated').toBeNull();
		expect(sheet!.querySelector('.menu-sheet-head')?.textContent?.trim()).toBe('Actions');
		expect(sheet!.getAttribute('role')).toBe('menu');

		/* Placed by the stylesheet alone. jsdom resolves no logical inset, so the placement is read
		   off the rule itself; the position is read off the element. */
		applyStyles(contextMenuSource);
		expect(getComputedStyle(sheet!).position).toBe('fixed');
		expect(contextMenuSource).toMatch(
			/:global\(\.ui-menu\.menu-sheet\) \{\s*position: fixed;\s*inset-inline: 0;\s*inset-block-end: 0;/
		);
	});

	it('opens the three dots as the same sheet', async () => {
		phoneWidth.yes = true;
		const host = document.createElement('div');
		document.body.append(host);
		instance = mount(ItemProbe, { target: host, props: {} });
		flushSync();
		/* A tap: the library opens a touch trigger on the finger coming UP. */
		const dots = host.querySelector('button');
		dots?.dispatchEvent(pointer('pointerdown', 'touch'));
		dots?.dispatchEvent(pointer('pointerup', 'touch'));
		flushSync();

		await vi.waitFor(() => expect(document.querySelector('.ui-menu.menu-sheet')).toBeTruthy());
		expect(
			document.querySelector('.ui-menu.menu-sheet .menu-sheet-head')?.textContent?.trim()
		).toBe('More for this file');
	});

	it('does not let the tap that opened the sheet choose the row that lands under it', async () => {
		/* More pressed over picked files would run a sharing verb on all of them, because the tap's
		   click arrives after the sheet is drawn under the finger. */
		phoneWidth.yes = true;
		const host = document.createElement('div');
		document.body.append(host);
		instance = mount(ItemProbe, { target: host, props: {} });
		flushSync();
		const dots = host.querySelector('button');
		dots?.dispatchEvent(pointer('pointerdown', 'touch'));
		dots?.dispatchEvent(pointer('pointerup', 'touch'));
		flushSync();
		await vi.waitFor(() => expect(document.querySelector('.ui-menu.menu-sheet')).toBeTruthy());
		const row = () =>
			[...document.querySelectorAll<HTMLElement>('.ui-menu.menu-sheet [role^="menuitem"]')].find(
				(one) => one.textContent?.includes('Compact rows')
			)!;

		row().dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
		flushSync();
		expect(document.querySelector('.ui-menu.menu-sheet'), 'the ghost tap chose a row').toBeTruthy();
		expect(row().getAttribute('aria-checked')).toBe('false');

		/* A real press in the sheet is chosen (a ticked row stays open and changes its tick). */
		row().dispatchEvent(pointer('pointerdown', 'touch'));
		row().dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
		flushSync();
		await vi.waitFor(() => expect(row().getAttribute('aria-checked')).toBe('true'));
	});

	it('does not let a mouse or pen press that opened the sheet choose a row by letting go on it', async () => {
		/* A mouse or a pen opens the door on the way DOWN, so the sheet is under the pointer before
		   it comes up, and the library's row answers a `pointerup` it saw no press for by clicking
		   itself (detail 0, which the click guard takes for the keyboard): More would open onto
		   Rename, and a Theater cell's three dots would run Play. */
		phoneWidth.yes = true;
		const host = document.createElement('div');
		document.body.append(host);
		instance = mount(ItemProbe, { target: host, props: {} });
		flushSync();
		host.querySelector('button')?.dispatchEvent(pointer('pointerdown', 'mouse'));
		flushSync();
		await vi.waitFor(() => expect(document.querySelector('.ui-menu.menu-sheet')).toBeTruthy());
		const row = () =>
			[...document.querySelectorAll<HTMLElement>('.ui-menu.menu-sheet [role^="menuitem"]')].find(
				(one) => one.textContent?.includes('Compact rows')
			)!;

		row().dispatchEvent(pointer('pointerup', 'mouse'));
		flushSync();
		expect(
			document.querySelector('.ui-menu.menu-sheet'),
			'the letting go chose a row'
		).toBeTruthy();
		expect(row().getAttribute('aria-checked')).toBe('false');

		/* A press made in the sheet still chooses on its letting go. */
		row().dispatchEvent(pointer('pointerdown', 'mouse'));
		row().dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
		flushSync();
		await vi.waitFor(() => expect(row().getAttribute('aria-checked')).toBe('true'));
	});

	it('lets the keyboard choose a row in the sheet with no press at all', async () => {
		phoneWidth.yes = true;
		const host = document.createElement('div');
		document.body.append(host);
		instance = mount(ItemProbe, { target: host, props: {} });
		flushSync();
		const dots = host.querySelector('button');
		dots?.dispatchEvent(pointer('pointerdown', 'touch'));
		dots?.dispatchEvent(pointer('pointerup', 'touch'));
		flushSync();
		await vi.waitFor(() => expect(document.querySelector('.ui-menu.menu-sheet')).toBeTruthy());
		const row = [
			...document.querySelectorAll<HTMLElement>('.ui-menu.menu-sheet [role^="menuitem"]')
		].find((one) => one.textContent?.includes('Copy link'))!;

		row.click();
		flushSync();
		await vi.waitFor(() => expect(document.querySelector('.ui-menu.menu-sheet')).toBeNull());
	});

	it('opens the three dots as a dropdown on a wide window, as it always has', async () => {
		const host = document.createElement('div');
		document.body.append(host);
		instance = mount(ItemProbe, { target: host, props: {} });
		flushSync();
		host.querySelector('button')?.dispatchEvent(pointer('pointerdown', 'mouse'));
		flushSync();

		await vi.waitFor(() =>
			expect(document.querySelector('[data-bits-floating-content-wrapper] > .ui-menu')).toBeTruthy()
		);
		expect(document.querySelector('.ui-menu.menu-sheet')).toBeNull();
	});

	it('puts the right-click sheet away on a finger drawn down its head, and not on one drawn up', async () => {
		phoneWidth.yes = true;
		tallMenu().dispatchEvent(pointer('contextmenu', 'mouse'));
		await vi.waitFor(() => expect(document.querySelector('.ui-menu.menu-sheet')).toBeTruthy());
		const head = () => document.querySelector('.ui-menu.menu-sheet .menu-sheet-head')!;

		drawDown(head(), -140);
		expect(document.querySelector('.ui-menu.menu-sheet'), 'a stroke up put it away').toBeTruthy();
		drawDown(head(), 140);
		await vi.waitFor(() => expect(document.querySelector('.ui-menu.menu-sheet')).toBeNull());
	});

	it("puts the three dots' sheet away the same way", async () => {
		phoneWidth.yes = true;
		const host = document.createElement('div');
		document.body.append(host);
		instance = mount(ItemProbe, { target: host, props: {} });
		flushSync();
		const dots = host.querySelector('button');
		dots?.dispatchEvent(pointer('pointerdown', 'touch'));
		dots?.dispatchEvent(pointer('pointerup', 'touch'));
		flushSync();
		await vi.waitFor(() => expect(document.querySelector('.ui-menu.menu-sheet')).toBeTruthy());

		drawDown(document.querySelector('.ui-menu.menu-sheet .menu-sheet-head')!, 140);
		await vi.waitFor(() => expect(document.querySelector('.ui-menu.menu-sheet')).toBeNull());
		expect(dots?.getAttribute('data-state'), 'the door still says it holds a menu open').toBe(
			'closed'
		);
	});

	it("leaves the stroke to script on the sheet's head, a finger high", () => {
		const rule = contextMenuSource.slice(
			contextMenuSource.indexOf(':global(.ui-menu.menu-sheet > .menu-sheet-head) {')
		);
		expect(rule).toMatch(/^[^}]*min-block-size: var\(--touch-target\);/);
		expect(rule).toMatch(/^[^}]*touch-action: none;/);
	});

	it("makes every menu row a finger's height, and the dots a finger's size", () => {
		const rows =
			/@media \(max-width: 767px\)\s*\{\s*:global\(\.ui-menu \.item\)\s*\{\s*min-block-size: var\(--touch-target\);/;
		expect(contextMenuSource).toMatch(rows);
		const dots =
			/@media \(max-width: 767px\)\s*\{\s*:global\(button\.more\.menu-dots\)\s*\{\s*inline-size: var\(--touch-target\);\s*block-size: var\(--touch-target\);/;
		expect(menuButtonSource).toMatch(dots);
	});
});

describe('keeping the focus off a pressed control', () => {
	const pressed = (pointerType: string) => {
		const down = new PointerEvent('pointerdown', { cancelable: true, pointerType });
		keepFocusOnPress(down);
		return down.defaultPrevented;
	};

	it('refuses a mouse press on the way down, so the focus stays where it was', () => {
		expect(pressed('mouse')).toBe(true);
		expect(pressed('pen')).toBe(true);
	});

	it("never refuses a finger's, which WebKit would answer with no click at all", () => {
		expect(pressed('touch')).toBe(false);
	});
});
