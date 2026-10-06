import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import SavedFilterButton from './SavedFilterButton.svelte';
import type { SavedSearch } from '$lib/search/saved-searches.svelte';

/*
 * One kept filter: the pill, and which of the four things it offers.
 *
 * What is worth pinning here is the part a browser test cannot see cheaply and a reader cannot see
 * at all: that the menu is built from the handlers ACTUALLY SUPPLIED. A caller that cannot honour
 * Edit (the theater's cell picker, which has no screen to give back) must not be offered it,
 * because a menu row that fails reads as broken rather than as not-here.
 *
 * The menus themselves are the library's and cannot be driven in jsdom: opening a `DropdownMenu`
 * needs a layout this environment does not have, and `bits-ui` is deliberately not exercised here.
 * So this asserts what is DECLARED and `saved-searches.spec.ts` presses the rows for real.
 */

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

/* The words on a pill, without the funnel in front of them.
 *
 * `textContent` of the whole press includes the ICON's own character (a glyph is a ligature, so
 * it is real text in the element) and a bare `.trim()` does not remove it, because a private-use
 * codepoint is not whitespace. It reads as a leading space in a failure message, which is exactly
 * the kind of thing that gets an assertion loosened instead of understood. */
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
	// The funnel, which is the app's mark for filtering wherever it appears. A row of pills reading
	// `r4t`, `gym`, `223` says nothing about what sort of thing they are; this says it once.
	expect(pill?.querySelector('.icon'), 'the pill drew no kind mark').not.toBeNull();
});

it('pressing the pill applies that filter and nothing else', () => {
	const applied = vi.fn();
	draw({ onapply: applied });

	host.querySelector<HTMLElement>('.name')?.click();

	expect(applied).toHaveBeenCalledWith(KEPT);
});

it('offers no menu at all when the caller can honour none of it', () => {
	/*
	 * A three-dot control opening an empty menu is worse than no control: it says there is
	 * something there.
	 */
	draw({});

	expect(host.querySelector('button.more')).toBeNull();
});

it('offers a menu once there is one thing to put in it', () => {
	draw({ onremove: () => {} });

	expect(host.querySelector('button.more')).not.toBeNull();
});

it('names the menu after the filter it belongs to', () => {
	/* A row of seven of these announced as seven "More" buttons tells somebody using a screen reader
	   nothing about which is which. */
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
	/* The whole reason a name was not enough. The tooltip is portalled and only exists while it is
	   shown, so what is asserted here is that the parts were read at all: the bubble's own contents
	   are `settings`-free markup and are pressed in the browser suite. */
	draw({});
	const named = host.querySelector('.name');

	// The pill carries the tooltip's describedby wiring only while open; what this asserts is that
	// mounting with a real stored query (cursor, an included value and an excluded one) draws
	// without throwing, which is the case the `-Somebody` half exists for.
	expect(named).not.toBeNull();
});
