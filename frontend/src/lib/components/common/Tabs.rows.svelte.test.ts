/* The tab row as addresses and as a filter: the row of words beside an entity heading, and the
 * same row where there is no address to go to.
 *
 * Three things here are load-bearing and none of them is visible in a screenshot.
 *
 * Every tab is a real ADDRESS, not a button with a handler: that is what makes the back button step
 * between tabs and a link somebody sends open on the tab they were looking at. A row of buttons
 * would look identical and lose all of it.
 *
 * The current tab says so to a reader who cannot see that it is brighter, and it is marked a second
 * way beside the colour (a rule under the word) because a difference carried by colour alone is
 * no difference to a good share of people.
 *
 * And a count that is not yet known draws NOTHING rather than a zero. A zero that becomes an eight
 * a moment later reports a fault that is not there, on the screen somebody reads most carefully.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { compile } from 'svelte/compiler';
import source from './Tabs.svelte?raw';
import { reactiveProps } from '$lib/design/testing.svelte';
import Tabs, { type TabChoice, type TabLink } from './Tabs.svelte';

let host: HTMLElement;
let instance: Record<string, unknown> | null = null;

const TABS: TabLink[] = [
	{ id: 'files', label: 'Files', href: '/people/p1' },
	{ id: 'tags', label: 'Tags', href: '/people/p1?show=tags', count: 12 },
	{ id: 'people', label: 'Seen with', href: '/people/p1?show=people' }
];

function draw(current = 'files', tabs: TabLink[] = TABS) {
	host = document.createElement('div');
	document.body.append(host);
	instance = mount(Tabs, { target: host, props: { tabs, current } });
	flushSync();
}

function words(node: Element | null): string {
	return (node?.textContent ?? '').trim();
}

afterEach(() => {
	if (instance) void unmount(instance, { outro: false });
	instance = null;
	host?.remove();
	document.body.innerHTML = '';
});

describe('the tab strip', () => {
	it('draws every tab as a link somewhere, never as a button', () => {
		draw();

		const links = [...host.querySelectorAll('a')];
		expect(links).toHaveLength(3);
		expect(links.map((link) => link.getAttribute('href'))).toEqual([
			'/people/p1',
			'/people/p1?show=tags',
			'/people/p1?show=people'
		]);
		expect(host.querySelector('button')).toBeNull();
	});

	it('keeps the current tab IN the row rather than leaving it out', () => {
		// Left out, the row reshuffles as you move along it and the tab you are on has nowhere to
		// go back to.
		draw('tags');

		expect([...host.querySelectorAll('a')].map((link) => words(link))).toHaveLength(3);
	});

	it('says which tab is current to somebody who cannot see that it is brighter', () => {
		draw('tags');

		const current = host.querySelectorAll('[aria-current="page"]');
		expect(current).toHaveLength(1);
		expect(words(current[0])).toContain('Tags');
	});

	it('marks the current tab a second way beside its ink', () => {
		draw('people');

		const marked = host.querySelectorAll('a.here');
		expect(marked).toHaveLength(1);
		expect(words(marked[0])).toBe('Seen with');
	});

	it("keeps the current tab's rule and ink under the pointer", () => {
		/* jsdom holds no hover state, so every compiled rule that answers the pointer is read with
		   its `:hover` taken out and asked whether it could reach the current tab. */
		draw('tags');
		const css = compile(source, { filename: 'Tabs.svelte', css: 'external' }).css?.code ?? '';
		const hovers = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^}]*)\}/g)]
			.filter((rule) => rule[1].includes(':hover') && /color/.test(rule[2]))
			.flatMap((rule) => rule[1].split(','))
			.filter((one) => one.includes(':hover'))
			.map((one) =>
				one
					.replace(/\.svelte-[\w-]+/g, '')
					.replace(/:hover/g, '')
					.trim()
			);
		expect(hovers.length).toBeGreaterThan(0);

		const current = host.querySelector('a.here') as Element;
		const other = host.querySelector('a:not(.here)') as Element;
		expect(hovers.filter((one) => current.matches(one))).toEqual([]);
		expect(hovers.some((one) => other.matches(one))).toBe(true);
	});

	it('draws a count where one is known', () => {
		draw();

		expect(words(host.querySelector('.count'))).toBe('12');
	});

	it('groups a count the way every other count on screen is grouped', () => {
		draw('files', [{ id: 'files', label: 'Files', href: '/people/p1', count: 8351 }]);

		expect(words(host.querySelector('.count'))).toBe((8351).toLocaleString());
		expect(words(host.querySelector('.count'))).not.toBe('8351');
	});

	it('draws no number at all where a wall has not answered yet', () => {
		// `undefined` is a different fact from `0`, and this is the whole reason the count is
		// optional rather than defaulted.
		draw('files', [{ id: 'loops', label: 'Loops', href: '/x?show=loops' }]);

		expect(host.querySelector('.count')).toBeNull();
		expect(words(host.querySelector('a'))).toBe('Loops');
	});

	it('draws no zero where a wall answered and found nothing, and keeps the tab', () => {
		// "Loops 0" on every person with no loops is a number nobody needs to read; the word
		// alone says there is a tab, and opening it says what is in it.
		draw('files', [{ id: 'loops', label: 'Loops', href: '/x?show=loops', count: 0 }]);

		expect(host.querySelector('.count')).toBeNull();
		expect(words(host.querySelector('a'))).toBe('Loops');
	});

	it('wears a mark on a tab something is waiting on, and says what in words', () => {
		// The whole point of the mark: a question waiting on a record is on the History tab, and
		// somebody has to be able to see that without opening it.
		draw('files', [
			{ id: 'files', label: 'Files', href: '/people/p1', count: 4 },
			{
				id: 'history',
				label: 'History',
				href: '/people/p1?show=history',
				count: 12,
				attention: 'One field a stash-box disagrees with'
			}
		]);

		const marks = host.querySelectorAll('.attention');
		expect(marks).toHaveLength(1);
		// Inside the History link and nowhere else, so it is obvious which word it belongs to.
		expect(words(marks[0].closest('a'))).toContain('History');
		// Not carried by colour alone: the sentence is on the glyph, for anything reading the page
		// out and for anybody who cannot separate the yellow from the ink.
		expect(marks[0].querySelector('[aria-label]')?.getAttribute('aria-label')).toBe(
			'One field a stash-box disagrees with'
		);
	});

	it('wears no mark when nothing is waiting, which is every ordinary record', () => {
		// The page draws the mark from a number and leaves `attention` off at nought. A mark that
		// appeared on every tab would say nothing at all.
		draw('files', [{ id: 'history', label: 'History', href: '/x?show=history', count: 12 }]);

		expect(host.querySelector('.attention')).toBeNull();
	});

	it('names the row, so it is not an unlabelled set of links', () => {
		draw();

		expect(host.querySelector('nav')?.getAttribute('aria-label')).toBe('What to show');
	});
});

