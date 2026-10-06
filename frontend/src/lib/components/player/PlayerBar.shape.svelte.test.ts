/* The bar keeps one shape: the drawer's button and the step pair are drawn whatever the caller
 * hands over, dimmed with a reason, except where a finger's swipe steps instead. */

import { afterEach, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
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

/* Beside the clocks the row stands under the timeline, its outer buttons flush with its ends. */
it('stands the row under the timeline wherever the scrub line keeps its clocks', () => {
	const under = () => host.querySelector('.player-bar')?.classList.contains('under-line');
	const again = (props: Record<string, unknown>) => {
		if (mounted) unmount(mounted);
		host.remove();
		draw(props);
		return under();
	};
	draw();
	expect(under()).toBe(true);
	expect(again({ variant: 'theater', timed: false })).toBe(true);
	expect(again({ timed: false })).toBe(false);
	expect(again({ compact: true })).toBe(false);
	phoneWidth.yes = true;
	expect(again({})).toBe(false);
});

/* A row that wants more than the whole bar keeps the bar's own edges and gives way inside them. */
it('leaves the timeline for a row wider than the whole bar, and comes back when it fits', () => {
	const real = globalThis.ResizeObserver;
	const read: Array<() => void> = [];
	globalThis.ResizeObserver = class {
		constructor(heard: ResizeObserverCallback) {
			read.push(() => heard([], this as unknown as ResizeObserver));
		}
		observe() {}
		unobserve() {}
		disconnect() {}
	} as unknown as typeof ResizeObserver;
	try {
		draw();
		const bar = host.querySelector('.player-bar') as HTMLElement;
		const parts = [...bar.querySelector('.row')!.children] as HTMLElement[];
		const each = (px: number) => {
			for (const part of parts)
				Object.defineProperty(part, 'scrollWidth', { value: px, configurable: true });
			for (const again of read) again();
			flushSync();
			return bar.classList.contains('under-line');
		};
		Object.defineProperty(bar, 'clientWidth', { value: 24 + 300 * parts.length });
		bar.style.paddingLeft = '12px';
		bar.style.paddingRight = '12px';
		expect(each(300)).toBe(true);
		expect(each(301)).toBe(false);
		expect(each(300)).toBe(true);
	} finally {
		globalThis.ResizeObserver = real;
	}
});

/* Told what the rest of the row takes, so the lead can fold before the row gives way. */
it('hands its lead the width the rest of the row takes beside it', () => {
	const real = globalThis.ResizeObserver;
	const read: Array<() => void> = [];
	globalThis.ResizeObserver = class {
		constructor(heard: ResizeObserverCallback) {
			read.push(() => heard([], this as unknown as ResizeObserver));
		}
		observe() {}
		unobserve() {}
		disconnect() {}
	} as unknown as typeof ResizeObserver;
	let beside = () => -1;
	const lead = createRawSnippet<[number]>((given) => ({
		render: () => '<span></span>',
		setup: () => {
			beside = given;
		}
	}));
	try {
		draw({ lead });
		const parts = [...host.querySelector('.player-bar > .row')!.children] as HTMLElement[];
		expect(parts).toHaveLength(3);
		for (const part of parts) {
			const px = part.classList.contains('start') ? 50 : 100;
			Object.defineProperty(part, 'scrollWidth', { value: px, configurable: true });
		}
		for (const again of read) again();
		flushSync();
		expect(beside()).toBe(200);
	} finally {
		globalThis.ResizeObserver = real;
	}
});
