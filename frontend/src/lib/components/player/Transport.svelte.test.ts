/* The transport every player bar draws: Repeat, Previous, Play, Next, Shuffle, always all five, a
 * press with nothing to act on dimmed with its reason. */

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
	mounted = mount(Transport, {
		target: host,
		props: {
			playing: false,
			shuffle: { on: false, onpress: () => {} },
			repeat: { mode: 'loop_all', onpress: () => {} },
			...props
		}
	});
	flushSync();
}

/** The presses, in the order drawn, each with whether it can act. */
function presses(): string[] {
	return [...host.querySelectorAll('button')].map(
		(one) =>
			`${one.getAttribute('aria-label')}${(one as HTMLButtonElement).disabled ? ' (dimmed)' : ''}`
	);
}

it('stands Repeat left of Previous and Shuffle right of Next, Play between', () => {
	draw({ onplay: () => {}, onback: () => {}, onforward: () => {} });
	expect(presses()).toEqual(['Play through', 'Previous', 'Play', 'Next', 'Shuffle']);
});

it('draws a step with no handler dimmed, with its reason or the default words', () => {
	draw({ onplay: () => {}, backWhy: 'Nothing to go back to', onforward: () => {} });
	expect(presses()).toEqual([
		'Play through',
		'Nothing to go back to (dimmed)',
		'Play',
		'Next',
		'Shuffle'
	]);
	unmount(mounted!);
	host.remove();

	draw({ onplay: () => {} });
	expect(presses(), 'a list of one keeps all five').toEqual([
		'Play through',
		'Nothing before this (dimmed)',
		'Play',
		'Nothing after this (dimmed)',
		'Shuffle'
	]);
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
		'Nothing before this (dimmed)',
		'This one is hidden (dimmed)',
		'Nothing after this (dimmed)',
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

it('dims Play where there is nothing to play and no reason given', () => {
	draw({ playable: false, onback: () => {} });
	expect(presses()[2]).toBe('Nothing after this (dimmed)');
});

it('draws Play alone on the docked strip, and no step pair where a swipe steps', () => {
	draw({ onplay: () => {}, playOnly: true });
	expect(presses()).toEqual(['Play']);
	unmount(mounted!);
	host.remove();

	draw({ onplay: () => {}, onback: () => {}, onforward: () => {}, steps: false });
	expect(presses()).toEqual(['Play through', 'Play', 'Shuffle']);
});
