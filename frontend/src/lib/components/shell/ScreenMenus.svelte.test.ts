import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount, type Component } from 'svelte';

import ScreenMenus from './ScreenMenus.svelte';
import { rail } from './rail-state.svelte';
import { FILTERS_PANEL, SORT_MENU, screenBar } from './screen-bar.svelte';

// A made-up second panel id, for tests about the row's rule rather than one menu.
const SECOND_PANEL = 'a second panel';

// Dimmed triggers are inert, so the hover tests need a screen that publishes.
const SCREEN = Symbol('a screen under the bar');

/*
 * A menu opens because somebody pointed at it, never because the row moved under the pointer
 * (the rail collapsing parks it until the pointer leaves). jsdom boxes sit at the origin, so the
 * tests that turn on geometry say where the row is.
 */

let host: HTMLElement;
let mounted: Record<string, unknown>;

beforeEach(() => {
	vi.useFakeTimers();
	screenBar.close();
	screenBar.publish(SCREEN, {
		filterable: true,
		sorts: [{ value: 'newest', label: 'Newest' }],
		sort: 'newest',
		// A published panel stands in for any second trigger; `content` is never rendered here.
		panels: [
			{
				id: SECOND_PANEL,
				icon: 'browse',
				label: 'Walls',
				lead: true,
				content: (() => undefined) as unknown as Component<Record<string, never>>
			}
		]
	});
	rail.collapsed = false;
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(ScreenMenus, { target: host }) as Record<string, unknown>;
	flushSync();
});

afterEach(() => {
	void unmount(mounted);
	host?.remove();
	screenBar.release(SCREEN);
	screenBar.close();
	rail.collapsed = false;
	vi.useRealTimers();
});

function trigger(name: string): HTMLElement {
	const button = host.querySelector(`button[aria-label="${name}"]`);
	expect(button, `the ${name} trigger is not on the row`).not.toBeNull();
	return button as HTMLElement;
}

/** Where the ROW is, so "the pointer has left it" means something. 100..200 across, 100..136 down. */
const ROW = { left: 100, top: 100, right: 200, bottom: 136 };

function placeRow() {
	const row = host.querySelector('.menus') as HTMLElement;
	row.getBoundingClientRect = () =>
		({ ...ROW, x: ROW.left, y: ROW.top, width: 100, height: 36, toJSON: () => ROW }) as DOMRect;
}

/** One mouse movement, landing at a point. Bubbles, because that is how the handler is reached. */
function moveTo(element: HTMLElement, x: number, y: number) {
	element.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: x, clientY: y }));
}

/** Longer than the dwell, whatever the dwell is: jsdom answers every custom property with "". */
function pastTheDwell() {
	vi.advanceTimersByTime(1000);
	flushSync();
}

describe('what opens a menu', () => {
	it('opens on hover, which is the whole point of the row', () => {
		moveTo(trigger('Walls'), 150, 118);
		pastTheDwell();

		expect(screenBar.open).toBe(SECOND_PANEL);
	});

	it('opens the NEXT one when the pointer slides along an open row', () => {
		// Sliding from an open menu to the next: a guard that needs a crossing would break it.
		moveTo(trigger('Filter'), 120, 118);
		pastTheDwell();
		expect(screenBar.open, 'the first one did not open').toBe(FILTERS_PANEL);

		moveTo(trigger('Walls'), 180, 118);
		pastTheDwell();

		expect(screenBar.open).toBe(SECOND_PANEL);
	});

	it('does NOT open when collapsing the sidebar slides the row under the pointer', () => {
		placeRow();
		rail.collapsed = true;
		flushSync();

		// The pointer never moved; the row moved. Then the first thing anybody does is jog the mouse,
		// and that jog is over a trigger they never approached.
		moveTo(trigger('Walls'), 150, 118);
		moveTo(trigger('Walls'), 150, 130);
		pastTheDwell();

		expect(screenBar.open, 'collapsing the sidebar opened a menu').toBeNull();
	});

	it('opens again once the pointer has actually left the row', () => {
		placeRow();
		rail.collapsed = true;
		flushSync();

		moveTo(trigger('Walls'), 150, 118); // still parked: refused
		pastTheDwell();
		expect(screenBar.open, 'the parked pointer opened one').toBeNull();

		// Off the row entirely (which is what says the pointer is somewhere it WENT rather than
		// somewhere it was put) and then back onto a trigger.
		document.dispatchEvent(
			new MouseEvent('mousemove', { bubbles: true, clientX: 150, clientY: 400 })
		);
		moveTo(trigger('Walls'), 150, 118);
		pastTheDwell();

		expect(screenBar.open).toBe(SECOND_PANEL);
	});

	it('still opens after a dwell rather than instantly, so crossing the row opens nothing', () => {
		moveTo(trigger('Walls'), 150, 118);
		flushSync();

		expect(screenBar.open, 'it opened before the dwell had elapsed').toBeNull();
	});

	it('does not restart the dwell on every move, or it would never elapse', () => {
		// Many mousemoves cross a button; rescheduling on each would never open the menu.
		const one = trigger('Walls');
		for (let i = 0; i < 12; i++) {
			moveTo(one, 150 + (i % 3), 118 + (i % 3));
			vi.advanceTimersByTime(40);
		}
		flushSync();

		expect(screenBar.open).toBe(SECOND_PANEL);
	});
});

