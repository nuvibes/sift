/*
 * In swap mode a card that will not go wears the pick's shape in the danger colour with the
 * do-not-swap mark, a press on it picks nothing and says why with the switch that changes it, and a
 * drag across it leaves it out. Out of the mode the card is drawn as it always was.
 *
 * Real cards, as the People wall hands them their rows' marks (`refusedMark`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import EntityCard from '$lib/components/entity/EntityCard.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { swapMode } from './mode.svelte';
import type { PickChoice } from '$lib/components/common/verbs';
import { refusedChoice, refusedMark, refusedWords } from './refused';

const PEOPLE = [
	{ id: 'p-ava', name: 'Ava Example', keep_local: true },
	{ id: 'p-bryn', name: 'Bryn Calloway' },
	{ id: 'p-orla', name: 'Orla Tennant', keep_from_swaps: true }
];

let drawn: Record<string, unknown>[] = [];

beforeEach(() => {
	vi.useFakeTimers();
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
					swapAs: { kind: 'person', id: person.id, refused: refusedMark(person) },
					onclickcapture: () => {}
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
	for (const one of toasts.items) toasts.dismiss(one.id);
	document.body.innerHTML = '';
});

function marks(): (string | null)[] {
	return [...document.querySelectorAll<HTMLElement>('.card')].map(
		(card) => card.querySelector('.pick')?.getAttribute('data-purpose') ?? null
	);
}

describe('a card that will not go in a swap', () => {
	it('wears the refused mark in swap mode, both marks counted, and nothing out of it', () => {
		expect(marks()).toEqual([null, null, null]);
		swapMode.enter();
		flushSync();
		expect(marks()).toEqual(['refused', null, 'refused']);
	});

	it('is not picked by a press, which says why and offers the switch', () => {
		swapMode.enter();
		flushSync();
		const faces = [...document.querySelectorAll<HTMLElement>('.card .face')];

		faces[0].click();
		faces[1].click();
		flushSync();

		expect(swapMode.picks.map((one) => one.id)).toEqual(['p-bryn']);
		expect(marks()).toEqual(['refused', 'swap', 'refused']);
		const said = toasts.items.at(-1);
		expect(said?.message).toBe("Ava Example is kept local, so it isn't offered");
		expect(said?.action?.label).toBe('Allow enrichment');
	});

	it('is left out of a drag across the wall', () => {
		swapMode.enter();
		flushSync();
		const cards = [...document.querySelectorAll<HTMLElement>('.card')];
		cards[1].dispatchEvent(
			new PointerEvent('pointerdown', { bubbles: true, button: 0, clientX: 150, clientY: 5 })
		);
		vi.advanceTimersByTime(400);
		window.dispatchEvent(new PointerEvent('pointermove', { clientX: 250, clientY: 5 }));
		window.dispatchEvent(new PointerEvent('pointerup'));
		flushSync();

		expect(swapMode.picks.map((one) => one.id)).toEqual(['p-bryn']);
	});
});

describe('the marks in words', () => {
	it("reads Kept local first, then Don't swap, and nothing for a row that goes", () => {
		expect(refusedMark({ keep_local: true, keep_from_swaps: true })).toBe('local');
		expect(refusedMark({ keep_from_swaps: true })).toBe('swap');
		expect(refusedMark({})).toBeNull();
		expect(refusedWords('Northlight Media', 'swap')).toEqual({
			words: "Northlight Media is kept out of swaps, so it isn't offered",
			change: 'Allow swapping'
		});
	});
});

describe("a swap's chooser", () => {
	it('lists a row that will not go with the reason the walls give, and leaves the rest alone', () => {
		const ava: PickChoice = { id: 'p-ava', name: 'Ava Example' };
		expect(refusedChoice(ava, { keep_local: true })).toEqual({
			...ava,
			refused: "Kept local, so it isn't offered"
		});
		expect(refusedChoice(ava, { keep_from_swaps: true }).refused).toBe(
			"Kept out of swaps, so it isn't offered"
		);
		expect(refusedChoice(ava, { keep_local: false, keep_from_swaps: false })).toEqual(ava);
	});
});
