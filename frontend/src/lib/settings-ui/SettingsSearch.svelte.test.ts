/* The settings search box.
 *
 * What it finds, where the results are drawn and what the pane beside them shows are the shell's,
 * because all three are about two columns this cannot see: drawn in here, they would fold
 * Settings up while somebody typed. `search.test.ts` pins what matches and
 * `settings-search.spec.ts` pins the screen.
 *
 * Three claims are left, and all three are this component's alone: what was typed is published
 * upwards, Escape empties it without closing the panel around it, and Ctrl-F means THIS box while
 * it exists.
 */

import { afterEach, beforeEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import SettingsSearch from './SettingsSearch.svelte';

let host: HTMLDivElement;
let component: Record<string, unknown> | null = null;

/* Its own props object, because reading a bindable back means owning the object it lives in.
   Declared once at the top level (`$state` may only initialise a declaration), and emptied
   before each test rather than replaced. */
const props = $state<{ typed: string; landed?: string }>({ typed: '' });

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
	props.typed = '';
	props.landed = undefined;
	component = mount(SettingsSearch, { target: host, props });
	flushSync();
});

afterEach(() => {
	if (component) unmount(component);
	component = null;
	host.remove();
});

function box(): HTMLInputElement {
	const input = host.querySelector('input');
	if (!input) throw new Error('no search box');
	return input;
}

function type(what: string) {
	box().value = what;
	box().dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

it('starts the typed words where the section rows under it start theirs', () => {
	expect(box().classList.contains('row-inset')).toBe(true);
});

it('publishes what was typed, which is the whole of what it is for', () => {
	type('vpn');

	expect(props.typed).toBe('vpn');
});

it('clears on Escape, and does not let the press reach the panel behind it', () => {
	/* `bubbles: true`, and it is not decoration: Svelte 5 DELEGATES keydown to the root, so a
	   synthetic event that does not bubble reaches no handler at all and the test reads as the key
	   doing nothing.

	   `cancelable`, so `defaultPrevented` can be read afterwards. Settings closes on an Escape that
	   nothing above answered, so a press meant to empty this box would otherwise shut the screen
	   it was helping somebody search. */
	type('vpn');
	const escape = new KeyboardEvent('keydown', {
		key: 'Escape',
		bubbles: true,
		cancelable: true
	});
	box().dispatchEvent(escape);
	flushSync();

	expect(props.typed).toBe('');
	expect(escape.defaultPrevented, 'the panel behind would have closed too').toBe(true);
});

it('takes Ctrl-F while it exists, and puts the caret in itself', () => {
	/* Ctrl-F is the LIBRARY's search everywhere else, and while Settings is open that is the wrong
	   box: it opens a sheet over the panel to search files. Marking the event answered is what the
	   sheet reads. See `$lib/shell/layers`, which is the same mechanism Escape already uses. */
	const pressed = new KeyboardEvent('keydown', {
		key: 'f',
		ctrlKey: true,
		bubbles: true,
		cancelable: true
	});
	document.body.dispatchEvent(pressed);
	flushSync();

	expect(pressed.defaultPrevented, 'the library sheet would have opened over Settings').toBe(true);
	expect(document.activeElement).toBe(box());
});

it('selects what is already in it, so a second press replaces rather than appends', () => {
	type('vpn');
	document.body.dispatchEvent(
		new KeyboardEvent('keydown', { key: 'f', ctrlKey: true, bubbles: true, cancelable: true })
	);
	flushSync();

	expect([box().selectionStart, box().selectionEnd]).toEqual([0, 3]);
});

it('leaves Ctrl-F alone while somebody is typing into something', () => {
	/* Not this component's rule: `matches` refuses every shortcut that is not marked as surviving a
	   text box, and a person typing expects the box's own keys. Pinned here because the claim is
	   made here, and a claim that fires from inside a field is one that eats a keystroke. */
	const other = document.createElement('input');
	document.body.append(other);
	other.focus();

	const pressed = new KeyboardEvent('keydown', {
		key: 'f',
		ctrlKey: true,
		bubbles: true,
		cancelable: true
	});
	other.dispatchEvent(pressed);
	flushSync();

	expect(pressed.defaultPrevented).toBe(false);
	other.remove();
});

it('keeps its label reachable while keeping it off the screen', () => {
	/* The box must not be something a screen reader can only call "edit text". It is a
	   `NarrowBox` and the name is on the element itself, so what is asserted is the accessible
	   NAME rather than the markup that carries it. */
	expect(box().getAttribute('aria-label')).toBe('Search settings');
});

/* The cross.
 *
 * A shared `Button` wearing a `.field-clear` class would come out 50 by 36 with a control's
 * padding: the class loses to the button's own scoped rules. What is pinned here is the part a
 * unit test can see: that the mark is a real button somebody can reach and press, that it empties
 * the box, and that the caret comes back so typing can carry on. What it LOOKS like is settled in
 * `app.css` and in this file's own rules, and a computed size is not something jsdom can be asked
 * about.
 */
function cross(): HTMLButtonElement | null {
	return host.querySelector('button[aria-label="Clear the search"]');
}

it('shows no cross over an empty box, because there is nothing to take away', () => {
	expect(cross()).toBe(null);
});

it('is a real button, so the keyboard reaches it', () => {
	type('vpn');

	expect(cross()?.tagName).toBe('BUTTON');
	expect(cross()?.getAttribute('type')).toBe('button');
});

it('empties the box and hands the caret back', () => {
	type('vpn');
	cross()?.click();
	flushSync();

	expect(props.typed).toBe('');
	expect(document.activeElement, 'somebody clearing a search is about to type again').toBe(box());
});

it('draws ONE cross: the box its own, with no second copy laid over it from here', () => {
	/* The cross is `NarrowBox`'s. A copy of its own positioned over the box would show two
	   crosses. */
	type('vpn');

	expect(host.querySelectorAll('button')).toHaveLength(1);
});

it('lets Escape over an EMPTY box through to the panel, which closes', () => {
	/* Swallowing every Escape would leave the panel impossible to close from the keyboard with the
	   caret here. There is nothing to empty, so the key is the panel's. */
	const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
	box().dispatchEvent(escape);
	flushSync();

	expect(escape.defaultPrevented).toBe(false);
});

/* A pasted path opens its row and empties the box at once, which alone reads as the paste having
   been dropped: the box says where it went while the shell holds `landed`, and to a screen reader. */
it('says where a pasted path went, and goes back to its own words after', () => {
	props.landed = 'How often';
	flushSync();
	expect(box().placeholder).toBe('Opened How often');
	expect(host.querySelector('[aria-live]')?.textContent).toBe('Opened How often');
	expect(host.querySelector('.landed')).not.toBeNull();
	// A long name is cut in the box, so the whole of it is on the box's tooltip.
	expect(box().title).toBe('Opened How often');

	props.landed = undefined;
	flushSync();
	expect(box().placeholder).toBe('Search settings');
	expect(host.querySelector('.landed')).toBeNull();
	expect(box().hasAttribute('title')).toBe(false);
});
