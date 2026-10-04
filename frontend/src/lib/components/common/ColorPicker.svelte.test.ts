/* Choosing a colour, in the app's own chrome.
 *
 * Four things are worth a test here and each of them is a way this goes wrong invisibly.
 *
 * THE TWO REPORTS. A drag has to paint on every movement and store only when it ends. Store on
 * every movement and one decision is a hundred writes; paint only at the end and the whole point of
 * the control (watching the app follow the marker) is gone. Both look fine in a screenshot.
 *
 * THE MARKER AND THE BOX AGREEING. They are two views of one colour, and either can be moved. A
 * hex typed into the box has to put the marker where that colour is, or the next drag starts from
 * somewhere nobody chose.
 *
 * THE HUE SURVIVING A GREY. White and black have no hue. A picker that re-derives its slider from
 * the colour swings it to red the moment somebody types white, and what they had is gone.
 *
 * THE COPY GOING THROUGH THE HELPER. `navigator.clipboard` does not exist on a plain-http address,
 * which is how a self-hosted Sift is normally reached, so a copy written against the browser API
 * directly works for whoever built it and for nobody else.
 *
 * NO COLOUR IS WRITTEN HERE. Every one is built from numbers, or derived from one that was: a
 * colour written into a file other than `app.css` is a second declaration of one.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const copyText = vi.hoisted(() => vi.fn(async () => true));
vi.mock('$lib/shell/clipboard', () => ({ copyText }));

import ColorPicker from './ColorPicker.svelte';
import { toHex, toHsv } from './hsv';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	copyText.mockClear();
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

/** A colour, built rather than typed. */
const colour = (red: number, green: number, blue: number): string =>
	'#' + [red, green, blue].map((one) => one.toString(16).padStart(2, '0')).join('');

/** The one the specimens start from: a mid blue, saturated enough to have a hue worth keeping. */
const START = colour(37, 99, 235);

/** How big the square is pretending to be. jsdom lays nothing out, so every box it measures is
 *  zero, and a control that divides by its own width has to be told one. */
const SIDE = 200;

interface Given {
	label: string;
	value: string;
	oninput: (colour: string) => void;
	onchange: (colour: string) => void;
	swatches?: { name: string; colour: string }[];
}

function draw(props: Given): Given {
	const live = $state({ ...props });
	drawn = mount(ColorPicker, { target: host, props: live }) as Record<string, unknown>;
	flushSync();
	// The square's geometry and the pointer capture it takes, neither of which jsdom has.
	let held = false;
	const sq = square();
	sq.getBoundingClientRect = () =>
		({ left: 0, top: 0, width: SIDE, height: SIDE, right: SIDE, bottom: SIDE }) as DOMRect;
	sq.setPointerCapture = () => {
		held = true;
	};
	sq.hasPointerCapture = () => held;
	sq.releasePointerCapture = () => {
		held = false;
	};
	return live;
}

const square = (): HTMLElement => host.querySelector('button.square') as HTMLElement;
const marker = (): HTMLElement => host.querySelector('.marker') as HTMLElement;
const box = (): HTMLInputElement => host.querySelector('input.text-input') as HTMLInputElement;
const hue = (): HTMLInputElement => host.querySelector('input[type="range"]') as HTMLInputElement;
const copyButton = (): HTMLButtonElement =>
	host.querySelector('button[aria-label]:not(.square)') as HTMLButtonElement;
const error = (): string | null => host.querySelector('[role="alert"]')?.textContent ?? null;

/** A pointer event with a place on the square, which jsdom has no constructor for. */
function point(type: string, across: number, down: number): void {
	const event = new Event(type, { bubbles: true });
	Object.assign(event, { clientX: across, clientY: down, pointerId: 1 });
	square().dispatchEvent(event);
	flushSync();
}

/** Type into the box and leave it, which is when a typed colour is believed. */
function type(text: string): void {
	box().value = text;
	box().dispatchEvent(new Event('input', { bubbles: true }));
	box().dispatchEvent(new Event('change', { bubbles: true }));
	flushSync();
}

/** Where the marker is, as the two fractions it is placed by. */
const at = () => ({
	across: marker().style.getPropertyValue('--across'),
	down: marker().style.getPropertyValue('--down')
});

it('shows the colour it was given, in the box and under the marker', () => {
	draw({ label: 'Accent', value: START, oninput: vi.fn(), onchange: vi.fn() });

	expect(box().value).toBe(START);
	const from = toHsv(START)!;
	expect(at()).toEqual({
		across: `${from.saturation * 100}%`,
		down: `${(1 - from.value) * 100}%`
	});
});

it('reports every step of a drag, and stores only when it ends', () => {
	const oninput = vi.fn();
	const onchange = vi.fn();
	draw({ label: 'Accent', value: START, oninput, onchange });

	// The top right corner: all the saturation and all the brightness, which is the hue itself.
	const pure = toHex({ hue: toHsv(START)!.hue, saturation: 1, value: 1 });

	point('pointerdown', SIDE, 0);
	expect(oninput).toHaveBeenLastCalledWith(pure);
	expect(onchange, 'a colour was stored in the middle of a drag').not.toHaveBeenCalled();

	// Halfway across and halfway down: half the saturation, half the brightness.
	point('pointermove', SIDE / 2, SIDE / 2);
	expect(oninput).toHaveBeenLastCalledWith(
		toHex({ hue: toHsv(START)!.hue, saturation: 0.5, value: 0.5 })
	);
	expect(onchange).not.toHaveBeenCalled();

	point('pointerup', SIDE / 2, SIDE / 2);
	expect(onchange).toHaveBeenCalledExactlyOnceWith(
		toHex({ hue: toHsv(START)!.hue, saturation: 0.5, value: 0.5 })
	);
	// The box follows the marker: they are two views of one colour.
	expect(box().value).toBe(toHex({ hue: toHsv(START)!.hue, saturation: 0.5, value: 0.5 }));
});

