/* The scrub line: the time so far at the line's start, the length at its end, no clock where there
 * is no playhead, and the clocks' room kept where a bar asks. */

import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ScrubLine from './ScrubLine.svelte';

let host: HTMLElement;
let mounted: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
});

function draw(props: Record<string, unknown>) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(ScrubLine, {
		target: host,
		props: { position: 83, duration: 276, onseek: () => {}, ...props }
	});
	flushSync();
	return host.querySelector('.scrub-line') as HTMLElement;
}

it('puts the time so far before the timeline and the length after it', () => {
	const line = draw({});
	const parts = [...line.children].map((one) =>
		one.classList.contains('time') ? (one.textContent ?? '').trim() : 'timeline'
	);
	expect(parts).toEqual(['1:23', 'timeline', '4:36']);
	expect(line.classList.contains('clocked')).toBe(true);
});

it('draws no clock where there is no playhead, and the timeline takes the whole line', () => {
	const line = draw({ timed: false });
	expect(line.querySelector('.time')).toBeNull();
	expect(line.classList.contains('clocked')).toBe(false);
});

it('keeps the clocks room, unread, where the bar asks for it', () => {
	const line = draw({ timed: false, keepRoom: true });
	const times = [...line.querySelectorAll('.time')];
	expect(times).toHaveLength(2);
	expect(times.every((one) => one.classList.contains('held-room'))).toBe(true);
	expect(line.querySelector('.reading')).toBeNull();
});

it('hides the two times from a screen reader, which hears the timeline say them', () => {
	const line = draw({});
	expect([...line.querySelectorAll('.time')].map((one) => one.getAttribute('aria-hidden'))).toEqual(
		['true', 'true']
	);
});
