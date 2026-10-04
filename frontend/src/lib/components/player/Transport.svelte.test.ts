/* The transport every player bar draws: Shuffle, Previous, Play, Next, Repeat, in that order, with
 * the act table's words and keys, and a press with nothing to act on drawn dimmed with its reason
 * or not drawn at all, as its caller says. */

import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import Transport from './Transport.svelte';

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
	mounted = mount(Transport, { target: host, props: { playing: false, ...props } });
	flushSync();
}

/** The presses, in the order drawn, each with whether it can act. */
function presses(): string[] {
	return [...host.querySelectorAll('button')].map(
		(one) =>
			`${one.getAttribute('aria-label')}${(one as HTMLButtonElement).disabled ? ' (dimmed)' : ''}`
	);
}

it('stands Shuffle left of Previous and Repeat right of Next, Play between', () => {
	draw({
		onplay: () => {},
		onback: () => {},
		onforward: () => {},
		shuffle: { on: false, onpress: () => {} },
		repeat: { mode: 'loop_all', onpress: () => {} }
	});
	expect(presses()).toEqual(['Shuffle', 'Previous', 'Play', 'Next', 'Play through']);
});

it('draws a step with no handler dimmed with its reason, and none at all without one', () => {
	draw({ onplay: () => {}, backWhy: 'Nothing before this', onforward: () => {} });
	expect(presses()).toEqual(['Nothing before this (dimmed)', 'Play', 'Next']);
	unmount(mounted!);
	host.remove();

	draw({ onplay: () => {} });
	expect(presses(), 'a list of one has no next to wait for').toEqual(['Play']);
});

it('dims Shuffle, Play and Repeat with the reason their caller gives', () => {
	draw({
		playable: false,
		playWhy: 'This one is hidden',
		shuffle: { on: false, onpress: () => {}, why: 'This one is hidden' },
		repeat: { mode: 'once', onpress: () => {}, why: 'This one is hidden' }
	});
	expect(presses()).toEqual([
		'This one is hidden (dimmed)',
		'This one is hidden (dimmed)',
		'This one is hidden (dimmed)'
	]);
});

it('says the answer for the end of a file, lit only while something repeats, and presses it', () => {
	const onpress = vi.fn();
	draw({ repeat: { mode: 'once', onpress } });
	const button = host.querySelector('button[aria-label="Stop at the end"]') as HTMLButtonElement;
	expect(button.getAttribute('aria-pressed')).toBe('false');
	button.click();
	expect(onpress).toHaveBeenCalledTimes(1);
});

it('lights Shuffle while the order is shuffled', () => {
	draw({ shuffle: { on: true, onpress: () => {} } });
	expect(host.querySelector('button[aria-label="Shuffle"]')?.getAttribute('aria-pressed')).toBe(
		'true'
	);
});

it('draws no Play where there is nothing to play and no reason given', () => {
	draw({ playable: false, onback: () => {} });
	expect(presses()).toEqual(['Previous']);
});
