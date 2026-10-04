/*
 * A pointer resting on a row is answered by that row, even while a neighbour's flyout is open,
 * and a pointer on its way to the flyout is not.
 *
 * The library lets go of a flyout only after half a second with no movement at all, and a resting
 * hand moves a pixel now and then, so on the library's rule alone a row beside a tall flyout would
 * never answer a pointer resting on it. These hold the rule added to it: resting means one row, no
 * ground gained towards the flyout, for `RESTING_MS`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import {
	HEADING_PX,
	RESTING_MS,
	RestingPointer,
	givesWayToARestingPointer,
	neighbourRow
} from './menu-resting.svelte';
import contextMenuItem from './ContextMenuItem.svelte?raw';
import pickMenu from './PickMenu.svelte?raw';

beforeEach(() => {
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
	document.body.innerHTML = '';
});

describe('RestingPointer', () => {
	it('answers a resting hand whose tremor gains no ground', () => {
		const settled = vi.fn();
		const watch = new RestingPointer(settled);
		const row = document.createElement('div');
		for (let i = 0; i < 20; i++) {
			watch.over(row, 100 + (i % 2), 'right');
			vi.advanceTimersByTime(RESTING_MS / 4);
			if (settled.mock.calls.length) break;
		}
		expect(settled).toHaveBeenCalledTimes(1);
		expect(settled).toHaveBeenCalledWith(row);
	});

	it('waits while the pointer keeps gaining ground towards the flyout', () => {
		const settled = vi.fn();
		const watch = new RestingPointer(settled);
		const row = document.createElement('div');
		for (let i = 0; i < 40; i++) {
			watch.over(row, 100 + i * HEADING_PX, 'right');
			vi.advanceTimersByTime(RESTING_MS / 2);
		}
		expect(settled).not.toHaveBeenCalled();
	});

	it('reads ground towards a flyout on the left as leftwards', () => {
		const settled = vi.fn();
		const watch = new RestingPointer(settled);
		const row = document.createElement('div');
		for (let i = 0; i < 10; i++) {
			watch.over(row, 500 - i * HEADING_PX, 'left');
			vi.advanceTimersByTime(RESTING_MS / 2);
		}
		expect(settled).not.toHaveBeenCalled();
		watch.over(row, 500 - 9 * HEADING_PX + HEADING_PX, 'left');
		vi.advanceTimersByTime(RESTING_MS);
		expect(settled).toHaveBeenCalledTimes(1);
	});

	it('starts again on each new row, and stops once the pointer is away', () => {
		const settled = vi.fn();
		const watch = new RestingPointer(settled);
		const one = document.createElement('div');
		const two = document.createElement('div');
		watch.over(one, 100, 'right');
		vi.advanceTimersByTime(RESTING_MS - 10);
		watch.over(two, 100, 'right');
		vi.advanceTimersByTime(RESTING_MS - 10);
		expect(settled).not.toHaveBeenCalled();
		vi.advanceTimersByTime(10);
		expect(settled).toHaveBeenCalledWith(two);

		watch.over(one, 100, 'right');
		watch.away();
		vi.advanceTimersByTime(RESTING_MS * 2);
		expect(settled).toHaveBeenCalledTimes(1);
	});
});

/** A menu of three rows, the middle one open onto a flyout drawn elsewhere, as the library draws it. */
function menu(): { trigger: HTMLElement; above: HTMLElement; flyoutRow: HTMLElement } {
	document.body.innerHTML = `
		<div role="menu" id="outer">
			<div role="menuitem" id="above">Add to</div>
			<div role="menuitem" id="trigger" aria-haspopup="menu" aria-controls="fly">Rating</div>
			<div role="menuitemcheckbox" id="below">Auto-enrich</div>
		</div>
		<div role="menu" id="fly"><div role="menuitem" id="star">5</div></div>`;
	const at = (id: string) => document.getElementById(id) as HTMLElement;
	return { trigger: at('trigger'), above: at('above'), flyoutRow: at('star') };
}

function move(on: Element, x: number, pointerType = 'mouse'): void {
	on.dispatchEvent(
		new PointerEvent('pointermove', { bubbles: true, clientX: x, clientY: 10, pointerType })
	);
}

describe('neighbourRow', () => {
	it('is a row of the same menu, never the trigger and never a row of the flyout', () => {
		const { trigger, above, flyoutRow } = menu();
		expect(neighbourRow(trigger, above)).toBe(above);
		expect(neighbourRow(trigger, document.getElementById('below'))).not.toBeNull();
		expect(neighbourRow(trigger, trigger)).toBeNull();
		expect(neighbourRow(trigger, flyoutRow)).toBeNull();
		expect(neighbourRow(trigger, document.body)).toBeNull();
	});
});

describe('givesWayToARestingPointer', () => {
	function mountRule(trigger: HTMLElement): { open: boolean; stop: () => void } {
		const state = $state({ open: true });
		const stop = $effect.root(() => {
			givesWayToARestingPointer({
				open: () => state.open,
				close: () => (state.open = false),
				trigger: () => trigger
			});
		});
		flushSync();
		return {
			get open() {
				return state.open;
			},
			stop
		};
	}

	it('closes the flyout for a pointer resting on a neighbour, and hands that row the pointer', async () => {
		const { trigger, above } = menu();
		const heard = vi.fn();
		above.addEventListener('pointermove', (event) => {
			if (!event.isTrusted) heard((event as PointerEvent).pointerType);
		});
		const rule = mountRule(trigger);
		for (let i = 0; i < 6; i++) {
			move(above, 100 + (i % 2));
			vi.advanceTimersByTime(RESTING_MS / 3);
		}
		expect(rule.open).toBe(false);
		await vi.runAllTimersAsync();
		flushSync();
		/* The six moves of the hand, then the one handed over, after the close. */
		expect(heard).toHaveBeenLastCalledWith('mouse');
		expect(heard).toHaveBeenCalledTimes(7);
		rule.stop();
	});

	it('leaves the flyout open while the pointer is on it, and ignores a finger', () => {
		const { trigger, above, flyoutRow } = menu();
		const rule = mountRule(trigger);
		for (let i = 0; i < 10; i++) {
			move(flyoutRow, 100 + (i % 2));
			move(above, 100 + (i % 2), 'touch');
			vi.advanceTimersByTime(RESTING_MS / 2);
		}
		expect(rule.open).toBe(true);
		rule.stop();
	});
});

describe('every row that opens out', () => {
	/* The two rows in the app that open a flyout, the menu's own and the picker's. A third that
	   opened one without this rule would be a row a resting pointer cannot reach past. */
	function givesWay(source: string): void {
		expect(source).toContain('givesWayToARestingPointer(');
		expect(source).toMatch(/<ContextMenu\.SubTrigger[^>]*bind:ref=\{\w+\}/);
	}

	it('ContextMenuItem gives way to a pointer resting on a neighbour', () => {
		givesWay(contextMenuItem);
	});

	it('PickMenu gives way to a pointer resting on a neighbour', () => {
		givesWay(pickMenu);
	});
});
