/*
 * A meter is a reading with a name, and it says so to a screen reader as a METER, not as progress,
 * which only ever grows. The one check a unit test can make of the primitive: the role, the range
 * and the value reach the element, and the fill is the caller's when they hand one in.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Meter from './Meter.svelte';

let host: HTMLElement | null = null;
let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	host = null;
});

function draw(props: Record<string, unknown>) {
	host = document.createElement('div');
	document.body.appendChild(host);
	drawn = mount(Meter, { target: host, props: { label: 'How full', value: 3, max: 10, ...props } });
	flushSync();
	return host.querySelector('[role="meter"]');
}

describe('a reading within a range', () => {
	it('is a meter to a screen reader, with the range and the value on it', () => {
		const meter = draw({});
		expect(meter).not.toBeNull();
		expect(meter?.getAttribute('aria-valuenow')).toBe('3');
		expect(meter?.getAttribute('aria-valuemax')).toBe('10');
		expect(meter?.getAttribute('aria-label')).toBe('How full');
	});

	it('draws the accent fill at the fraction when nobody hands one in', () => {
		const meter = draw({ value: 2, max: 8 });
		const fill = meter?.querySelector('.fill') as HTMLElement | null;
		expect(fill?.style.inlineSize).toBe('25%');
	});

	it('clamps a reading past its own range, so the fill stays in the track', () => {
		const meter = draw({ value: 40, max: 10 });
		expect((meter?.querySelector('.fill') as HTMLElement).style.inlineSize).toBe('100%');
	});
});