/*
 * The same row where there is no address to go to.
 *
 * The Activity pane is inside the Settings sheet, which opens over a page without leaving it, so a
 * link there would navigate and tear that page down. Handed `onselect`, the row draws the same
 * words, counts and mark as a tablist that says which was pressed. What is under test is what a
 * screenshot cannot show: that it is ONE control rather than four tab stops, that the caller's
 * `current` is the only answer to which tab is lit, and that arrowing along it chooses nothing.
 */
/** A tab's words without its mark: the label and the count, as a sighted reader takes them in. The
 *  mark is a glyph whose text is its ligature, which is not a word anybody reads. */
function plain(node: Element | null): string {
	const copy = node?.cloneNode(true) as Element | undefined;
	copy?.querySelectorAll('.attention').forEach((mark) => mark.remove());
	return (copy?.textContent ?? '').replace(/\s+/g, '').trim();
}

const STATES: TabChoice[] = [
	{ id: 'all', label: 'All', count: 24 },
	{ id: 'running', label: 'Running', count: 3 },
	{ id: 'blocked', label: 'Blocked', count: 2, attention: 'Waiting on you' },
	{ id: 'failed', label: 'Failed', count: 0 }
];

function choose(extra: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	const onselect = vi.fn();
	const props = reactiveProps({
		tabs: STATES,
		current: 'all',
		label: 'Which tasks to show',
		onselect,
		controls: 'the-list',
		...extra
	});
	instance = mount(Tabs, { target: host, props });
	flushSync();

	const tabs = () => [...host.querySelectorAll('[role="tab"]')] as HTMLButtonElement[];
	const tabFor = (label: string) =>
		tabs().find((one) => one.textContent?.includes(label)) as HTMLButtonElement;
	return {
		props,
		onselect,
		tabs,
		tabFor,
		selected: () =>
			tabs()
				.filter((one) => one.getAttribute('aria-selected') === 'true')
				.map((one) => plain(one)),
		press: (label: string) => {
			tabFor(label).click();
			flushSync();
		},
		key: (label: string, key: string) => {
			const tab = tabFor(label);
			tab.focus();
			tab.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
			flushSync();
		}
	};
}

