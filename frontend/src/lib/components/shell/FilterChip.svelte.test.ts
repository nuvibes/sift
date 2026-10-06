import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import FilterChip from './FilterChip.svelte';
import { rememberFacetNames } from './facet-labels';

/*
 * The one drawing of a filter, which four places share.
 *
 * ## What is worth pinning here
 *
 * That it draws ONE chip for what it is handed, however many values that is.
 *
 * That the two ways values combine are told apart by a MARK (`&` against the splitting arrow)
 * and that the mark is not offered where pressing it would change nothing.
 *
 * And that it is inert when it is handed nothing to do. A tooltip is portalled and unfocusable, so a
 * control drawn inside one is a control nobody can reach; the read-only case is not a convenience,
 * it is the requirement `Tooltip`'s own `detail` states at length.
 */

let host: HTMLDivElement;
let mounted: Record<string, unknown> | null = null;

function draw(props: Record<string, unknown>) {
	mounted = mount(FilterChip, {
		target: host,
		props: { field: 'tags', values: ['runway'], ...props }
	}) as Record<string, unknown>;
	flushSync();
}

/** The words the chip shows, with the sound-only "not" left out of them. */
function words(): string {
	return host.querySelector('.value')?.textContent?.trim() ?? '';
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

it('draws ONE chip for a filter, whatever it holds', () => {
	draw({ values: ['runway', 'Edited'] });

	expect(host.querySelectorAll('.chip')).toHaveLength(1);
	expect(words()).toBe('runway or Edited');
});

it('says how the values combine in words, not only in a mark', () => {
	draw({ values: ['runway', 'Edited'], all: true });

	expect(words()).toBe('runway and Edited');
});

it('leads with the dimension the query language names it by', () => {
	/* Not the panel's heading. Two vocabularies for one dimension on one screen is what sends
	   somebody who read "Tags" off to type `Tags:` and get a text search. */
	draw({});

	expect(host.querySelector('.field')?.textContent).toBe('tags:');
});

/* The joiner mark, and ONLY it. Scoped to the any-or-all control rather than asked of the whole
   chip: a chip can carry a state mark of its own, and a bare `.icon` query would find that one and
   say the ampersand had been replaced by an icon. */
function joiner(): Element | null {
	return host.querySelector('.pressable .icon');
}

it('marks all-of with an ampersand, because the icon set has no glyph for it', () => {
	draw({ values: ['a', 'b'], all: true, onswitch: () => {} });

	expect(host.querySelector('.amp')?.textContent).toBe('&');
	expect(joiner()).toBeNull();
});

it('marks any-of with the splitting arrow', () => {
	draw({ values: ['a', 'b'], onswitch: () => {} });

	expect(host.querySelector('.amp')).toBeNull();
	expect(joiner()).not.toBeNull();
});

it('wears no glyph for its dimension: the token on it IS the dimension', () => {
	/*
	 * No dimension glyph on the chip: that suits the chooser, where a glyph sits beside one word on
	 * its own row, but a chip is already four things in a pill a few characters wide, and the token
	 * `tags:` already names the dimension. The state box is not a dimension glyph and stays: it is
	 * the only thing saying whether the filter includes or refuses.
	 */
	draw({});

	expect(host.querySelector('.glyph')).toBeNull();
	expect(host.querySelector('.box .mark'), 'the chip lost its state mark').not.toBeNull();
});

it('offers no way to change the joiner on a single value', () => {
	draw({ values: ['runway'], onswitch: () => {} });

	expect(host.querySelector('.aside')).toBeNull();
});

it('offers no way to change the joiner on a REFUSED filter', () => {
	/* "Not any of these" and "not all of these" are the same set of files, so the control would
	   rewrite the address and leave the screen exactly as it was: worse than no control. */
	draw({ values: ['a', 'b'], excluded: true, onswitch: () => {} });

	expect(host.querySelector('.aside')).toBeNull();
});

it('says a refusal three ways, one of which is not a colour', () => {
	draw({ excluded: true });

	expect(host.querySelector('.chip')?.className).toContain('refused');
	expect(host.querySelector('.value')?.className).toContain('struck');
	// Read out to anybody not looking at it: a line through some text is not announced, and a chip
	// that says "tags: runway" out loud while meaning the opposite is worse than a plain one.
	expect(host.querySelector('.said')?.textContent).toBe('not');
});

it('refuses without striking, when the words are the refusal', () => {
	/* A filter asking whether a dimension is EMPTY reads as a refusal and wears the refusal colour,
	   and its word is "none": a line through that says the opposite of what it means. The two are
	   separate props for this one case, and this is the case. */
	draw({ values: ['none'], excluded: true, struck: false });

	expect(host.querySelector('.chip')?.className).toContain('refused');
	expect(host.querySelector('.value')?.className).not.toContain('struck');
	expect(host.querySelector('.said')).toBeNull();
});

it('draws how the values combine as a mark nobody can press, where it only describes', () => {
	draw({ values: ['a'], of: 2, all: true, matchShown: true });

	const mark = host.querySelector('.aside .match');
	expect(mark?.getAttribute('role')).toBe('img');
	expect(mark?.getAttribute('aria-label')).toBe('All of these');
	expect(mark?.querySelector('.amp')?.textContent).toBe('&');
	expect(host.querySelector('button')).toBeNull();
});

it('draws the splitting arrow in that mark for any of them', () => {
	draw({ values: ['a'], of: 2, matchShown: true });

	const mark = host.querySelector('.aside .match');
	expect(mark?.getAttribute('aria-label')).toBe('Any of these');
	expect(mark?.querySelector('.icon')).not.toBeNull();
	expect(mark?.querySelector('.amp')).toBeNull();
});

it('draws no such mark on one value, on a refusal, or unasked', () => {
	draw({ values: ['a'], of: 1, matchShown: true });
	expect(host.querySelector('.aside')).toBeNull();
	unmount(mounted!);

	draw({ values: ['a'], of: 2, excluded: true, matchShown: true });
	expect(host.querySelector('.aside')).toBeNull();
	unmount(mounted!);

	draw({ values: ['a'], of: 2 });
	expect(host.querySelector('.aside')).toBeNull();
});

it('keeps the press, never the mark, where the joiner can be changed', () => {
	draw({ values: ['a'], of: 2, matchShown: true, onswitch: () => {} });

	expect(host.querySelector('.match')).toBeNull();
	expect(host.querySelector('.aside .pressable')).not.toBeNull();
});

it('is inert when it is handed nothing to do', () => {
	/* What the tooltip needs. Portalled and unfocusable, so anything operable in there is operable
	   by nobody. */
	draw({ values: ['a', 'b'] });

	expect(host.querySelector('button')).toBeNull();
});

it('presses, removes and switches through the handlers it was given', () => {
	const chosen = vi.fn();
	const removed = vi.fn();
	const switched = vi.fn();
	draw({ values: ['a', 'b'], onselect: chosen, onremove: removed, onswitch: switched });

	host.querySelector<HTMLElement>('.chip .body')?.click();
	host.querySelector<HTMLElement>('.chip .remove')?.click();
	host.querySelector<HTMLElement>('.aside button')?.click();

	expect(chosen).toHaveBeenCalledOnce();
	expect(removed).toHaveBeenCalledOnce();
	expect(switched).toHaveBeenCalledOnce();
});

it('reads a value the way the panel reads it, not the way the address spells it', () => {
	/* `viewed=none` is a file nobody has opened, and a chip reading "none" beside a column reading
	   "Not viewed" is one fact spelled two ways on one screen. */
	draw({ field: 'viewed', values: ['none'] });

	expect(words()).toBe('Not viewed');
});

/*
 * What a source read cannot say here, and why there is no test for it.
 *
 * The mark's ink and the chip's hover are checked in a real browser: jsdom does not resolve
 * `:hover`, so `getComputedStyle` cannot say what a rule paints. At rest the mark is `--sift-ink-2`
 * while the chip's words are the accent (the mark's rule beats `Pressable`'s `color: inherit`);
 * pointing at the mark lights the mark alone, and pointing at the words leaves the mark as it was.
 *
 * A test asserting a selector would pin the spelling of the current fix rather than the property,
 * and the two come apart the moment either component's rules move.
 */

it("reads a same-music filter as the file's name once the strip has said it", () => {
	/* The value is a file's ID, and the address is all the chip has. The strip's heading remembers
	   the name as it is pressed; a chip for an ID nothing has named reads as the ID. */
	draw({ field: 'same_music', values: ['01HX0000000000000000000002'] });
	expect(words()).toBe('01HX0000000000000000000002');
	if (mounted) unmount(mounted);
	mounted = null;

	rememberFacetNames('same_music', [
		{ value: '01HX0000000000000000000002', label: 'Golden hour', count: 3 }
	]);
	draw({ field: 'same_music', values: ['01HX0000000000000000000002'] });
	expect(words()).toBe('Golden hour');
	expect(host.textContent).toContain('same_music:');
});

/* A kept filter's chip reads the words the list of kept filters gave for a value before any other
 * name: a thing deleted since it was kept says so, rather than drawing its id. */
it("reads a kept filter's own words for a value the address cannot name", async () => {
	const { savedSearches } = await import('$lib/search/saved-searches.svelte');
	const { api } = await import('$lib/api/client');
	const gone = '01J5T6R7S8XYZ3ABCDEFGH4JK5';
	const get = vi.spyOn(api, 'get').mockResolvedValue({
		items: [
			{
				id: 's',
				name: 'P',
				query: `tags=${gone}`,
				named: [{ field: 'tags', value: gone, name: null }]
			}
		]
	});
	await savedSearches.reload();
	draw({ values: [gone] });

	expect(words()).toBe('a tag that no longer exists');
	get.mockRestore();
});
