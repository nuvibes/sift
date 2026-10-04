/* One day, typed in segments.
 *
 * What is worth a test is the two conversions at the edges, because both have a wrong answer that
 * looks right. Text comes in as the column holds it and goes back out the same way, with no `Date`
 * in between: the first caller to reach for a date object shifts the day by the browser's offset,
 * and a birthdate then reads as the day before for anybody east of UTC. And a value that is not a
 * date is not an error: what is in the column is whatever was put there, and a control that refused
 * it would be a record nobody could open.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import DateField from './DateField.svelte';

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
	drawn = mount(DateField, { target: host, props }) as Record<string, unknown>;
	flushSync();
}

/** What the segments say, in the order the reader's locale puts them. */
function segments(): string[] {
	return [...host.querySelectorAll('[data-segment]')]
		.map((one) => one.textContent?.trim() ?? '')
		.filter((text) => /\w/.test(text));
}

it('draws the day it was given, part by part', () => {
	draw({ value: '1990-04-07' });

	const parts = segments();
	expect(parts).toContain('1990');
	expect(parts).toContain('04');
	expect(parts).toContain('07');
});

it('draws an empty field for a value that is not a day', () => {
	// Not an error. The column holds whatever was put in it, and a control is not the place to
	// refuse it: a record with one bad value has to still open.
	draw({ value: 'sometime in 1990' });

	expect(segments().some((text) => text.includes('1990'))).toBe(false);
});

it('draws an empty field when there is no day at all', () => {
	draw({});

	expect(segments().some((text) => /\d/.test(text))).toBe(false);
});

it('names the control for anybody who cannot see the label beside it', () => {
	// Inside a `Field` the label has already named it. On its own there is nothing else that does.
	draw({ value: '', label: 'Birthdate' });

	expect(host.querySelector('[role="group"]')?.getAttribute('aria-label')).toBe('Birthdate');
});

it('ties the day to the help text a Field draws, and nothing else to it', () => {
	// One id and one description for the whole control, on the part somebody lands on first. Put on
	// every segment they would be read out three times over.
	draw({ value: '1990-04-07', id: 'birth', describedBy: 'birth-help' });

	const described = [...host.querySelectorAll('[aria-describedby="birth-help"]')];
	expect(described).toHaveLength(1);
	expect(described[0].id).toBe('birth');
});

it('hands back the day as text, in the shape the column holds', () => {
	// The round trip this file exists for. A control that answered with a date object would leave
	// its caller to convert, and the obvious conversion is the one that moves the day.
	const onchange = vi.fn();
	draw({ value: '1990-04-07', onchange });

	const year = host.querySelector<HTMLElement>('[data-segment="year"]');
	year?.focus();
	year?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
	flushSync();

	expect(onchange).toHaveBeenCalledWith('1991-04-07');
});
