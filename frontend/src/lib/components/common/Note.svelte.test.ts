/* The aside: a sentence with a symbol in front of it.
 *
 * Two properties, and both are about what it is NOT. It must not announce itself: it is a
 * standing fact about a screen rather than a report that something went wrong, and an aside that
 * interrupts trains people to ignore interruptions. And its symbol must not be read out, because
 * the words beside it already say the same thing.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';

import NoteProbe from './NoteProbe.test.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

function render(props: Record<string, unknown> = {}): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(NoteProbe, { target: host, props });
	return host;
}

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
});

describe('the sentence', () => {
	it('is drawn', () => {
		expect(render({ words: 'Nothing leaves this machine.' }).textContent).toContain(
			'Nothing leaves this machine.'
		);
	});

	/* `Problem` always interrupts, deliberately: something failed and nobody noticed. This is the
	   opposite case and must be read in its turn, or every screen carrying one shouts on arrival. */
	it('and is not announced as an alert or a status', () => {
		const where = render();

		expect(where.querySelector('[role="alert"]')).toBeNull();
		expect(where.querySelector('[role="status"]')).toBeNull();
	});
});

describe('the symbol', () => {
	it('is drawn, and unnamed, because the sentence says the same thing', () => {
		const where = render();

		const icon = where.querySelector('.icon');
		expect(icon).not.toBeNull();
		expect(icon?.getAttribute('aria-label')).toBeNull();
	});

	/* The whole point of the two tones: a caution has to be distinguishable from an aside at a
	   glance, and the mark is what carries that. A tone that drew the same glyph would be a tone
	   that did nothing. */
	it('and a caution draws a different one from an aside', () => {
		const aside = render().querySelector('.icon')?.textContent;
		host.remove();
		if (mounted) void unmount(mounted);

		const caution = render({ tone: 'caution' }).querySelector('.icon')?.textContent;

		expect(aside).toBeTruthy();
		expect(caution).toBeTruthy();
		expect(caution).not.toBe(aside);
	});

	it('and a named glyph wins over the tone default', () => {
		const where = render({ tone: 'caution', icon: 'lock' });

		expect(where.querySelector('.icon')).not.toBeNull();
	});

	/*
	 * The mark sits on the sentence's row and there is no second arrangement. The class list is the
	 * tell: a `.note` carrying anything beyond its tone is a stacked modifier coming back.
	 */
	it('and sits on the sentence row, with no second arrangement to switch to', () => {
		const note = render({ tone: 'caution' }).querySelector('.note');

		expect(note).not.toBeNull();
		const own = [...(note?.classList ?? [])].filter((name) => !name.startsWith('svelte-'));
		expect(own.sort()).toEqual(['caution', 'note']);
		expect(note?.firstElementChild?.classList.contains('icon')).toBe(true);
	});
});