describe('the tab strip where there is no address', () => {
	it('draws a tablist of buttons, and not one link', () => {
		const row = choose();

		expect(host.querySelector('[role="tablist"]')?.getAttribute('aria-label')).toBe(
			'Which tasks to show'
		);
		expect(host.querySelector('a')).toBeNull();
		expect(row.tabs()).toHaveLength(4);
		expect(row.tabs().every((one) => one.tagName === 'BUTTON')).toBe(true);
	});

	it('draws the same words, counts and mark a page draws', () => {
		const row = choose();

		expect(row.tabs().map((one) => plain(one))).toEqual([
			'All24',
			'Running3',
			'Blocked2',
			'Failed'
		]);
		expect(
			row.tabFor('Blocked').querySelector('.attention [aria-label]')?.getAttribute('aria-label')
		).toBe('Waiting on you');
		expect(host.querySelectorAll('.attention')).toHaveLength(1);
		// The same look as a page's row: the words wear `.tab`, and the one lit wears `.here`.
		expect(host.querySelectorAll('.tab')).toHaveLength(4);
		expect(plain(host.querySelector('.here'))).toBe('All24');
	});

	it("marks exactly one tab selected, and it is the caller's current", () => {
		const row = choose({ current: 'blocked' });

		expect(row.selected()).toEqual(['Blocked2']);
	});

	it('says which list it narrows', () => {
		const row = choose();

		expect(row.tabs().every((one) => one.getAttribute('aria-controls') === 'the-list')).toBe(true);
	});

	it('tells the caller which tab was pressed', () => {
		const row = choose();

		row.press('Failed');
		expect(row.onselect).toHaveBeenCalledWith('failed');
		expect(row.onselect).toHaveBeenCalledTimes(1);
	});

	it('does not ask again for the tab already showing', () => {
		const row = choose();

		row.press('All');
		expect(row.onselect).not.toHaveBeenCalled();
	});

	it('stays where it was when the caller does not take the press', () => {
		// `onselect` is a request. A row that lit what was pressed on its own would say one state
		// while the list underneath showed another.
		const row = choose();

		row.press('Running');
		expect(row.selected()).toEqual(['All24']);
	});

	it('moves when the caller moves current, without anybody pressing it', () => {
		const row = choose();

		row.props.current = 'failed';
		flushSync();
		expect(row.selected()).toEqual(['Failed']);
	});

	it('is one tab stop, and the stop is the tab showing', () => {
		const row = choose({ current: 'running' });

		const stops = row.tabs().filter((one) => one.tabIndex === 0);
		expect(stops).toHaveLength(1);
		expect(plain(stops[0])).toBe('Running3');
	});

	it('keeps a tab stop when the tab showing is not in the row', () => {
		// A row nobody can Tab into is a row a keyboard cannot use at all.
		const row = choose({ current: 'gone' });

		const stops = row.tabs().filter((one) => one.tabIndex === 0);
		expect(stops.map((one) => plain(one))).toEqual(['All24']);
	});

	it('moves along with the arrows, round the ends, and chooses nothing doing it', () => {
		const row = choose();

		row.key('All', 'ArrowRight');
		expect(document.activeElement).toBe(row.tabFor('Running'));
		row.key('Running', 'ArrowLeft');
		expect(document.activeElement).toBe(row.tabFor('All'));
		row.key('All', 'ArrowLeft');
		expect(document.activeElement).toBe(row.tabFor('Failed'));
		expect(row.onselect).not.toHaveBeenCalled();
	});

	it('jumps to the ends with Home and End', () => {
		const row = choose();

		row.key('Running', 'End');
		expect(document.activeElement).toBe(row.tabFor('Failed'));
		row.key('Failed', 'Home');
		expect(document.activeElement).toBe(row.tabFor('All'));
	});
});
