/*
 * The bar under a finger.
 *
 * A finger is "over" something only while it touches, and the browser says it left the moment it
 * lifts, before the click that finishes the tap. Read as a pointer leaving, every tap on an icon in
 * the drawer would shut it under the finger, and the click would go to the picture behind. And a
 * press refused on the way down is, to WebKit, a tap that never happened: no click at all.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import PlayerBarMenuProbe from './PlayerBarMenuProbe.test.svelte';

let host: HTMLElement;
let mounted: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
});

function render() {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PlayerBarMenuProbe, {
		target: host,
		props: { onhold: vi.fn(), onrelease: vi.fn() }
	});
	flushSync();
}

const tray = () => host.querySelector('.tray') as HTMLElement;
const drawerOpen = () => host.querySelector('.tray .bridge') !== null;
const moreControls = () =>
	(host.querySelector('[aria-label="More controls"]') as HTMLElement).closest('button')!;

/** The finger's own events around a tap, in the order a phone's browser raises them. */
function fingerOn(target: HTMLElement): void {
	tray().dispatchEvent(new PointerEvent('pointerenter', { bubbles: false, pointerType: 'touch' }));
	target.dispatchEvent(
		new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerType: 'touch' })
	);
	target.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerType: 'touch' }));
	tray().dispatchEvent(new PointerEvent('pointerleave', { bubbles: false, pointerType: 'touch' }));
	flushSync();
}

describe('the drawer under a finger', () => {
	it('is not opened by a finger arriving on it, only by the press', () => {
		render();
		fingerOn(moreControls());
		expect(drawerOpen(), 'the finger arriving opened it').toBe(false);

		moreControls().click();
		flushSync();
		expect(drawerOpen(), 'the press did not open it').toBe(true);
	});

	it('stays open when a finger lifts off something in it', () => {
		render();
		moreControls().click();
		flushSync();
		const inside = host.querySelector('.tray .bridge [aria-haspopup]') as HTMLElement;

		fingerOn(inside);

		expect(drawerOpen(), 'the finger lifting shut the drawer under the tap').toBe(true);
	});

	it("is shut by a second press, which is the finger's way out", () => {
		render();
		moreControls().click();
		flushSync();
		moreControls().click();
		flushSync();
		expect(drawerOpen()).toBe(false);
	});
});

describe('a finger pressing a button on the bar', () => {
	it('is never refused on the way down, which would cost the tap its click', () => {
		render();
		const down = new PointerEvent('pointerdown', {
			bubbles: true,
			cancelable: true,
			pointerType: 'touch'
		});
		moreControls().dispatchEvent(down);
		expect(down.defaultPrevented).toBe(false);
	});

	it('while a mouse still keeps the focus where it was', () => {
		render();
		const down = new PointerEvent('pointerdown', {
			bubbles: true,
			cancelable: true,
			pointerType: 'mouse'
		});
		moreControls().dispatchEvent(down);
		expect(down.defaultPrevented).toBe(true);
	});
});
