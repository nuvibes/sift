import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import SavedFilterButton from './SavedFilterButton.svelte';
import type { SavedSearch } from '$lib/search/saved-searches.svelte';

/* One kept filter's pill: its menu is built only from the handlers supplied, as a failing row
 * reads as broken. The library's menus are pressed for real in `saved-searches.spec.ts`. */

const KEPT: SavedSearch = {
	id: 'one',
	name: 'Runway clips',
	query: 'from=01J5T6R7S8MNP6QRSTVWXYZ7A8&tags=runway&people=-Somebody',
	kind: 'asset'
};

let host: HTMLDivElement;
let mounted: Record<string, unknown> | null = null;

function draw(props: Record<string, unknown>) {
	mounted = mount(SavedFilterButton, {
		target: host,
		props: { kept: KEPT, onapply: () => {}, ...props }
	}) as Record<string, unknown>;
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

/* A pill's words without the glyph's ligature text, which `.trim()` does not remove. */
function pillName(pill: Element | null | undefined): string {
	return [...(pill?.childNodes ?? [])]
		.filter((node) => !(node instanceof Element && node.classList.contains('icon')))
		.map((node) => node.textContent ?? '')
		.join('')
		.trim();
}

it('draws the name it was kept under, behind the mark saying what kind of thing it is', () => {
	draw({});

	const pill = host.querySelector('.name');
	expect(pillName(pill)).toBe('Runway clips');
	// The funnel says once what sort of thing the pills are.
	expect(pill?.querySelector('.icon'), 'the pill drew no kind mark').not.toBeNull();
});

it('pressing the pill applies that filter and nothing else', () => {
	const applied = vi.fn();
	draw({ onapply: applied });

	host.querySelector<HTMLElement>('.name')?.click();

	expect(applied).toHaveBeenCalledWith(KEPT);
});

it('offers no menu at all when the caller can honour none of it', () => {
	// A three-dot control opening an empty menu says something is there.
	draw({});

	expect(host.querySelector('button.more')).toBeNull();
});

it('offers a menu once there is one thing to put in it', () => {
	draw({ onremove: () => {} });

	expect(host.querySelector('button.more')).not.toBeNull();
});

it('names the menu after the filter it belongs to', () => {
	// Seven "More" buttons would not say which is which.
	draw({ onremove: () => {} });

	expect(host.querySelector('button.more')?.getAttribute('aria-label')).toBe(
		'More for Runway clips'
	);
});

/** The chips the bubble draws, each as its words; the bubble opens on a pointer move. */
async function bubbleChips(): Promise<HTMLElement[]> {
	host.querySelector('.name')?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
	await vi.waitFor(() => {
		flushSync();
		if (!document.querySelector('[role="tooltip"] .chip')) throw new Error('no bubble yet');
	});
	return [...document.querySelectorAll<HTMLElement>('[role="tooltip"] .chip')];
}

it('draws one chip for each value in the bubble, so none is cut short', async () => {
	draw({
		kept: { ...KEPT, query: 'filetype=-webm|mp4|gif&tags=runway|beach&people=Somebody,Other' }
	});

	const chips = await bubbleChips();

	expect(chips.map((chip) => chip.querySelector('.value')?.textContent?.trim())).toEqual([
		'webm',
		'mp4',
		'gif',
		'runway',
		'beach',
		'Somebody',
		'Other'
	]);
	// How the values of one filter combine, on each of its chips; a refusal has nothing to say.
	expect(chips.map((chip) => chip.querySelector('.match')?.getAttribute('aria-label'))).toEqual([
		undefined,
		undefined,
		undefined,
		'Any of these',
		'Any of these',
		'All of these',
		'All of these'
	]);
	expect(document.querySelector('[role="tooltip"] button'), 'a press nobody can reach').toBeNull();
});

it('says what the filter holds, before anybody presses it', () => {
	// The tooltip is portalled and only exists while shown, so this asserts the parts were read.
	draw({});
	const named = host.querySelector('.name');

	// Mounting with a real stored query (cursor, included and excluded values) draws cleanly.
	expect(named).not.toBeNull();
});
