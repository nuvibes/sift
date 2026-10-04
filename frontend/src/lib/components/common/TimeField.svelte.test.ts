/* One time of day, typed in segments.
 *
 * The same two conversions `DateField` is tested on, and the same reason: both have a wrong answer
 * that looks right. Text comes in as the setting holds it (`HH:MM` on a twenty-four hour clock)
 * and goes back out the same way, padded, because `9:5` is not a time the server accepts. And a
 * value that is not a time is not an error: the setting holds whatever was put there, by a version
 * of Sift this control knows nothing about, so it draws an empty field rather than refusing.
 *
 * What is deliberately NOT tested is how the parts are laid out. The reader's locale decides
 * whether that is `23:00` or `11:00 PM`, and pinning one of them here would pin the machine this
 * happens to run on.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import TimeField from './TimeField.svelte';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(props: Record<string, unknown>): void {
	drawn = mount(TimeField, { target: host, props }) as Record<string, unknown>;
	flushSync();
}

/** What the segments say, in whatever order the reader's locale puts them. */
function segments(): string[] {
	return [...host.querySelectorAll('[data-segment]')]
		.map((one) => one.textContent?.trim() ?? '')
		.filter((text) => /\w/.test(text));
}

it('draws the time it was given, part by part', () => {
	draw({ value: '23:15' });

	expect(segments().some((text) => /^(23|11)$/.test(text))).toBe(true);
	expect(segments()).toContain('15');
});

it('draws an empty field for a value that is not a time', () => {
	// Not an error, for the reason in the head of this file.
	draw({ value: 'eleven at night' });

	expect(segments().some((text) => /\d/.test(text))).toBe(false);
});

it('draws an empty field for a time that could not be one', () => {
	// The hour has to be an hour. `25:00` would otherwise reach the library and be refused there,
	// which is a crash rather than an empty box.
	draw({ value: '25:00' });

	expect(segments().some((text) => /\d/.test(text))).toBe(false);
});

it('draws an empty field when there is no time at all', () => {
	draw({});

	expect(segments().some((text) => /\d/.test(text))).toBe(false);
});

it('names the control for anybody who cannot see the label beside it', () => {
	draw({ value: '', label: 'Start at' });

	expect(host.querySelector('[role="group"]')?.getAttribute('aria-label')).toBe('Start at');
});

it('ties the hour to the help text the row draws, and nothing else to it', () => {
	// One id and one description for the whole control, on the part somebody lands on first. Put on
	// every segment they would be read out three times over.
	draw({ value: '23:00', id: 'nightly', describedBy: 'nightly-help' });

	const described = [...host.querySelectorAll('[aria-describedby="nightly-help"]')];
	expect(described).toHaveLength(1);
	expect(described[0].id).toBe('nightly');
});

it('hands back the time as padded text, in the shape the setting holds', () => {
	/* The round trip this file exists for. Stepping 23:00 up by an hour lands on midnight, and
	   midnight is the case that catches an unpadded answer: `0:0` is what a naive conversion gives
	   and `00:00` is what the server takes. */
	const onchange = vi.fn();
	draw({ value: '23:00', onchange });

	const hour = host.querySelector<HTMLElement>('[data-segment="hour"]');
	hour?.focus();
	hour?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
	flushSync();
	hour?.blur();

	expect(onchange).toHaveBeenCalledWith('00:00');
});

it('steps the hour without touching the minute, which is the whole reason for segments', () => {
	const onchange = vi.fn();
	draw({ value: '07:45', onchange });

	const hour = host.querySelector<HTMLElement>('[data-segment="hour"]');
	hour?.focus();
	hour?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
	flushSync();
	hour?.blur();

	expect(onchange).toHaveBeenCalledWith('06:45');
});

function press(part: string, key: string): void {
	const segment = host.querySelector<HTMLElement>(`[data-segment="${part}"]`);
	segment?.focus();
	segment?.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
	flushSync();
}

it('tells one time for an edit of several keystrokes, the one it ended on', () => {
	/* Every keystroke on the way to a time is a time. Told each, a setting would be saved once a
	   key and each half-typed time would be in force for a moment. */
	vi.useFakeTimers();
	try {
		const onchange = vi.fn();
		draw({ value: '23:00', onchange });

		press('hour', 'ArrowUp');
		press('hour', 'ArrowUp');
		press('minute', 'ArrowUp');
		expect(onchange).not.toHaveBeenCalled();

		vi.advanceTimersByTime(1000);
		expect(onchange).toHaveBeenCalledTimes(1);
		expect(onchange).toHaveBeenCalledWith('01:01');
	} finally {
		vi.useRealTimers();
	}
});

it('keeps what is being typed when another time arrives from outside', async () => {
	/* The answer to an earlier save lands after a later keystroke. Drawn over the field, it would
	   put an evening back to the morning it passed through. */
	vi.useFakeTimers();
	try {
		const onchange = vi.fn();
		const props = $state({ value: '11:00', onchange });
		drawn = mount(TimeField, { target: host, props }) as Record<string, unknown>;
		flushSync();

		press('hour', 'ArrowUp');
		props.value = '09:30';
		flushSync();
		expect(segments()).toContain('12');

		vi.advanceTimersByTime(1000);
		expect(onchange).toHaveBeenCalledWith('12:00');
	} finally {
		vi.useRealTimers();
	}
});

it('says nothing for an edit that ends on the time it began with', () => {
	const onchange = vi.fn();
	draw({ value: '07:45', onchange });

	press('hour', 'ArrowUp');
	press('hour', 'ArrowDown');
	host.querySelector<HTMLElement>('[data-segment="hour"]')?.blur();

	expect(onchange).not.toHaveBeenCalled();
});

it('tells a time still held when the field is taken off the screen', () => {
	const onchange = vi.fn();
	draw({ value: '07:45', onchange });

	press('minute', 'ArrowUp');
	if (drawn) unmount(drawn);
	drawn = null;

	expect(onchange).toHaveBeenCalledWith('07:46');
});
