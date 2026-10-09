/* The rating's one dressing, held to the heart's rules: unfilled until rated, the value read out,
 * and a press opens the chooser (`RatingChoices`, tested beside it). */
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
	// A bare glyph like the heart: no `Chip` class.
	render({ rating: 8, label: 'This file' });

	expect(host?.querySelector('.chip')).toBeNull();
	expect(host?.querySelector('.mark')).not.toBeNull();
});

it('draws the OUTLINE glyph while there is no rating', () => {
	// An unrated star never wears `filled`.
	render({ rating: null, label: 'This file' });

	expect(host?.querySelector('.star .filled')).toBeNull();
});

it("fills the glyph once there is one, which is the heart's rule exactly", () => {
	render({ rating: 8, label: 'This file' });

	expect(host?.querySelector('.star .filled')).not.toBeNull();
});

it('still reads the rating out, which a heart has no equivalent of', () => {
	// A rating is ten values, so the figure is drawn.
	render({ rating: 8, label: 'This file' });

	expect(host?.querySelector('.figure')?.textContent).toBe('4');
	expect(trigger().getAttribute('aria-label')).toBe('This file: 4 out of 5');
});

it('says so plainly when there is no rating yet', () => {
	// Unrated, the label is the whole of it.
	render({ rating: null, label: 'This file' });

	expect(host?.querySelector('.figure')).toBeNull();
	expect(trigger().getAttribute('aria-label')).toBe('This file: not rated');
});

it("takes the heart's own sizes, so the pair cannot drift apart", () => {
	// The size list is Heart's, so the pair is set together.
	render({ rating: 8, size: 20, label: 'This file' });

	expect(host?.querySelector('.star .icon')?.classList.contains('size-20')).toBe(true);
});

it('opens the chooser when the mark is pressed', async () => {
	// The chooser still opens; waited for, as it is portalled.
	render({ rating: 8, label: 'This file' });

	trigger().click();

	await vi.waitFor(() => {
		flushSync();
		expect(document.querySelector('[role="group"][aria-label="Rating"]')).not.toBeNull();
	});
});

it('has nothing to press at all when the rating is only being shown', () => {
	// Read only: a disabled readout, not a menu of refused answers.
	render({ rating: 8, readonly: true, label: 'This file' });

	expect(trigger().disabled).toBe(true);
});
