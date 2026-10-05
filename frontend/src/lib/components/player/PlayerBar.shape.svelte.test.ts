/* The bar keeps one shape: the drawer's button and the step pair are drawn whatever the caller
 * hands over, dimmed with a reason, except where a finger's swipe steps instead. */

import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import PlayerBar from './PlayerBar.svelte';
import { finger } from './finger.svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';

let host: HTMLElement;
let mounted: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
	finger.yes = false;
	phoneWidth.yes = false;
});

function draw(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PlayerBar, {
		target: host,
		props: {
			position: 0,
			duration: 30,
			onseek: () => {},
			playing: false,
			onplay: () => {},
			shuffle: { on: false, onpress: () => {} },
			repeat: { mode: 'loop_all', onpress: () => {} },
			muted: false,
			volume: 1,
			onmute: () => {},
			onvolume: () => {},
			...props
		}
	});
	flushSync();
}

const button = (label: string) =>
	host.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement | null;

it('draws the drawer button dimmed with its reason when there is no drawer', () => {
	draw({ trayWhy: 'This one is hidden' });
	expect(button('This one is hidden')?.disabled).toBe(true);
});

it('stands the transport first on a row with nothing to lead with', () => {
	draw();
	const row = host.querySelector('.row') as HTMLElement;
	expect(row.classList.contains('led')).toBe(false);
	expect(row.firstElementChild?.classList.contains('middle')).toBe(true);
});

it('stands the step pair down only where a finger on a phone swipes instead', () => {
	draw();
	expect(button('Nothing before this')?.disabled).toBe(true);
	unmount(mounted!);
	host.remove();

	phoneWidth.yes = true;
	finger.yes = true;
	draw({ onback: () => {}, onforward: () => {} });
	expect(button('Previous')).toBeNull();
	expect(button('Next')).toBeNull();
});

it("holds a clock's height on the scrub line with no clock, except on a compact bar", () => {
	draw({ timed: false });
	expect(host.querySelector('.scrub-line')?.classList.contains('steady')).toBe(true);
	unmount(mounted!);
	host.remove();

	draw({ timed: false, compact: true });
	expect(host.querySelector('.scrub-line')?.classList.contains('steady')).toBe(false);
});
