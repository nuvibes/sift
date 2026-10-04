import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import Slider from './Slider.svelte';

/* Every slider in Sift, and the one thing about it that nothing else can check.
 *
 * How far along a slider is, is drawn from `--level`. On Chromium there is no pseudo-element for the
 * filled part of a track, so that custom property IS the fill: it is painted as a gradient stop.
 * A wrong number there is not an error and not a crash: it is a track whose blue part does not
 * match where the handle is, which is the sort of thing somebody looks at for a week and calls a
 * rendering quirk.
 *
 * It is also the arithmetic most likely to be got wrong: dividing by the maximum instead of by the
 * span is right only for a scale that starts at zero, and a slider that starts at zero would never
 * show the mistake.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function render(props: {
	value: number;
	min?: number;
	max?: number;
	step?: number;
	label?: string;
	valueText?: string;
	disabled?: boolean;
	vertical?: boolean;
	class?: string;
}) {
	host = document.createElement('div');
	document.body.append(host);

	const oninput = vi.fn();
	const onchange = vi.fn();
	const all = reactiveProps({ label: 'A slider', ...props, oninput, onchange });
	mount(Slider, { target: host, props: all });
	flushSync();

	const input = host.querySelector('input') as HTMLInputElement;

	return {
		input,
		oninput,
		onchange,
		/**
		 * How far along the track the fill reaches, as the fraction inside the expression the
		 * stylesheet reads. The handle travels inset by half its width, so the fill and everything
		 * on the track are placed by `alongTrack`, which builds a `calc()` ending in the fraction.
		 * Asserting the fraction says halfway is halfway without pinning the arithmetic that
		 * carries it.
		 */
		level: () => {
			const held = input.style.getPropertyValue('--at');
			const found = /\*\s*([0-9.]+)\s*\)$/.exec(held.trim());
			return found ? Number(found[1]) : null;
		},
		/** Somebody dragging it. */
		drag(to: number) {
			input.value = String(to);
			input.dispatchEvent(new Event('input', { bubbles: true }));
			flushSync();
		},
		/** Somebody letting go of it. */
		release(at: number) {
			input.value = String(at);
			input.dispatchEvent(new Event('change', { bubbles: true }));
			flushSync();
		},
		set(next: Partial<Parameters<typeof render>[0]>) {
			Object.assign(all, next);
			flushSync();
		}
	};
}

describe('how far along it is', () => {
	it('is the distance across the SPAN, not the fraction of the maximum', () => {
		// 30 on a scale of 20 to 40 is halfway. Divided by the maximum it would be 75%, and the fill
		// would sit well past the handle.
		const slider = render({ value: 30, min: 20, max: 40 });

		expect(slider.level()).toBe(0.5);
	});

	it('is drawn from the value snapped to the step, where the browser draws the handle', () => {
		// A range input turns 12.36 into 12.4 at a tenth and paints the thumb there; a fill painted
		// from 12.36 would sit half a step off it and swap sides every tick on the player's
		// timeline. (12.36, not 12.35: a value on the half is a float-rounding question, not a
		// snapping one.)
		const slider = render({ value: 12.36, min: 0, max: 100, step: 0.1 });

		expect(slider.level()).toBeCloseTo(0.124, 6);
	});

	it('is empty at the start of the scale', () => {
		const slider = render({ value: 20, min: 20, max: 40 });

		expect(slider.level()).toBe(0);
	});

	it('is full at the end of it', () => {
		const slider = render({ value: 40, min: 20, max: 40 });

		expect(slider.level()).toBe(1);
	});

	it('reads as empty where there is only one possible value', () => {
		// A clip whose length is not known yet: the maximum is still zero. Nothing has been played,
		// so nothing is filled, and the division that would otherwise happen has no answer.
		const slider = render({ value: 0, min: 0, max: 0 });

		expect(slider.level()).toBe(0);
	});

	it('follows the value it is given', () => {
		const slider = render({ value: 0, max: 200 });

		slider.set({ value: 50 });

		expect(slider.level()).toBe(0.25);
	});
});

describe('what it reports', () => {
	it('hands over a number rather than the text in the box', () => {
		// A range input's value is a string. Passed straight on, it turns "40" + 5 into "405"
		// wherever the caller does arithmetic on it.
		const slider = render({ value: 10 });

		slider.drag(40);

		expect(slider.oninput).toHaveBeenCalledWith(40);
		expect(slider.oninput.mock.calls[0][0]).toBeTypeOf('number');
	});

	it('tells a drag from letting go', () => {
		// The settings row follows one and writes the other: a save per pixel of travel is a request
		// per frame of a drag.
		const slider = render({ value: 10 });

		slider.drag(40);
		expect(slider.onchange).not.toHaveBeenCalled();

		slider.release(40);
		expect(slider.onchange).toHaveBeenCalledWith(40);
	});

	it('is not required to report a release', () => {
		// The timeline has nothing to write down, so it passes no `onchange`. Absent must be silent
		// rather than a crash the first time somebody lets go of it.
		host = document.createElement('div');
		document.body.append(host);
		mount(Slider, {
			target: host,
			props: reactiveProps({ label: 'A slider', value: 10, oninput: vi.fn() })
		});
		flushSync();
		const input = host.querySelector('input') as HTMLInputElement;

		input.value = '40';
		expect(() => input.dispatchEvent(new Event('change', { bubbles: true }))).not.toThrow();
	});
});

describe('what it is announced as', () => {
	it('carries the name it was given', () => {
		const slider = render({ value: 10, label: 'Volume' });

		expect(slider.input.getAttribute('aria-label')).toBe('Volume');
	});

	it('says what the number means where the number alone does not', () => {
		// "3" on a tile-size slider is a step nobody can picture. "3 of 6" is a position.
		const slider = render({ value: 3, max: 6, valueText: '3 of 6' });

		expect(slider.input.getAttribute('aria-valuetext')).toBe('3 of 6');
	});

	it('says nothing where there is nothing to add', () => {
		// An empty string is a value text, and it would replace the number with silence.
		const slider = render({ value: 3 });

		expect(slider.input.hasAttribute('aria-valuetext')).toBe(false);
	});
});

describe('the shapes it takes', () => {
	it('stands on its end when it is asked to', () => {
		const slider = render({ value: 10, vertical: true });

		expect(slider.input.classList.contains('vertical')).toBe(true);
	});

	it('lies flat otherwise', () => {
		const slider = render({ value: 10 });

		expect(slider.input.classList.contains('vertical')).toBe(false);
	});

	it('keeps its own classes when a caller adds one', () => {
		// A caller's class is for size and position. Written over this file's, the control would keep
		// the caller's one class and lose every rule that makes it a slider.
		const slider = render({ value: 10, class: 'level' });

		expect(slider.input.classList.contains('slider')).toBe(true);
		expect(slider.input.classList.contains('level')).toBe(true);
	});

	it('can be turned off', () => {
		const slider = render({ value: 10, disabled: true });

		expect(slider.input.disabled).toBe(true);
	});
});
