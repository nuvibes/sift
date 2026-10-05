/*
 * The timeline's own keyboard.
 *
 * A range input answers the arrow keys itself, by its own `step`, and this one's step is a tenth of
 * a second because that is what somebody dragging it wants. Left to itself, clicking the bar and
 * then pressing an arrow would move the playhead a tenth of a second at a time while the same arrow
 * with the focus anywhere else moves it five, and the app's shortcut cannot rescue it, because a
 * shortcut never fires into a focused input.
 *
 * The full-size player and a wall's bar both draw this one component, so both are this file.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import Scrubber from './Scrubber.svelte';
import source from './Scrubber.svelte?raw';
import { SKIP_SECONDS } from '$lib/player/skip';

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function render(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mount(Scrubber, {
		target: host,
		props: { position: 30, duration: 120, onseek: () => {}, ...props }
	});
	flushSync();
	return {
		slider: host.querySelector('input[type="range"]') as HTMLInputElement,
		timeline: host.querySelector('.timeline') as HTMLElement
	};
}

/** A press on the timeline, as one arrives from a focused slider: it bubbles to the box. */
function press(on: HTMLElement, key: string): KeyboardEvent {
	const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
	on.dispatchEvent(event);
	flushSync();
	return event;
}

describe('the arrows, with the timeline focused', () => {
	it('moves the playhead by the step the rest of the app uses', () => {
		const seeks: number[] = [];
		const { slider } = render({ onseek: (at: number) => seeks.push(at) });

		press(slider, 'ArrowRight');
		press(slider, 'ArrowLeft');

		expect(seeks).toEqual([30 + SKIP_SECONDS, 30 - SKIP_SECONDS]);
	});

	/*
	 * REFUSED, or the input steps by its own tenth of a second AS WELL and the playhead lands five
	 * seconds and a tenth away. This is the assertion that catches a guard half-made.
	 */
	it('refuses the press, so the input does not step as well', () => {
		const { slider } = render();

		expect(press(slider, 'ArrowRight').defaultPrevented).toBe(true);
	});

	/* Up and Down as well: a range input treats Up as Right, and somebody who has learned that on
	   the volume slider beside this one will try it here. */
	it('answers up and down the same way', () => {
		const seeks: number[] = [];
		const { slider } = render({ onseek: (at: number) => seeks.push(at) });

		press(slider, 'ArrowUp');
		press(slider, 'ArrowDown');

		expect(seeks).toEqual([30 + SKIP_SECONDS, 30 - SKIP_SECONDS]);
	});

	/* Home, End and the two Page keys belong to the widget and are already right, so they are left
	   alone: taking them would mean writing three more behaviours that already work. */
	it('leaves the keys the widget already answers correctly', () => {
		const seek = vi.fn();
		const { slider } = render({ onseek: seek });

		const home = press(slider, 'Home');
		const page = press(slider, 'PageUp');

		expect(seek).not.toHaveBeenCalled();
		expect(home.defaultPrevented).toBe(false);
		expect(page.defaultPrevented).toBe(false);
	});

	it('does not run off either end', () => {
		const seeks: number[] = [];
		const { slider } = render({ position: 1, duration: 3, onseek: (at: number) => seeks.push(at) });

		press(slider, 'ArrowLeft');
		press(slider, 'ArrowRight');

		expect(seeks).toEqual([0, 3]);
	});
});

describe('a timeline with nothing to move along', () => {
	/*
	 * A picture has no playhead: a GIF included, because it is drawn in an `<img>` and an
	 * `<img>` can neither say where it is nor be sent anywhere. Dimmed and refused rather than
	 * removed, so a cell's bar is the same shape whatever it happens to be showing.
	 */
	it('is refused, dimmed, and answers no key', () => {
		const seek = vi.fn();
		const { slider, timeline } = render({ disabled: true, onseek: seek });

		expect(slider.disabled).toBe(true);
		expect(timeline.classList.contains('off')).toBe(true);

		press(slider, 'ArrowRight');

		expect(seek).not.toHaveBeenCalled();
	});

	it('draws no playhead, read from the rule since jsdom paints no handle', () => {
		expect(source).toMatch(/\.timeline\.off \{[^}]*--slider-thumb: 0px;/);
	});

	/* Nothing has said how long it is yet, so there is nowhere to seek TO. Without this the arrows
	   would report a seek against a length of zero, which lands every press at the same place. */
	it('says nothing while the length is unknown', () => {
		const seek = vi.fn();
		const { slider } = render({ duration: 0, onseek: seek });

		press(slider, 'ArrowRight');

		expect(seek).not.toHaveBeenCalled();
	});
});

/*
 * The fill and the handle, from one number.
 *
 * A range input sanitises whatever it is handed to its own `step`, so the handle is drawn at the
 * rounded number; a fill painted from the raw playhead would sit up to half a step away, flipping
 * every tick. The component rounds first, so the number it hands over is one the input will not
 * change.
 */
describe('where the playhead is drawn', () => {
	it('hands the slider a value already on the track step, so nothing rounds it afterwards', () => {
		const { slider } = render({ position: 12.3456, duration: 120 });

		expect(Number(slider.value)).toBeCloseTo(12.3, 6);
	});

	it('rounds to the nearest step rather than always down', () => {
		const { slider } = render({ position: 12.36, duration: 120 });

		expect(Number(slider.value)).toBeCloseTo(12.4, 6);
	});

	/* The fill is placed from `--at`, which the primitive builds from the same number the input is
	   given, so proving they agree is proving the fraction in that expression is the handle's. */
	it('places the fill at the fraction the handle is at', () => {
		const { slider } = render({ position: 12.3456, duration: 120 });

		expect(slider.style.getPropertyValue('--at')).toContain(String(12.3 / 120));
	});

	/* The track is the whole tenths that hold the clip, so the fill and the handle meet at its end
	   together and never overshoot each other. */
	it('never goes past the end of the track', () => {
		const { slider } = render({ position: 87.36, duration: 87.36 });

		expect(Number(slider.value)).toBeCloseTo(87.4, 6);
		expect(slider.style.getPropertyValue('--at')).toContain('* 1)');
	});

	it('sits at the beginning while nothing has said how long the file is', () => {
		const { slider } = render({ position: 9, duration: 0 });

		expect(Number(slider.value)).toBe(0);
	});
});

describe('the end of a file', () => {
	/* A range input will not rest between two of its steps, so a track exactly as long as a short
	   clip would stop its handle at the last whole tenth, short of the end. */
	it('is the end of the track, whatever the clip is long', () => {
		for (const duration of [5.589, 87.36, 0.05, 3]) {
			const { slider } = render({ position: duration, duration });
			expect(Number(slider.value)).toBe(Number(slider.max));
			expect(Number(slider.max)).toBeGreaterThanOrEqual(duration);
			// A step the input can stand on: a whole number of tenths.
			const tenths = Number(slider.max) * 10;
			expect(Math.abs(tenths - Math.round(tenths))).toBeLessThan(1e-6);
			host.remove();
		}
	});

	it('seeks no further than the end when the handle is dragged to it', () => {
		const seeks: number[] = [];
		const { slider } = render({
			position: 1,
			duration: 5.589,
			onseek: (at: number) => seeks.push(at)
		});

		slider.value = slider.max;
		slider.dispatchEvent(new Event('input', { bubbles: true }));

		expect(seeks).toEqual([5.589]);
	});
});