describe('the order chooser, which is a menu rather than a panel', () => {
	it('opens on the dwell, exactly as the two panels beside it do', () => {
		// Hover-opening the orders keeps focus on the trigger (`aria-activedescendant`).
		moveTo(trigger('Sort by'), 150, 118);
		pastTheDwell();

		expect(screenBar.open).toBe(SORT_MENU);
	});

	it('is what is open, so pointing at a panel beside it takes its place', () => {
		// One row, one open thing: moving along the row swaps them.
		moveTo(trigger('Sort by'), 150, 118);
		pastTheDwell();
		expect(screenBar.open).toBe(SORT_MENU);

		moveTo(trigger('Filter'), 120, 118);
		pastTheDwell();

		expect(screenBar.open).toBe(FILTERS_PANEL);
	});

	it('takes a click beside its open list, and leaves the row pressable', () => {
		// A pressed list stays on leaving; the dismissing click closes it and opens nothing.
		placeRow();
		screenBar.show(SORT_MENU);
		flushSync();
		vi.advanceTimersByTime(100);
		flushSync();

		const shield = document.querySelector('.page-shield');
		expect(shield, 'the open order list left the page under it pressable').not.toBeNull();
		expect(shield?.getAttribute('style') ?? '').toContain(
			'100px 100px, 200px 100px, 200px 136px, 100px 136px'
		);
	});

	it('opens nothing when the screen published no orders', () => {
		screenBar.publish(SCREEN, { filterable: true });
		flushSync();

		moveTo(trigger('Sort by'), 150, 118);
		pastTheDwell();

		expect(trigger('Sort by').hasAttribute('disabled'), 'it was still live with no orders').toBe(
			true
		);
		expect(screenBar.open, 'a dimmed trigger opened its menu').toBeNull();
	});
});

// Escape is marked as taken, or the screen below would act on the same press.
describe('Escape', () => {
	it('shuts what is open and marks the key as used', () => {
		screenBar.show(FILTERS_PANEL);
		const key = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true });
		window.dispatchEvent(key);
		expect(screenBar.open).toBeNull();
		expect(key.defaultPrevented).toBe(true);
	});

	it('leaves the key alone when nothing is open, so the screen may have it', () => {
		const key = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true });
		window.dispatchEvent(key);
		expect(key.defaultPrevented).toBe(false);
	});
});

describe('the order of the row', () => {
	it('is Filter, then Sort, then whatever the screen published', () => {
		// The two that act on the screen first, the always-available one last.
		const names = [...host.querySelectorAll('button')].map((b) => b.getAttribute('aria-label'));

		expect(names.slice(0, 3)).toEqual(['Filter', 'Sort by', 'Walls']);
	});

	it("puts the screen's menus before its panels: Theater's Layouts, then Saved Layouts", () => {
		screenBar.publish(SCREEN, {
			filterable: true,
			sorts: [{ value: 'newest', label: 'Newest' }],
			sort: 'newest',
			menus: [
				{
					id: 'layout',
					icon: 'view_array',
					label: 'Layouts',
					options: [{ value: 'single', label: '1x1' }],
					value: 'single',
					onChoose: () => {}
				}
			],
			panels: [
				{
					id: 'presets',
					icon: 'table_view',
					label: 'Saved Layouts',
					lead: true,
					content: (() => undefined) as unknown as Component<Record<string, never>>
				}
			]
		});
		flushSync();
		const names = [...host.querySelectorAll('button')].map((b) => b.getAttribute('aria-label'));

		expect(names.indexOf('Layouts'), 'no Layouts on the row').toBeGreaterThan(1);
		expect(names.indexOf('Saved Layouts')).toBeGreaterThan(names.indexOf('Layouts'));
	});
});
