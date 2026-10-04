/* An address shown a little at a time.
 *
 * Two callers (a tunnel's exit server and the address Sift shares its library on), and they
 * hand it two different SHAPES of address. What is pinned here is the split: which part stays on
 * the screen, which part is covered, and that the covered part is really the machine rather than
 * the port. Reaching for the colon first is right for a bare IPv6 address and wrong for every
 * IPv4 address with a port on it, and the wrong way round leaves the whole machine address on
 * screen and covers a number that identifies nothing.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ExitAddress from './ExitAddress.svelte';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function render(address: string, what = 'this computer s address') {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(ExitAddress, { target: host, props: { address, what } });
	flushSync();
	return {
		control: host.querySelector('button'),
		shown: () => host.querySelector('button')?.firstElementChild?.textContent ?? '',
		covered: () => host.querySelector('.covered')?.textContent ?? ''
	};
}

describe('what is covered', () => {
	it('keeps the first part of an IPv4 address and covers the rest', () => {
		const { shown, covered } = render('192.168.1.44');

		expect(shown()).toBe('192.');
		expect(covered()).toBe('168.1.44');
	});

	it('covers the machine and not the port, on an address that has both', () => {
		// The whole reason the dot is preferred. Splitting on the colon here would leave
		// `192.168.1.44` on the screen and cover `5171`, which is exactly backwards.
		const { shown, covered } = render('192.168.1.44:5171');

		expect(shown()).toBe('192.');
		expect(covered()).toBe('168.1.44:5171');
	});

	it('leaves a scheme in the open, because it says nothing about which machine this is', () => {
		const { shown, covered } = render('http://192.168.1.44:5171');

		expect(shown()).toBe('http://192.');
		expect(covered()).toBe('168.1.44:5171');
	});

	it('still splits an IPv6 address on its colons, having no dots to go on', () => {
		const { shown, covered } = render('2001:db8::7334');

		expect(shown()).toBe('2001:');
		expect(covered()).toBe('db8::7334');
	});

	it('draws a plain span, not a control, when there is nothing to cover', () => {
		// One part and no separator: pressing would reveal nothing, and a control that does nothing
		// is worse than no control.
		const { control } = render('localhost');

		expect(control).toBe(null);
		expect(host.textContent).toBe('localhost');
	});
});

describe('the control', () => {
	it('starts covered and says what pressing it would show', () => {
		const { control } = render('192.168.1.44', 'the address Lanternfield is connected to');

		expect(control?.getAttribute('aria-pressed')).toBe('false');
		expect(control?.getAttribute('aria-label')).toBe(
			'Show the whole of the address Lanternfield is connected to'
		);
	});

	it('shows the whole of it when pressed, and offers to put it back', () => {
		const { control } = render('192.168.1.44', 'the address Lanternfield is connected to');

		control?.click();
		flushSync();

		expect(control?.getAttribute('aria-pressed')).toBe('true');
		expect(control?.getAttribute('aria-label')).toBe(
			'Hide the rest of the address Lanternfield is connected to'
		);
	});

	it('has the whole address in it either way, which is what makes this a screen measure', () => {
		// Said out loud because it is the limit of what this is for: the address is in the page
		// whether it is covered or not. It stops somebody reading it over a shoulder; it is not a
		// secret and must never be described as one.
		const { control } = render('192.168.1.44');

		expect(control?.textContent).toBe('192.168.1.44');
	});
});
