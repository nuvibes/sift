import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import SavedFilters from './SavedFilters.svelte';
import { savedSearches, type SavedSearch } from '$lib/search/saved-searches.svelte';

/*
 * The kept filters at the foot of the filter panel, and the one being edited.
 *
 * What is pinned here rather than in the browser is the draft. The columns are the editor, and
 * editing a kept filter must not put it on the screen, which would cost the view somebody was
 * working in and make the chips row describe the filter instead of the screen. So an edit has a
 * draft and nothing navigates, and these assert that shape: the chips describe the draft, Save
 * writes the draft, a refused save leaves the edit standing, and neither leaving nor saving touches
 * whatever filters the screen.
 *
 * The store is a module singleton, so it is filled directly and `loaded` is set: `ensure` returns
 * at once and nothing here reaches the network.
 */

const KEPT: SavedSearch[] = [
	{ id: 'one', name: 'Runway clips', query: 'tags=runway', kind: 'asset' },
	{ id: 'two', name: 'Remix', query: 'tags=Remix&sites=Lanternfield', kind: 'asset' },
	/* Kept on the People wall, so it is in the one list the store holds and belongs to none of the
	   drawings below. Every test here draws the library's own, so this is the row that proves the
	   panel divides them rather than showing whatever it was handed. */
	{ id: 'theirs', name: 'Blondes', query: 'hair_color=BLONDE', kind: 'person' }
];

let host: HTMLDivElement;
let mounted: Record<string, unknown> | null = null;

function draw(props: Record<string, unknown> = {}) {
	mounted = mount(SavedFilters, {
		target: host,
		props: { current: 'tags=beach', onapply: () => {}, ...props }
	}) as Record<string, unknown>;
	flushSync();
}

/** The region the edit is drawn in. */
function editingRegion(): Element | null {
	return host.querySelector('[aria-label="Editing a saved filter"]');
}

function pressing(words: string): HTMLButtonElement | undefined {
	return [...host.querySelectorAll('button')].find((one) => one.textContent?.includes(words));
}

it("draws the filters kept on THIS wall, and none of another wall's", () => {
	/* One account keeps one list and every filter in it is spelled in the vocabulary of the wall it
	   was made on. Offered anywhere else it would write an address that wall does not read: a pill
	   that changes the link and moves nothing, which is why each row says where it came from. */
	draw({ kind: 'person' });

	expect(host.textContent).toContain('Blondes');
	expect(host.textContent).not.toContain('Runway clips');
});

it("does not open another wall's edit over these columns", () => {
	/* The draft outlives the panel and the screen both, by design: somebody editing a filter opens
	   and closes it more than once. So an edit begun on People can still be open on the library, and
	   the columns here are spelled in the file language: they would write values into somebody's kept
	   filter that its own wall cannot read, with nothing on screen to say an edit was even open. */
	savedSearches.editing = {
		id: 'theirs',
		name: 'Blondes',
		draft: 'hair_color=BLONDE',
		kind: 'person'
	};
	draw();

	expect(editingRegion()).toBeNull();
});

it('says nothing is kept HERE when this wall has none, with other walls full', () => {
	/* The empty state is per wall as well. Reading the whole list it would stay silent on a wall
	   with nothing kept on it, and the invitation to keep one, which is the only thing that screen
	   can usefully say, would never appear. */
	draw({ kind: 'tag' });

	expect(host.textContent).toContain('No saved filters yet');
});

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
	savedSearches.items = [...KEPT];
	savedSearches.loaded = true;
	savedSearches.editing = null;
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host.remove();
	savedSearches.items = [];
	savedSearches.loaded = false;
	savedSearches.editing = null;
});

/* The words on a pill, without the funnel in front of them. A glyph is a ligature and therefore
   real text inside the element, and a private-use codepoint is not whitespace, so `.trim()` does
   not take it off and it reads as a leading space in a failure message. */
function pillNames(): string[] {
	return [...host.querySelectorAll('.name')].map((pill) =>
		[...pill.childNodes]
			.filter((node) => !(node instanceof Element && node.classList.contains('icon')))
			.map((node) => node.textContent ?? '')
			.join('')
			.trim()
	);
}

