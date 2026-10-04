/*
 * An entity page's tabs across a change of wall: the wall being left stays over the new one, out
 * of reach, until the new one has its answer, so a press never shows an empty screen between two
 * full ones; and the new wall is drawn from the first moment, so it asks at once.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import TabHoldHarness from './TabHoldHarness.svelte';

let host: HTMLElement;
let instance: Record<string, unknown> | null = null;

afterEach(() => {
	if (instance) void unmount(instance, { outro: false });
	instance = null;
	host?.remove();
	vi.useRealTimers();
});

function draw(first: string) {
	host = document.createElement('div');
	document.body.append(host);
	const arrivals = new Map<string, () => void>();
	const props = $state({
		tab: first,
		onwall: (tab: string, arrived: () => void) => void arrivals.set(tab, arrived)
	});
	instance = mount(TabHoldHarness, { target: host, props });
	flushSync();
	return { props, arrivals };
}

/** What is on screen: each wall's tab, and whether it is the one held over the chosen one. */
function layers(): string[] {
	return [...host.querySelectorAll('.layer')].map((layer) => {
		const tab = layer.querySelector('.wall')?.getAttribute('data-tab') ?? 'nothing';
		return layer.classList.contains('held') ? `${tab} held` : tab;
	});
}

async function settle() {
	for (let turn = 0; turn < 4; turn += 1) await tick();
	flushSync();
}

describe('a tab on another wall', () => {
	it('keeps the wall being left over the new one until the new one has its answer', async () => {
		const { props, arrivals } = draw('files');
		arrivals.get('files')?.();
		expect(layers()).toEqual(['files']);

		const seen: string[][] = [];
		const watcher = new MutationObserver(() => seen.push(layers()));
		watcher.observe(host, { childList: true, subtree: true, attributes: true });

		props.tab = 'people';
		flushSync();
		// Both walls are drawn: the new one asks at once, under the old one, which is out of reach.
		expect(layers()).toEqual(['files held', 'people']);
		const held = host.querySelector<HTMLElement>('.layer.held');
		expect(held?.inert || held?.hasAttribute('inert')).toBe(true);

		arrivals.get('people')?.();
		// The entrance is looked for two frames after the answer.
		await new Promise((done) => setTimeout(done, 100));
		await settle();
		watcher.disconnect();

		expect(layers()).toEqual(['people']);
		// Never a moment with no wall on screen.
		expect(seen.every((one) => one.length > 0)).toBe(true);
	});

	it('hands a second tab on the same wall to that wall, holding nothing', () => {
		const { props } = draw('people');
		props.tab = 'tags';
		flushSync();
		expect(layers()).toEqual(['tags']);
	});

	it('lets the old wall go after a while when the new one never answers', () => {
		vi.useFakeTimers();
		const { props } = draw('files');
		props.tab = 'loops';
		flushSync();
		expect(layers()).toEqual(['files held', 'loops']);
		vi.advanceTimersByTime(2000);
		flushSync();
		expect(layers()).toEqual(['loops']);
	});

	it('keeps the wall on screen held when another tab is pressed before the new wall answered', () => {
		const { props, arrivals } = draw('files');
		arrivals.get('files')?.();
		props.tab = 'people';
		flushSync();
		props.tab = 'loops';
		flushSync();
		expect(layers()).toEqual(['files held', 'loops']);
		props.tab = 'files';
		flushSync();
		expect(layers()).toEqual(['files']);
	});

	it('ignores an answer from the wall being left', async () => {
		const { props, arrivals } = draw('files');
		props.tab = 'people';
		flushSync();
		arrivals.get('files')?.();
		await settle();
		expect(layers()).toEqual(['files held', 'people']);
	});
});
