/*
 * The trail, and the one thing about it that is not markup.
 *
 * A crumb names a bare address, and a wall writes its position into the address it is left at, so
 * following "People" as a link would restart the wall at the top. The claim under test: a plain
 * click returns to where the wall was left, where back is the same place, and every other press
 * follows the link.
 *
 * Not a bare `history.back()`: a screen whose tabs are real links pushes an entry per tab at one
 * pathname, so one step back would land on the previous tab.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { goto } from '$app/navigation';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import Breadcrumbs, { folding } from './Breadcrumbs.svelte';
import source from './Breadcrumbs.svelte?raw';
import { noteAddress } from '$lib/shell/navigation.svelte';

const gone = vi.mocked(goto);

/* "We were there, and then we came here." Two notes, because that is what a person does and
   what the module reads: the address it holds as PREVIOUS is the one noted before the current. */
function cameFrom_wasAt(href: string) {
	noteAddress(new URL(href));
	noteAddress(new URL(`${window.location.origin}/people/ada`));
}

const TRAIL = [{ label: 'People', href: '/people' }, { label: 'Ada' }];

let host: HTMLDivElement;
let component: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
	gone.mockClear();
	sessionStorage.clear();
	component = mount(Breadcrumbs, { target: host, props: { crumbs: TRAIL } });
	flushSync();
});

afterEach(() => {
	if (component) unmount(component);
	component = null;
	host.remove();
});

function pressPeople(over: Partial<MouseEventInit> = {}) {
	const link = host.querySelector('a');
	if (!link) throw new Error('no crumb link');
	link.dispatchEvent(
		new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ...over })
	);
}

it('goes to the address the wall was left at when the step above is where we came from', () => {
	cameFrom_wasAt(`${window.location.origin}/people?from=abc`);

	const link = host.querySelector('a');
	const event = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
	link?.dispatchEvent(event);

	expect(gone).toHaveBeenCalledExactlyOnceWith('/people?from=abc');
	expect(event.defaultPrevented).toBe(true);
});

it('matches on the path and keeps the query, which is the very thing being preserved', () => {
	/* `/people?from=abc` IS `/people`. Comparing whole addresses would refuse to go back to exactly
	   the addresses worth going back to, and the query is what is carried there. */
	cameFrom_wasAt(`${window.location.origin}/people?from=abc&prefix=k`);

	pressPeople();

	expect(gone).toHaveBeenCalledExactlyOnceWith('/people?from=abc&prefix=k');
});

it('follows the link for somebody who opened this page directly', () => {
	/* Nothing behind them. Stepping back leaves Sift, which is worse than losing a page number. */
	pressPeople();

	expect(gone).not.toHaveBeenCalled();
});

it('follows the link when we came from somewhere else entirely', () => {
	cameFrom_wasAt(`${window.location.origin}/browse`);

	pressPeople();

	expect(gone).not.toHaveBeenCalled();
});

it('leaves a modified or middle click alone', () => {
	cameFrom_wasAt(`${window.location.origin}/people`);

	for (const modifier of ['ctrlKey', 'metaKey', 'shiftKey', 'altKey'] as const) {
		pressPeople({ [modifier]: true });
	}
	pressPeople({ button: 1 });

	expect(gone).not.toHaveBeenCalled();
});

it('still draws the last crumb as the page rather than a link', () => {
	expect(host.querySelector('[aria-current="page"]')?.textContent).toBe('Ada');
	expect(host.querySelectorAll('a')).toHaveLength(1);
});

it('steps back instead, where the browser can say the wall is the screen before this one', () => {
	/*
	 * A step back returns to the entry the wall was left in, so the scroll comes back with the
	 * page; going to the address would make a new entry starting at the top. The rule is
	 * `returnTo`; this holds that the crumb is wired to it.
	 */
	const go = vi.fn();
	const entries = [
		`${window.location.origin}/people?from=abc`,
		`${window.location.origin}/people/ada`
	].map((url, index) => ({ url, index }));
	vi.stubGlobal('navigation', { currentEntry: entries[1], entries: () => entries });
	vi.stubGlobal('history', { go });

	pressPeople();

	expect(go).toHaveBeenCalledExactlyOnceWith(-1);
	expect(gone).not.toHaveBeenCalled();
	vi.unstubAllGlobals();
});

/*
 * A LONG TRAIL FOLDS ITS MIDDLE: from four steps on, the steps between the first and the last two
 * go behind one press, which lists them as links. Where the trail is told to fit a box (the top
 * bar), it keeps to one line that ends in an ellipsis, with the whole path in its tooltip.
 */