it('ignores a pointer that is moving over it without being held down', () => {
	// The pointer crosses this square on its way to the hue slider all day. Without the capture
	// check, every one of those crossings would be a colour.
	const oninput = vi.fn();
	draw({ label: 'Accent', value: START, oninput, onchange: vi.fn() });

	point('pointermove', SIDE, 0);

	expect(oninput).not.toHaveBeenCalled();
});

it('moves the marker to a colour that was typed', () => {
	const onchange = vi.fn();
	draw({ label: 'Accent', value: START, oninput: vi.fn(), onchange });

	const typed = colour(168, 49, 183);
	type(typed);

	const wanted = toHsv(typed)!;
	expect(at()).toEqual({
		across: `${wanted.saturation * 100}%`,
		down: `${(1 - wanted.value) * 100}%`
	});
	expect(onchange).toHaveBeenCalledWith(typed);
	expect(error()).toBeNull();
});

it('says what to do about something that is not a colour, and reports nothing', () => {
	const onchange = vi.fn();
	draw({ label: 'Accent', value: START, oninput: vi.fn(), onchange });

	type('burnt umber');

	expect(onchange).not.toHaveBeenCalled();
	expect(error()).toContain('hash and six digits');
});

it('says nothing while a colour is still being typed', () => {
	// An error under a box somebody is still filling in is shouting at them for a word they had not
	// finished. Half of one is what is in the box in the middle of typing.
	const onchange = vi.fn();
	draw({ label: 'Accent', value: START, oninput: vi.fn(), onchange });

	box().value = colour(170, 51, 0).slice(0, 4);
	box().dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();

	expect(error()).toBeNull();
	expect(onchange).not.toHaveBeenCalled();
});

it('keeps the hue when a colour arrives that has none', () => {
	const onchange = vi.fn();
	draw({ label: 'Accent', value: START, oninput: vi.fn(), onchange });
	const before = hue().value;

	// White: no hue and no saturation. The slider must stay where it was put.
	type(colour(255, 255, 255));
	expect(hue().value).toBe(before);

	// And the hue it kept is the one that comes back when the saturation does.
	point('pointerdown', SIDE, 0);
	point('pointerup', SIDE, 0);
	expect(onchange).toHaveBeenLastCalledWith(
		toHex({ hue: toHsv(START)!.hue, saturation: 1, value: 1 })
	);
});

it('moves and stores on an arrow key, because there is no letting go of one', () => {
	const oninput = vi.fn();
	const onchange = vi.fn();
	draw({ label: 'Accent', value: START, oninput, onchange });

	const from = toHsv(START)!;
	square().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
	flushSync();

	const wanted = toHex({
		hue: from.hue,
		saturation: Math.min(1, from.saturation + 0.05),
		value: from.value
	});
	expect(oninput).toHaveBeenLastCalledWith(wanted);
	expect(onchange).toHaveBeenLastCalledWith(wanted);
});

it('leaves a key that is not an arrow alone', () => {
	const oninput = vi.fn();
	draw({ label: 'Accent', value: START, oninput, onchange: vi.fn() });

	square().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
	flushSync();

	expect(oninput).not.toHaveBeenCalled();
});

it('copies the colour through the helper, and says that it did', async () => {
	draw({ label: 'Accent', value: START, oninput: vi.fn(), onchange: vi.fn() });

	copyButton().click();
	await vi.waitFor(() => expect(copyText).toHaveBeenCalledWith(START));
	flushSync();

	expect(copyButton().getAttribute('aria-label')).toBe('Copied');
});

it('says nothing when the clipboard refused it', async () => {
	copyText.mockResolvedValueOnce(false);
	draw({ label: 'Accent', value: START, oninput: vi.fn(), onchange: vi.fn() });

	copyButton().click();
	await vi.waitFor(() => expect(copyText).toHaveBeenCalled());
	flushSync();

	expect(copyButton().getAttribute('aria-label')).toBe('Copy the color');
});

it('draws what the colour turns into, when the caller says what that is', () => {
	draw({
		label: 'Accent',
		value: START,
		oninput: vi.fn(),
		onchange: vi.fn(),
		// The tokens the page itself is painted with, which is what makes these the real answer
		// rather than a second derivation of it.
		swatches: [{ name: 'Fill', colour: 'var(--sift-accent)' }]
	});

	expect(host.querySelector('.derived-name')?.textContent).toBe('Fill');
});

it('draws no row of samples when there is nothing to show in it', () => {
	draw({ label: 'Accent', value: START, oninput: vi.fn(), onchange: vi.fn() });

	expect(host.querySelector('.derived')).toBeNull();
});

it('follows the colour when it is changed somewhere else', () => {
	// Another window on the same account, or a named accent being pressed. The box is not a second
	// opinion about what the colour is.
	const live = draw({ label: 'Accent', value: START, oninput: vi.fn(), onchange: vi.fn() });

	const elsewhere = colour(8, 128, 136);
	live.value = elsewhere;
	flushSync();

	expect(box().value).toBe(elsewhere);
});

it('keeps the hue when its own answer comes back down as the prop', () => {
	/*
	 * The colour this control reported is stored by the caller and arrives back as `value` a tick
	 * later. Re-reading it is harmless (it is the same colour), but the hue must survive the trip
	 * when the colour has no hue to read: the grey case above, arriving by the other door.
	 */
	const live = draw({ label: 'Accent', value: START, oninput: vi.fn(), onchange: vi.fn() });

	type(colour(255, 255, 255));
	const kept = hue().value;
	live.value = colour(255, 255, 255);
	flushSync();

	expect(hue().value).toBe(kept);
});
