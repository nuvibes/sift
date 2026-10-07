/* The heart, and the small kick it gives when it fills in.
 *
 * The kick is a class that has to come AND go. A timer tied to the effect that put it on would
 * stick: an effect's cleanup runs before the effect runs again, so the next change to what is
 * drawn would cancel it, and no other timer would follow. That leaves a heart
 * mid-animation until the screen is rebuilt, which is the sort of thing that reads as a rendering
 * fault rather than as a bug in a control.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';

import Heart from './Heart.svelte';

let host: HTMLElement;

afterEach(() => {
	host?.remove();
	vi.useRealTimers();
});

function render(props: { favorite: boolean; onchange?: (favorite: boolean) => void }) {
	host = document.createElement('div');
	document.body.append(host);
	const heart = $state({ ...props });
	mount(Heart, { target: host, props: heart });
	flushSync();
	return {
		heart,
		button: () => host.querySelector('.heart') as HTMLElement,
		kicking: () => host.querySelector('.heart.popped') !== null
	};
}

describe('the kick', () => {
	it('happens when a file becomes a favourite', () => {
		vi.useFakeTimers();
		const drawn = render({ favorite: false });

		drawn.heart.favorite = true;
		flushSync();

		expect(drawn.kicking()).toBe(true);
	});

	it('and is over a moment later', () => {
		vi.useFakeTimers();
		const drawn = render({ favorite: false });
		drawn.heart.favorite = true;
		flushSync();

		vi.advanceTimersByTime(400);
		flushSync();

		expect(drawn.kicking(), 'the heart is left mid-animation').toBe(false);
	});

	it('survives the server confirming the write', () => {
		/*
		 * The failure this file exists for, in the shape it would really happen in.
		 *
		 * Pressing the heart shows the choice immediately and sends the write; a moment later the
		 * server's answer replaces the held one. The answer says the same thing: still a favourite,
		 * but it is a different object, so everything reading through it is worked out again,
		 * including the effect that started the kick. With the timer tied to that effect, the
		 * re-run would cancel it with no other timer to follow, and the heart would stay mid-animation.
		 */
		vi.useFakeTimers();
		host = document.createElement('div');
		document.body.append(host);
		// The same shape `judge` has: what is drawn comes from an object that gets REPLACED.
		const held = $state({ answer: { favorite: false } });
		mount(Heart, {
			target: host,
			props: {
				get favorite() {
					return held.answer.favorite;
				}
			}
		});
		flushSync();

		held.answer = { favorite: true }; // pressed: shown immediately
		flushSync();
		expect(host.querySelector('.heart.popped')).not.toBeNull();

		vi.advanceTimersByTime(80);
		held.answer = { favorite: true }; // the server, saying the same thing
		flushSync();
		vi.advanceTimersByTime(400);
		flushSync();

		expect(host.querySelector('.heart.popped'), 'the heart is stuck mid-kick').toBeNull();
	});

	it('does not happen on a heart that arrives already filled', () => {
		// A grid of favourites would otherwise pulse in its entirety the moment it was drawn.
		vi.useFakeTimers();
		const drawn = render({ favorite: true });

		expect(drawn.kicking()).toBe(false);
	});

	it('does not happen when a file stops being a favourite', () => {
		// Taking something off a shortlist is not a moment.
		vi.useFakeTimers();
		const drawn = render({ favorite: true });

		drawn.heart.favorite = false;
		flushSync();

		expect(drawn.kicking()).toBe(false);
	});
});

describe('the press', () => {
	/* Whether the press reaches the tile behind it is settled by the framework's own event
	 * delegation rather than by anything here, so it is not asserted: what would be under test is
	 * Svelte. The `stopPropagation` that stops a tile opening when its heart is pressed is exercised
	 * by the grid's own end-to-end tests. */
	it('asks for the opposite of what it is', () => {
		const changes: boolean[] = [];
		const drawn = render({ favorite: false, onchange: (next) => changes.push(next) });

		drawn.button().click();
		flushSync();

		expect(changes).toEqual([true]);
	});
});
