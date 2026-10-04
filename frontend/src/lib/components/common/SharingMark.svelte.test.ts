import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import SharingMark from './SharingMark.svelte';

/* The mark says "click to see sharing menu". This is the test that it can be clicked.
 *
 * The tooltip carries the sentence on every wall, so a person's card, a site's card, a tag chip and
 * a folder row all invite the click. A mark with nowhere to send it would fail silently: the mark
 * drawn, the words right, and the one thing they promised missing.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function render(props: Record<string, unknown>) {
	host = document.createElement('div');
	document.body.append(host);
	mount(SharingMark, { target: host, props });
	flushSync();
	return host;
}

describe('a mark with somewhere to go', () => {
	it('is a button, and pressing it opens sharing', () => {
		const onopen = vi.fn();
		render({ shared: true, onopen });

		const button = host.querySelector('button');
		expect(button, 'the mark is not pressable at all').not.toBeNull();

		button?.click();
		expect(onopen).toHaveBeenCalledTimes(1);
	});

	/* Whether the press also reaches the wall underneath is asked where a wall exists: see the folder
	 * tree's own test, where a mark inside a selectable row must not select it. */
});

describe('a mark with nowhere to go', () => {
	it('is not drawn as something to press', () => {
		render({ shared: true });

		expect(host.querySelector('button')).toBeNull();
		expect(host.textContent).not.toBe('');
	});
});

describe('nothing said about it', () => {
	it('draws no mark at all, which is most of a library', () => {
		const onopen = vi.fn();
		render({ onopen });

		expect(host.querySelector('button')).toBeNull();
		expect(host.querySelector('span')).toBeNull();
	});
});

describe('a thing in the vault', () => {
	/* With the vault open a hidden thing is still listed, correctly, and without a badge it would
	 * look exactly like one that had never been hidden. Hiding something would then appear to do
	 * nothing at all, which is worse than the row vanishing: at least that is legible. */
	it('carries its own mark beside the sharing one', () => {
		render({ shared: true, hidden: true });

		expect(host.querySelectorAll('.mark')).toHaveLength(2);
		expect(host.querySelector('.mark.vaulted')).not.toBeNull();
	});

	it('carries it even when nothing has been shared', () => {
		render({ hidden: true });

		expect(host.querySelector('.mark.vaulted'), 'hiding it looked like nothing').not.toBeNull();
	});

	it('and an ordinary thing has no vault mark at all', () => {
		render({ shared: true });

		expect(host.querySelector('.mark.vaulted')).toBeNull();
	});
});

describe('the two glyphs answer two different questions', () => {
	/*
	 * Each glyph opens its own panel: "this is hidden" must not open the Sharing panel. Asserted
	 * both ways round on each glyph, because "the hidden one opened something" would also pass if
	 * both opened the same thing.
	 */
	function marks(props: Record<string, unknown>) {
		render(props);
		return [...host.querySelectorAll('button')];
	}

	it('the crossed-out eye opens Hidden and not sharing', () => {
		const onopen = vi.fn();
		const onhidden = vi.fn();
		const buttons = marks({ shared: true, hidden: true, onopen, onhidden });

		const eye = buttons.find((one) => one.classList.contains('vaulted'));
		expect(eye, 'there is no vault glyph to press').not.toBeUndefined();

		eye?.click();
		expect(onhidden).toHaveBeenCalledTimes(1);
		expect(onopen).not.toHaveBeenCalled();
	});

	it('and the sharing mark opens sharing and not Hidden', () => {
		const onopen = vi.fn();
		const onhidden = vi.fn();
		const buttons = marks({ shared: true, hidden: true, onopen, onhidden });

		const sharing = buttons.find((one) => !one.classList.contains('vaulted'));
		expect(sharing, 'there is no sharing mark to press').not.toBeUndefined();

		sharing?.click();
		expect(onopen).toHaveBeenCalledTimes(1);
		expect(onhidden).not.toHaveBeenCalled();
	});

	it('and neither is drawn as something to press where there is nowhere to send it', () => {
		render({ shared: true, hidden: true });

		expect(host.querySelectorAll('button')).toHaveLength(0);
	});
});
