/*
 * A drag across a card wall in swap mode picks the cards FOR THE SWAP, the way a drag across a wall
 * of files does, and leaves the wall's own selection (the bar that shares and hides) alone. Out of
 * the mode the same drag is the wall's selection, as it always was.
 *
 * Real cards, a real wall gesture handed to them as the People wall hands it, the pointer's place
 * read off one card per hundred pixels.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import EntityCard from '$lib/components/entity/EntityCard.svelte';
import { Selection } from '$lib/components/common/selection.svelte';
import { TileGesture } from '$lib/components/common/tile-gesture.svelte';
import { swapMode } from './mode.svelte';

const PEOPLE = [
	{ id: 'p-ava', name: 'Ava Example' },
	{ id: 'p-bryn', name: 'Bryn Calloway' },
	{ id: 'p-orla', name: 'Orla Tennant' }
];

let drawn: Record<string, unknown>[] = [];
let wall: Selection;

beforeEach(() => {
	vi.useFakeTimers();
	wall = new Selection();
	const gesture = new TileGesture(wall, () => PEOPLE.map((one) => one.id));
	for (const person of PEOPLE) {
		const host = document.createElement('div');
		document.body.append(host);
		drawn.push(
			mount(EntityCard, {
				target: host,
				props: {
					href: `/people/${person.id}`,
					name: person.name,
					id: person.id,
					swapAs: { kind: 'person', id: person.id },
					onpressstart: (event: PointerEvent) => gesture.pressStart(person.id, event),
					onpressend: () => gesture.pressEnd(),
					onclickcapture: (event: MouseEvent) => gesture.clicked(person.id, event)
				}
			}) as Record<string, unknown>
		);
	}
	flushSync();
	const cards = [...document.querySelectorAll<HTMLElement>('.card')];
	document.elementFromPoint = (x: number) => cards[Math.floor(x / 100)] ?? null;
});

afterEach(() => {
	for (const one of drawn) void unmount(one);
	drawn = [];
	vi.useRealTimers();
	swapMode.leave();
	document.body.innerHTML = '';
});

/** Hold the first card, sweep to the last, let go, and the click the let-go makes. */
function holdAndSweep(): void {
	const cards = [...document.querySelectorAll<HTMLElement>('.card')];
	cards[0].dispatchEvent(
		new PointerEvent('pointerdown', { bubbles: true, button: 0, clientX: 50, clientY: 5 })
	);
	vi.advanceTimersByTime(400);
	window.dispatchEvent(new PointerEvent('pointermove', { clientX: 150, clientY: 5 }));
	window.dispatchEvent(new PointerEvent('pointermove', { clientX: 250, clientY: 5 }));
	window.dispatchEvent(new PointerEvent('pointerup'));
	cards[2].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
	flushSync();
}

describe('a card wall in swap mode', () => {
	it('picks every card a drag crosses for the swap, and nothing for the wall', () => {
		swapMode.enter();
		flushSync();

		holdAndSweep();

		expect(swapMode.picks.map((one) => [one.kind, one.id, one.name])).toEqual([
			['person', 'p-ava', 'Ava Example'],
			['person', 'p-bryn', 'Bryn Calloway'],
			['person', 'p-orla', 'Orla Tennant']
		]);
		expect(wall.count).toBe(0);
	});

	it('is the wall selection out of the mode, as it always was', () => {
		holdAndSweep();

		expect(wall.count).toBe(3);
		expect(swapMode.picks).toEqual([]);
	});
});