it('draws one pill per kept filter', () => {
	draw();

	expect(pillNames()).toEqual(['Runway clips', 'Remix']);
});

it('names the control that saves a filter when nothing has been kept', () => {
	savedSearches.items = [];
	draw();

	expect(host.querySelector('[role="status"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
		'No saved filters yet. Select some filters, then press the Add to saved filters icon on the filters bar.'
	);
});

it('draws nothing about editing until something is being edited', () => {
	draw();

	expect(editingRegion()).toBeNull();
});

it('starts the draft as what the filter holds, and does NOT touch the screen', () => {
	/*
	 * Opening an editor sets a draft and the screen stays exactly where it was, which is what lets
	 * the bar go on describing it.
	 */
	const applied = vi.fn();
	draw({ onapply: applied });

	const pill = host.querySelectorAll('.kept')[1];
	expect(pill).toBeTruthy();
	savedSearches.editing = {
		id: 'two',
		name: 'Remix',
		draft: 'tags=Remix&sites=Lanternfield',
		kind: 'asset'
	};
	flushSync();

	expect(applied).not.toHaveBeenCalled();
	expect(editingRegion()?.querySelector('h3')?.textContent).toContain('Remix');
});

it('draws the DRAFT, not what is narrowing the screen', () => {
	/*
	 * `current` is deliberately something else: read from the screen, these chips would describe
	 * whatever somebody happened to be looking at.
	 */
	savedSearches.editing = {
		id: 'two',
		name: 'Remix',
		draft: 'tags=Remix&sites=Lanternfield',
		kind: 'asset'
	};
	draw({ current: 'tags=beach&rating=8' });

	const words = editingRegion()?.textContent ?? '';
	expect(words).toContain('Lanternfield');
	expect(words).not.toContain('beach');
});

it('leaves the screen alone on Cancel, and stops editing', () => {
	const applied = vi.fn();
	savedSearches.editing = { id: 'two', name: 'Remix', draft: 'tags=Remix', kind: 'asset' };
	draw({ onapply: applied });

	pressing('Cancel')?.click();
	flushSync();

	expect(savedSearches.editing).toBeNull();
	expect(applied).not.toHaveBeenCalled();
});

it('keeps the DRAFT on Save, and still leaves the screen alone', () => {
	const applied = vi.fn();
	const update = vi.spyOn(savedSearches, 'update').mockResolvedValue(undefined);
	savedSearches.editing = {
		id: 'two',
		name: 'Remix',
		draft: 'tags=Remix&sites=Lanternfield',
		kind: 'asset'
	};
	draw({ current: 'tags=beach', onapply: applied });

	pressing('Save')?.click();

	return vi.waitFor(() => {
		expect(update).toHaveBeenCalledWith('Remix', 'tags=Remix&sites=Lanternfield', 'asset');
		expect(savedSearches.editing).toBeNull();
		expect(applied).not.toHaveBeenCalled();
		update.mockRestore();
	});
});

it('leaves the edit standing when the save is refused', () => {
	/* The one with a server call in front of it. An edit ended before the await, or ended whatever
	   the server said, would drop the change and say nothing. */
	const update = vi.spyOn(savedSearches, 'update').mockRejectedValue(new Error('no'));
	savedSearches.editing = { id: 'two', name: 'Remix', draft: 'tags=Remix', kind: 'asset' };
	draw();

	pressing('Save')?.click();

	return vi.waitFor(() => {
		expect(savedSearches.editing).not.toBeNull();
		update.mockRestore();
	});
});

it('says a kept filter was picked, which is what shuts the panel behind it', () => {
	const applied = vi.fn();
	draw({ onapply: applied });

	host.querySelector<HTMLElement>('.name')?.click();

	expect(applied).toHaveBeenCalledWith('tags=runway');
});

it('the chips under Editing answer to the three verbs the host handed down', () => {
	/* They point at the DRAFT rather than the address: the host decides which, and this file only
	   passes the dimension on, with the one value a chip draws, or the raw parameter for
	   any-or-all, which belongs to the whole filter. */
	const flipped = vi.fn();
	const dropped = vi.fn();
	const switched = vi.fn();
	savedSearches.editing = {
		id: 'one',
		name: 'Runway clips',
		draft: 'tags=runway|Edited',
		kind: 'asset'
	};
	draw({ onflip: flipped, ondrop: dropped, onswitchmatch: switched });

	const editing = editingRegion()!;
	editing.querySelectorAll<HTMLElement>('.chip .body')[1]?.click();
	editing.querySelector<HTMLElement>('.chip .remove')?.click();
	editing.querySelector<HTMLElement>('.aside button')?.click();

	expect(flipped).toHaveBeenCalledWith('tags', 'Edited');
	expect(dropped).toHaveBeenCalledWith('tags', 'runway');
	expect(switched).toHaveBeenCalledWith('tags', 'runway|Edited');
});

it('draws two values of one dimension as two chips, exactly as the bar does', () => {
	/* One chip for "runway or Edited" can only be refused whole: there is no way to keep one tag
	   and refuse the other in a kept filter. A chip per value is what a press can refuse alone. */
	savedSearches.editing = {
		id: 'one',
		name: 'Runway clips',
		draft: 'tags=runway|Edited&tags=-Remix',
		kind: 'asset'
	};
	draw({ onflip: vi.fn(), ondrop: vi.fn(), onswitchmatch: vi.fn() });

	const chips = [...editingRegion()!.querySelectorAll('.chip')].map((one) =>
		(one.textContent ?? '').replace(/\s+/g, ' ').trim()
	);
	expect(chips).toHaveLength(3);
	expect(chips[0]).toContain('runway');
	expect(chips[0]).not.toContain('Edited');
	expect(chips[1]).toContain('Edited');
	expect(chips[2]).toContain('not');
	expect(chips[2]).toContain('Remix');
});

it('the chips only DESCRIBE where the host offered no verbs', () => {
	/* The theater's picker has nothing to write to, so a cross there would be a control that does
	   nothing, which reads as broken rather than as not-here. */
	savedSearches.editing = { id: 'one', name: 'Runway clips', draft: 'tags=runway', kind: 'asset' };
	draw();

	expect(editingRegion()!.querySelector('button.remove')).toBeNull();
});

it('draws ONE box, with the edit inside it rather than under it', () => {
	/* Two identical outlines stacked read as two lists rather than as a list and the thing being done
	   to one of its rows. */
	savedSearches.editing = { id: 'one', name: 'Runway clips', draft: 'tags=runway', kind: 'asset' };
	draw();

	const section = host.querySelector('section[aria-label="Saved filters"]')!;
	expect(section.querySelector('[aria-label="Editing a saved filter"]')).not.toBeNull();
	expect(host.querySelectorAll('section')).toHaveLength(1);
});

it('lets the panel fall shut while an edit is open, and holds it while a name is being typed', () => {
	/*
	 * `data-unfinished` stops the panel closing on hover (see `screen-bar`), and which region
	 * carries it is a decision.
	 *
	 * The edit does not carry it: the draft lives in the store, so shutting the panel costs nothing
	 * and reopening shows the edit where it was, while a panel that will not close when you point
	 * away is the more annoying fault.
	 *
	 * The rename does, because a half-typed name is destroyed by a close, and `#someoneIsTyping`
	 * holds only while the caret is still in the box.
	 */
	savedSearches.editing = { id: 'one', name: 'Runway clips', draft: 'tags=runway', kind: 'asset' };
	draw();

	expect(editingRegion()!.hasAttribute('data-unfinished')).toBe(false);

	// Open the rename on the first pill, through the verb the menu declares.
	host.querySelector<HTMLElement>('.kept button.more')?.click();
	flushSync();
	expect(host.querySelector('.renaming')).toBeNull();
});

it('offers no Edit where the host cannot edit', () => {
	/* The theater's picker draws this list to choose a cell's source, not to change one. A menu row
	   that fails reads as broken rather than as not-here, so it is simply not drawn. */
	draw({ editable: false });

	expect(host.querySelector('.kept')).not.toBeNull();
	expect(host.textContent).not.toContain('No saved filters yet');
});

it('draws Everything when the draft narrows to nothing at all', () => {
	/* A filter can be emptied down to nothing by taking its last chip off, and a region with no chips
	   in it reads as one that failed to draw rather than as one that holds no filters. */
	savedSearches.editing = { id: 'one', name: 'Runway clips', draft: '', kind: 'asset' };
	draw();

	expect(editingRegion()?.textContent).toContain('Everything');
});

/*
 * What is on screen while the write is in flight.
 *
 * Keeping a filter goes to the server and back, and can take seconds; a panel unchanged all that
 * time reads as a press that did nothing. Both writes are covered, answered in two places for a
 * reason: a form's Save is a button and wears `Button.busy`, while "Update from current filters" is
 * a menu row gone the instant it is pressed, so the pill carries it.
 *
 * Each is held open by a promise the test resolves, the only way to see the state between the press
 * and the answer.
 */
function held(): { promise: Promise<void>; finish: () => void } {
	let finish: () => void = () => {};
	const promise = new Promise<void>((resolve) => {
		finish = resolve;
	});
	return { promise, finish };
}

it('turns the Save while it is being written, and refuses a second press', async () => {
	const waiting = held();
	const update = vi.spyOn(savedSearches, 'update').mockReturnValue(waiting.promise);
	savedSearches.editing = { id: 'two', name: 'Remix', draft: 'tags=Remix', kind: 'asset' };
	draw();

	const save = pressing('Save')!;
	save.click();
	flushSync();

	expect(save.getAttribute('aria-busy')).toBe('true');
	expect(save.disabled).toBe(true);
	// And the way out is closed with it: dropping the draft half way through writing it leaves
	// somebody unable to say what was kept.
	expect(pressing('Cancel')?.disabled).toBe(true);

	save.click();
	waiting.finish();

	await vi.waitFor(() => {
		expect(savedSearches.editing).toBeNull();
	});
	expect(update).toHaveBeenCalledTimes(1);
	update.mockRestore();
});

it('turns the pill that "Update from current filters" was asked about, and only that one', async () => {
	const waiting = held();
	const update = vi.spyOn(savedSearches, 'update').mockReturnValue(waiting.promise);
	draw({ current: 'tags=beach' });

	host.querySelector<HTMLElement>('.kept button.more')?.click();
	const row = await vi.waitFor(() => {
		const found = [...document.querySelectorAll('[role="menuitem"]')].find((one) =>
			(one.textContent ?? '').includes('Update from current filters')
		);
		expect(found).toBeTruthy();
		return found as HTMLElement;
	});
	row.click();

	await vi.waitFor(() => {
		expect(host.querySelector('.kept .spinner')).not.toBeNull();
	});
	// One pill, not the row. The other kept filter is untouched and still pressable: a panel that
	// went busy as a whole would say the wrong thing about every filter in it.
	expect(host.querySelectorAll('.kept .spinner')).toHaveLength(1);

	// And asking again while it is still running sends nothing. The menu is deliberately NOT closed
	// off while the pill turns (the right-click offers the same rows and cannot be) so the
	// second write is refused by the panel rather than by the door.
	host.querySelector<HTMLElement>('.kept button.more')?.click();
	const again = await vi.waitFor(() => {
		const found = [...document.querySelectorAll('[role="menuitem"]')].find((one) =>
			(one.textContent ?? '').includes('Update from current filters')
		);
		expect(found).toBeTruthy();
		return found as HTMLElement;
	});
	again.click();
	flushSync();
	expect(update).toHaveBeenCalledTimes(1);

	waiting.finish();

	await vi.waitFor(() => {
		expect(host.querySelector('.kept .spinner')).toBeNull();
	});
	expect(update).toHaveBeenCalledWith('Runway clips', 'tags=beach', 'asset');
	update.mockRestore();
});
