/*
 * The way back, and what it costs to get it wrong.
 *
 * Given a destination this is a link, and following a link to a bare address is a new navigation
 * that throws away the query a wall wrote its position into. The claim under test: a plain click
 * goes to the address the wall was left at when back is the same place, and every other press
 * follows the href, so it never walks somebody out of the app.
 *
 * Goes to the address, not `history.back()`: see `Breadcrumbs.svelte.test.ts`.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { goto } from '$app/navigation';
import BackButton from './BackButton.svelte';
import { noteAddress } from '$lib/shell/navigation.svelte';

const gone = vi.mocked(goto);

/* "We were there, and then we came here." Two notes, because that is what a person does and
   what the module reads: the address it holds as PREVIOUS is the one noted before the current. */
function cameFrom_wasAt(href: string) {
	noteAddress(new URL(href));
	noteAddress(new URL(`${window.location.origin}/people/anna`));
}

let host: HTMLDivElement;
let component: Record<string, unknown> | null = null;
let back: ReturnType<typeof vi.fn>;
let go: ReturnType<typeof vi.fn>;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
	back = vi.fn();
	go = vi.fn();
	vi.stubGlobal('history', { back, go });
	gone.mockClear();
	sessionStorage.clear();
});

afterEach(() => {
	if (component) unmount(component);
	component = null;
	host.remove();
	vi.unstubAllGlobals();
});

type Drawn = { to?: string; label: string; onback?: () => void };

function draw(props: Drawn) {
	component = mount(BackButton, { target: host, props });
	flushSync();
}

function press(over: Partial<MouseEventInit> = {}): MouseEvent {
	const link = host.querySelector('a');
	if (!link) throw new Error('no link was drawn');
	const event = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ...over });
	link.dispatchEvent(event);
	return event;
}

it('goes to the remembered address when back is the place it names', () => {
	cameFrom_wasAt(`${window.location.origin}/people?from=abc`);
	draw({ to: '/people', label: 'People' });

	const event = press();

	expect(gone).toHaveBeenCalledExactlyOnceWith('/people?from=abc');
	expect(back).not.toHaveBeenCalled();
	expect(event.defaultPrevented).toBe(true);
});

it('follows the link when back is somewhere else', () => {
	cameFrom_wasAt(`${window.location.origin}/browse`);
	draw({ to: '/people', label: 'People' });

	press();

	expect(gone).not.toHaveBeenCalled();
});

it('follows the link for somebody who arrived here directly', () => {
	/* The whole reason `to` exists. With nothing behind them, stepping back leaves Sift. */
	draw({ to: '/people', label: 'People' });

	press();

	expect(gone).not.toHaveBeenCalled();
});

it('leaves a modified click alone, whatever back would have been', () => {
	/* Ctrl, Shift, Meta, Alt and a middle-click each mean "somewhere else, not this window", and
	   answering any of them by moving THIS window ignores what was asked. */
	cameFrom_wasAt(`${window.location.origin}/people`);
	draw({ to: '/people', label: 'People' });

	for (const modifier of ['ctrlKey', 'metaKey', 'shiftKey', 'altKey'] as const) {
		press({ [modifier]: true });
	}
	press({ button: 1 });

	expect(gone).not.toHaveBeenCalled();
});

it('is still a plain button, going back, when it names nowhere', () => {
	draw({ label: 'All faces' });

	host.querySelector('button')?.click();

	expect(back).toHaveBeenCalledOnce();
	expect(host.querySelector('a')).toBeNull();
});

it('closes rather than moves when the caller handed it a way to close', () => {
	const onback = vi.fn();
	draw({ to: '/people', label: 'People', onback });

	host.querySelector('button')?.click();

	expect(onback).toHaveBeenCalledOnce();
	expect(back).not.toHaveBeenCalled();
});

it('steps back to the place it names where the browser can say it is the screen before', () => {
	/* Shares `returnTo` with the crumb: a step back returns to the entry the wall was left in, which
	   brings the scroll with it. Counted over the tabs, so two tabs in means two steps. */
	const entries = ['/people?from=abc', '/people/anna', '/people/anna?tab=faces'].map(
		(path, index) => ({
			url: `${window.location.origin}${path}`,
			index
		})
	);
	vi.stubGlobal('navigation', { currentEntry: entries[2], entries: () => entries });
	draw({ to: '/people', label: 'People' });

	const event = press();

	expect(go).toHaveBeenCalledExactlyOnceWith(-2);
	expect(gone).not.toHaveBeenCalled();
	expect(back).not.toHaveBeenCalled();
	expect(event.defaultPrevented).toBe(true);
});
