/* A shape, drawn as the shape.
 *
 * Words do not say what a wall will look like: "One above two" and "Two above one" are one word
 * apart and describe opposite pictures. What matters about this component is that the picture is
 * built from the SAME `grid-template` the wall itself is drawn with, so it cannot come to disagree
 * with what choosing it produces, and that there is one block per place.
 */

import { afterEach, beforeEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import LayoutGlyph from './LayoutGlyph.svelte';
import { layout, template } from '$lib/theater/layouts';

let host: HTMLDivElement;
let mounted: Record<string, unknown> | null = null;

function draw(id: string) {
	mounted = mount(LayoutGlyph, { target: host, props: { shape: layout(id) } }) as Record<
		string,
		unknown
	>;
	flushSync();
}

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host.remove();
});

it('draws one block per place in the shape', () => {
	draw('grid');

	expect(host.querySelectorAll('.block')).toHaveLength(4);
});

it('is laid out by the same template the wall uses, not a second table of pictures', () => {
	draw('side_by_side_by_side');

	const glyph = host.querySelector('.glyph') as HTMLElement;
	expect(glyph.style.getPropertyValue('--shape')).toBe(
		template(layout('side_by_side_by_side').shape)
	);
});

it('sets the shape through the CSSOM rather than as a style attribute', () => {
	// The policy this app is served under refuses inline styles, and it refuses them silently, so
	// a glyph written that way draws as the fallback and nothing anywhere says why.
	draw('grid');

	const glyph = host.querySelector('.glyph') as HTMLElement;
	expect(glyph.style.getPropertyValue('--shape')).not.toBe('');
});

it('is hidden from a screen reader, because it is a picture of what the words beside it say', () => {
	draw('grid');

	// The ROOT of whatever it draws, rather than a named box inside it: hiding an inner element
	// and leaving the outer one announced is exactly the fault this is here to catch.
	expect(host.firstElementChild?.getAttribute('aria-hidden')).toBe('true');
});

/* A layout with a strip has to LOOK like one. Center Stage's focus half is a single feed, so
   without the strip its picture is one square: the same picture a wall of one would have, in a
   menu whose whole job is to say what choosing this produces. */
it('draws the strip a layout opens with, under the wall', () => {
	draw('center_stage');

	expect(host.querySelectorAll('.strip .block')).toHaveLength(5);
	expect(host.querySelectorAll('.glyph .block'), 'and one feed above it').toHaveLength(1);
});
