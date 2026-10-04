/*
 * A panel opened by pointing goes away when the pointer leaves it, after a pick inside it as well
 * as before one. The library stops answering a leaving at the first press inside the panel, so on
 * its own a download folder picked from Add's list would leave the panel up until somebody pressed
 * elsewhere.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { LEAVE_MS, closesWhenThePointerLeaves, onThePanel, typingIn } from './popover-leave.svelte';

let panel: HTMLDivElement;
let trigger: HTMLButtonElement;
let elsewhere: HTMLDivElement;
let list: HTMLDivElement;

beforeEach(() => {
	document.body.innerHTML = `
		<button id="door">Add</button>
		<div id="panel"><input id="link" type="url" /><button id="chooser">Download folder</button></div>
		<div id="list" role="listbox"><div role="option" id="option">A folder</div></div>
		<div id="elsewhere">the screen</div>`;
	panel = document.getElementById('panel') as HTMLDivElement;
	trigger = document.getElementById('door') as HTMLButtonElement;
	elsewhere = document.getElementById('elsewhere') as HTMLDivElement;
	list = document.getElementById('list') as HTMLDivElement;
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
	document.body.innerHTML = '';
});

function move(over: Element, pointerType = 'mouse') {
	over.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerType }));
}

/** A hover panel, up, watched; returns the close count and the teardown. */
function watched(state: { asked?: boolean; hover?: boolean } = {}) {
	const close = vi.fn();
	const stop = $effect.root(() => {
		closesWhenThePointerLeaves({
			open: () => true,
			hover: () => state.hover ?? true,
			asked: () => state.asked ?? false,
			panel: () => panel,
			trigger: () => trigger,
			close
		});
	});
	flushSync();
	return { close, stop };
}

describe('where the pointer is', () => {
	it('is on the panel over the panel, its control, or a list opened from it', () => {
		expect(onThePanel(panel.querySelector('#chooser'), panel, trigger)).toBe(true);
		expect(onThePanel(trigger, panel, trigger)).toBe(true);
		expect(onThePanel(list.querySelector('#option'), panel, trigger)).toBe(true);
		expect(onThePanel(elsewhere, panel, trigger)).toBe(false);
	});

	it('holds the panel while a field in it has the keyboard, and not for a pressed chooser', () => {
		expect(typingIn(panel, panel.querySelector('#link'))).toBe(true);
		expect(typingIn(panel, panel.querySelector('#chooser'))).toBe(false);
		expect(typingIn(panel, elsewhere)).toBe(false);
	});
});

describe('a pointer leaving', () => {
	it('closes the panel after a pick, once the pointer has been away for the delay', () => {
		const { close, stop } = watched();
		/* The pick: the chooser pressed, its list used, the focus back on the chooser. */
		(panel.querySelector('#chooser') as HTMLButtonElement).focus();
		move(list.querySelector('#option')!);
		vi.advanceTimersByTime(LEAVE_MS * 2);
		expect(close).not.toHaveBeenCalled();

		move(elsewhere);
		vi.advanceTimersByTime(LEAVE_MS - 1);
		expect(close).not.toHaveBeenCalled();
		vi.advanceTimersByTime(1);
		expect(close).toHaveBeenCalledOnce();
		stop();
	});

	it('does not close when the pointer comes back within the delay', () => {
		const { close, stop } = watched();
		move(elsewhere);
		move(panel);
		vi.advanceTimersByTime(LEAVE_MS * 3);
		expect(close).not.toHaveBeenCalled();
		stop();
	});

	it('holds while a link is being typed', () => {
		const { close, stop } = watched();
		(panel.querySelector('#link') as HTMLInputElement).focus();
		move(elsewhere);
		vi.advanceTimersByTime(LEAVE_MS * 3);
		expect(close).not.toHaveBeenCalled();
		stop();
	});

	it('leaves a panel somebody pressed the control for, and one not opened by pointing, alone', () => {
		for (const state of [{ asked: true }, { hover: false }]) {
			const { close, stop } = watched(state);
			move(elsewhere);
			vi.advanceTimersByTime(LEAVE_MS * 3);
			expect(close).not.toHaveBeenCalled();
			stop();
		}
	});

	it('answers a mouse only', () => {
		const { close, stop } = watched();
		move(elsewhere, 'touch');
		vi.advanceTimersByTime(LEAVE_MS * 3);
		expect(close).not.toHaveBeenCalled();
		stop();
	});
});
