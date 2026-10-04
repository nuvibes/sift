/*
 * The rating's one dressing, and the three things it owes.
 *
 * There is one look, held to the heart's own rules, because the heart has no second dressing and
 * the two halves of one opinion must read as a pair.
 *
 * Pinned here is what a screenshot would not settle: the glyph is unfilled until there is a rating
 * (a filled yellow star over an unrated file says it is rated), the mark still reads the value out
 * rather than hiding it behind the menu, and pressing it opens the chooser. The chooser's answers
 * are `RatingChoices`', tested beside it.
 */
import { flushSync, mount, unmount, type ComponentProps } from 'svelte';
import { afterEach, expect, it, vi } from 'vitest';

import RatingChip from './RatingChip.svelte';

let host: HTMLElement | undefined;
let mounted: Record<string, unknown> | undefined;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = undefined;
	host?.remove();
	host = undefined;
});

function render(props: ComponentProps<typeof RatingChip>): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(RatingChip, { target: host, props }) as Record<string, unknown>;
	flushSync();
	return host;
}

/** The one thing to press. */
function trigger(): HTMLButtonElement {
	const found = host?.querySelector<HTMLButtonElement>('button');
	if (!found) throw new Error('the rating has nothing to press');
	return found;
}

it('is a bare mark with no pill at all, wherever it is drawn', () => {
	/*
	 * A bare glyph like the heart, not a bordered chip. `Chip` puts its own class on what it draws,
	 * so the absence of that class is the assertion.
	 */
	render({ rating: 8, label: 'This file' });

	expect(host?.querySelector('.chip')).toBeNull();
	expect(host?.querySelector('.mark')).not.toBeNull();
});

it('draws the OUTLINE glyph while there is no rating', () => {
	/*
	 * `Icon` marks a filled glyph with the `filled` class; an unrated star must not wear it, or the
	 * loudest thing on an unrated row is a mark saying a rating is there.
	 */
	render({ rating: null, label: 'This file' });

	expect(host?.querySelector('.star .filled')).toBeNull();
});

it("fills the glyph once there is one, which is the heart's rule exactly", () => {
	render({ rating: 8, label: 'This file' });

	expect(host?.querySelector('.star .filled')).not.toBeNull();
});

it('still reads the rating out, which a heart has no equivalent of', () => {
	/* The one place the pair genuinely differ: a heart is one bit and says everything by being
	   filled, and a rating is ten. A mark with no figure would be a readout you cannot read without
	   opening the menu, which is not a readout. */
	render({ rating: 8, label: 'This file' });

	expect(host?.querySelector('.figure')?.textContent).toBe('4');
	expect(trigger().getAttribute('aria-label')).toBe('This file: 4 out of 5');
});

it('says so plainly when there is no rating yet', () => {
	// No figure to draw, and the star alone is a complete sentence to look at and half of one to
	// hear, so the label is the whole of it.
	render({ rating: null, label: 'This file' });

	expect(host?.querySelector('.figure')).toBeNull();
	expect(trigger().getAttribute('aria-label')).toBe('This file: not rated');
});

it("takes the heart's own sizes, so the pair cannot drift apart", () => {
	/* The star stands beside a heart on every surface that draws both. An 18px heart beside a 20px
	   star reads as two unrelated controls, so the size is one list shared with
	   `Heart` and set at the call site for both at once. */
	render({ rating: 8, size: 20, label: 'This file' });

	expect(host?.querySelector('.star .icon')?.classList.contains('size-20')).toBe(true);
});

it('opens the chooser when the mark is pressed', async () => {
	/* The half that is easy to lose while changing how a trigger is drawn: the menu still opens
	   when the mark is pressed. Waited for rather than read at once: the surface is portalled
	   and positioned, so it arrives a frame after the press. */
	render({ rating: 8, label: 'This file' });

	trigger().click();

	await vi.waitFor(() => {
		flushSync();
		expect(document.querySelector('[role="group"][aria-label="Rating"]')).not.toBeNull();
	});
});

it('has nothing to press at all when the rating is only being shown', () => {
	// A readout with no chooser behind it. A disabled control is the honest drawing of that, rather
	// than one that opens a menu whose answers would be refused.
	render({ rating: 8, readonly: true, label: 'This file' });

	expect(trigger().disabled).toBe(true);
});
