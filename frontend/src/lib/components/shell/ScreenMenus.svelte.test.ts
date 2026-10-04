import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount, type Component } from 'svelte';

import ScreenMenus from './ScreenMenus.svelte';
import { rail } from './rail-state.svelte';
import { FILTERS_PANEL, SORT_MENU, screenBar } from './screen-bar.svelte';

/*
 * A second panel id, for the tests about the row's rule rather than any one menu. The row has one
 * panel and one menu on it (the kept filters are at the foot of the filter panel), so a made-up id
 * keeps those tests about two.
 */
const SECOND_PANEL = 'a second panel';

/* A screen's own token. The bar draws Filter and Sort dimmed until a screen says they act, and a
   dimmed trigger is deliberately inert, so a test of HOVERING that never published anything would
   be hovering two controls that are switched off, and would pass while proving nothing. */
const SCREEN = Symbol('a screen under the bar');

/*
 * A menu opens because somebody pointed at it, and never because the row arrived under the pointer.
 *
 * The collapse button is on this row, so collapsing the sidebar slides the row under a pointer that
 * has not moved, and `mouseenter` fires on that: it fires whenever a pointer and an element start
 * overlapping, whichever did the moving. Scheduling on `mousemove` alone is not enough, because the
 * next thing anybody does after collapsing is move the mouse, over the trigger the collapse parked
 * them on. Asking whether the pointer crossed onto the trigger is not the answer either: it needs
 * an unbroken stream of mousemoves, and the desktop window's top bar is a drag region that hands
 * the page none, so menus would stop opening on hover at all.
 *
 * What is guarded against is one event, the row moving, so that is what is watched. The rail
 * collapsing parks the row; the row stays parked until the pointer is somewhere else; pointing is
 * otherwise left alone. Both halves are tested here, and so is sliding from one menu to the next.
 *
 * jsdom gives every element a zero-sized box at the origin, so the tests that turn on geometry say
 * where the row is. "The pointer is off the row" is a question about a position, and a test of it
 * that did not say where anything was would be testing nothing.
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
		/*
		 * A panel the screen publishes, standing in for any two of the row's triggers: what is
		 * tested is the row's rule for them, so a published panel is a truer stand-in than a
		 * built-in. `content` is never rendered by this component; the bar below it draws a panel.
		 */
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
		/*
		 * Sliding from an open menu to the one beside it is how a row of menus is used, and a guard
		 * that needs a crossing breaks it. Nothing about a second menu is different from the first.
		 *
		 * Filter to Saved, stepping over the order chooser between them: not because pointing at it
		 * does nothing (it opens, like the other two) but because this test is about the two that
		 * drop panels, and the order's own behaviour has its own describe below.
		 */
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
		/* A mousemove fires many times while a pointer crosses a button. The first schedules the open
		   and the rest have to be ignored: rescheduling on each one leaves a timer permanently a dwell
		   away from firing, which is a menu that never opens however long you hover. */
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
		/*
		 * Opening the orders on hover cannot take the keyboard: this library's Select keeps focus
		 * on its trigger and drives the list with `aria-activedescendant`, so a pointer crossing
		 * the bar does not pull the caret out of anything being typed.
		 */
		moveTo(trigger('Sort by'), 150, 118);
		pastTheDwell();

		expect(screenBar.open).toBe(SORT_MENU);
	});

	it('is what is open, so pointing at a panel beside it takes its place', () => {
		/* One row, one open thing. All three answer to the same store, which is what makes moving
		   along the row swap them rather than stack them. */
		moveTo(trigger('Sort by'), 150, 118);
		pastTheDwell();
		expect(screenBar.open).toBe(SORT_MENU);

		moveTo(trigger('Filter'), 120, 118);
		pastTheDwell();

		expect(screenBar.open).toBe(FILTERS_PANEL);
	});

	it('takes a click beside its open list, and leaves the row pressable', () => {
		/*
		 * A list opened by a press does not fall shut on leaving, so the next click is often on the
		 * wall to dismiss it. That click must close the list and open nothing. The sheet that takes
		 * it has a hole over this row, so sliding along the row still reaches the next trigger.
		 */
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

/*
 * Escape shuts what the bar has open, and says it took the key. The screen under the bar hears the
 * same keydown on the same window, and the Downloads screen's Escape takes a layer off its list;
 * without the mark one press would do both, shutting the panel and sending the tab back to All.
 */
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
		/*
		 * Reaching order: the two that act on the screen in front of somebody come first, and the
		 * one that is never unavailable sits at the end, always in the same place. A row whose
		 * order drifts is a row people stop aiming at.
		 */
		const names = [...host.querySelectorAll('button')].map((b) => b.getAttribute('aria-label'));

		expect(names.slice(0, 3)).toEqual(['Filter', 'Sort by', 'Walls']);
	});

	it("puts the screen's menus before its panels: Theater's Layouts, then Layout Presets", () => {
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
					label: 'Layout Presets',
					lead: true,
					content: (() => undefined) as unknown as Component<Record<string, never>>
				}
			]
		});
		flushSync();
		const names = [...host.querySelectorAll('button')].map((b) => b.getAttribute('aria-label'));

		expect(names.indexOf('Layouts'), 'no Layouts on the row').toBeGreaterThan(1);
		expect(names.indexOf('Layout Presets')).toBeGreaterThan(names.indexOf('Layouts'));
	});
});