describe('folding', () => {
	const DEEP = [
		{ label: 'Your folders', href: '/browse?folders=1' },
		{ label: 'Holidays', href: '/browse?folders=a' },
		{ label: 'Coast', href: '/browse?folders=b' },
		{ label: 'Harbour', href: '/browse?folders=c' },
		{ label: 'Boats' }
	];
	let deep: HTMLDivElement;
	let drawn: Record<string, unknown> | null = null;

	function drawDeep(props: { crumbs: typeof DEEP; fit?: boolean }) {
		deep = document.createElement('div');
		document.body.append(deep);
		drawn = mount(Breadcrumbs, { target: deep, props });
		flushSync();
	}

	afterEach(() => {
		removeStyles();
		document.querySelectorAll('[role="tooltip"]').forEach((bubble) => bubble.remove());
		if (drawn) unmount(drawn);
		drawn = null;
		deep?.remove();
		document.querySelectorAll('[role="menu"]').forEach((menu) => menu.remove());
	});

	it('keeps the first and the last two, and folds what is between', () => {
		const parts = folding(DEEP, 'middle');
		expect(parts.before.map((one) => one.label)).toEqual(['Your folders']);
		expect(parts.folded.map((one) => one.label)).toEqual(['Holidays', 'Coast']);
		expect(parts.after.map((one) => one.label)).toEqual(['Harbour', 'Boats']);
	});

	it('folds nothing under four steps', () => {
		expect(folding(DEEP.slice(-3), 'middle').folded).toEqual([]);
	});

	it('draws the fold at four steps, with the current page last and not a link', async () => {
		drawDeep({ crumbs: DEEP.slice(-4) });
		const nav = deep.querySelector('nav[aria-label="Breadcrumb"]');
		expect(nav?.querySelector('ol')).not.toBeNull();
		const press = nav!.querySelector<HTMLButtonElement>('button.fold');
		expect(press, 'no fold at four steps').not.toBeNull();
		expect(press!.getAttribute('aria-label')).toBe('Show the folded steps');
		expect([...nav!.querySelectorAll('a')].map((link) => link.textContent?.trim())).toEqual([
			'Holidays',
			'Harbour'
		]);
		const here = nav!.querySelector('[aria-current="page"]');
		expect(here?.textContent?.trim()).toBe('Boats');
		expect(here?.closest('a')).toBeNull();

		/* The library opens on the way down. The folded steps are links, outermost first. */
		press!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
		flushSync();
		await tick();
		const rows = [...document.querySelectorAll<HTMLAnchorElement>('[role="menu"] a[href]')];
		expect(rows.map((row) => [row.textContent?.trim(), row.getAttribute('href')])).toEqual([
			['Coast', '/browse?folders=b']
		]);
	});

	it('draws no fold for three steps', () => {
		drawDeep({ crumbs: DEEP.slice(-3) });
		expect(deep.querySelector('button.fold')).toBeNull();
		expect(deep.querySelectorAll('li')).toHaveLength(3);
	});

	it('keeps to one line in the top bar, ending in an ellipsis rather than folding further', () => {
		drawDeep({ crumbs: DEEP, fit: true });
		const nav = deep.querySelector<HTMLElement>('nav[aria-label="Breadcrumb"]')!;
		/* The same steps as a trail with all the room it wants: room never folds it. */
		expect(nav.querySelectorAll('a')).toHaveLength(2);
		expect(nav.querySelector('[aria-current="page"]')?.textContent?.trim()).toBe('Boats');
		applyStyles(source, nav);
		const line = getComputedStyle(nav.querySelector('ol')!);
		expect(line.textOverflow).toBe('ellipsis');
		expect(line.whiteSpace).toBe('nowrap');
		expect(line.overflow).toBe('hidden');
		expect(getComputedStyle(nav.querySelector('li')!).display, 'each word cut alone').toBe(
			'inline'
		);
	});

	it('names the whole path in its tooltip', async () => {
		drawDeep({ crumbs: DEEP, fit: true });
		const box = deep.querySelector<HTMLElement>('.wrap')!;
		box.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerType: 'mouse' }));
		await vi.waitFor(() =>
			expect(document.querySelector('[role="tooltip"]')?.textContent).toBe(
				'Your folders \u203a Holidays \u203a Coast \u203a Harbour \u203a Boats'
			)
		);
	});

	it('has no tooltip of its own where it may wrap', () => {
		drawDeep({ crumbs: DEEP });
		expect(deep.querySelector('.wrap > .target > nav')).toBeNull();
		expect(deep.querySelector('nav')?.classList.contains('fit')).toBe(false);
	});
});
