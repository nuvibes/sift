/* A closing menu answers Escape until the library says the close is complete, and not after.
 *
 * Held against the rule the panels underneath read (`keystrokeIsUnanswered`), because that is the
 * whole contract: without it the file popout would close its History pane on an Escape aimed at an
 * Add-to menu that was still on screen but already closed as far as the library was concerned.
 */

import { afterEach, expect, it } from 'vitest';
import { flushSync } from 'svelte';

import { keystrokeIsUnanswered } from '$lib/shell/layers';
import { escapeWhileClosing, type ClosingMenu } from './menu-closing.svelte';

let stop: (() => void) | null = null;

afterEach(() => {
	stop?.();
	stop = null;
});

function guard(): ClosingMenu {
	let made: ClosingMenu | undefined;
	stop = $effect.root(() => {
		made = escapeWhileClosing();
	});
	flushSync();
	return made!;
}

/** What a panel listening on the window would conclude about one keystroke. */
function panelHears(key = 'Escape'): boolean {
	const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
	document.body.dispatchEvent(event);
	return keystrokeIsUnanswered(event);
}

it('leaves Escape to the panel while the menu is open or gone', () => {
	const menu = guard();

	expect(panelHears()).toBe(true);
	menu.changed(true);
	expect(panelHears()).toBe(true);
});

it('answers Escape from the moment the menu closes until the close is complete', () => {
	const menu = guard();

	menu.changed(false);
	expect(menu.closing).toBe(true);
	expect(panelHears()).toBe(false);
	// Only Escape: a closing menu has no claim on any other key.
	expect(panelHears('ArrowDown')).toBe(true);

	menu.complete(false);
	expect(menu.closing).toBe(false);
	expect(panelHears()).toBe(true);
});

it('lets go when the menu opens again before the close completed', () => {
	const menu = guard();

	menu.changed(false);
	menu.changed(true);
	expect(panelHears()).toBe(true);
});

it('takes its listener away with the component that owned the menu', () => {
	const menu = guard();
	menu.changed(false);

	stop?.();
	stop = null;

	expect(panelHears()).toBe(true);
});
