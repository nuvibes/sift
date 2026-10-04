/* The searches somebody chose to keep, under names they gave them.
 *
 * The client half of the saved-search feature. It holds the list, and the three things done to it
 * (load, save, delete), each of which calls the server and then reconciles the local copy, so
 * the modal showing this never has to guess what the server ended up holding.
 *
 * A module singleton, like the other small stores: there is one account signed in, so there is one
 * list, and a second copy would be a second answer to "what have I saved". It loads lazily (the
 * first time something asks), so a session that never opens the Filters modal never fetches it.
 */

import { SvelteMap } from 'svelte/reactivity';
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { libraryChanges, mine } from '$lib/library/changes.svelte';
import { asNamedFilters } from '$lib/search/query-parts';

type KeptName = components['schemas']['KeptNameOut'];

/* The notes are optional HERE although the server always sends the list: every other reader of a
 * kept filter (the pill, the bar's recognition, the swap's picker) holds the name and the query and
 * nothing else, and only this store reads the notes, once, as the list arrives. */
export type SavedSearch = Omit<components['schemas']['SavedSearchOut'], 'named'> & {
	named?: KeptName[];
};

/*
 * WHAT A KEPT FILTER'S CHIP SAYS WHERE THE ADDRESS CANNOT SAY IT.
 *
 * A kept filter names each thing by its id, and the server reads it back under the name the thing
 * has TODAY, so a rename needs no rewrite and the chip follows it. Three values come back that the
 * address cannot put into words on its own, and the server notes each: an id kept because two
 * things share its name (the note carries the name), an id nothing answers to any more (deleted
 * since it was kept), and a name nothing answers to any more (renamed before filters were kept by
 * id). The chip reads the note, so a gone tag says so instead of drawing a ULID or a name that is
 * no longer anywhere in the library.
 *
 * Rebuilt whole on every load, and the list loads again whenever the library changes, so a note
 * never outlives the thing it describes coming back.
 */
const SAID = new SvelteMap<string, string>();

/** What each field's thing is called in a sentence. The screen's own words: a Site, a Photo Set. */
const THING: Record<string, string> = {
	tags: 'a tag',
	people: 'a person',
	sites: 'a Site',
	platforms: 'a Site',
	collections: 'a collection',
	photo_sets: 'a Photo Set',
	songs: 'a song',
	song: 'a song',
	in: 'a folder',
	folder: 'a folder'
};

const AN_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;

/** The words for a thing that is gone: by id, or by the name it was kept under. */
export function goneLabel(field: string, value: string): string {
	const thing = THING[field] ?? 'a thing';
	const which = field === 'people' ? 'who' : 'that';
	return AN_ID.test(value)
		? `${thing} ${which} no longer exists`
		: `${thing} named ${value} no longer exists`;
}

function remember(named: readonly KeptName[]): void {
	for (const one of named) {
		SAID.set(`${one.field} ${one.value}`, one.name ?? goneLabel(one.field, one.value));
	}
}

/** What a kept filter's chip reads for this value, where the address alone cannot say it. */
export function keptValueLabel(field: string, value: string): string | undefined {
	return SAID.get(`${field} ${value}`);
}

/**
 * The wall a kept filter is a question about, when nothing says otherwise.
 *
 * The noun six screens are without saying so, and the server's own default. So a filter kept
 * before the walls were told apart is one of these, and so is anything saved from the library.
 */
export const ASSET_WALL = 'asset';

class SavedSearches {
	items = $state<SavedSearch[]>([]);
	loaded = $state(false);
	busy = $state(false);

	/**
	 * The filters kept on ONE wall, newest first.
	 *
	 * The list is fetched whole and divided here rather than asked for per wall. One account keeps a
	 * few dozen filters at most, so a request per wall would be several requests for one small list,
	 * and each would need its own notion of whether it had been loaded, which is three flags and
	 * a cache to go wrong instead of one.
	 *
	 * A filter is offered ONLY on the wall it was kept on, because it is spelled in that wall's own
	 * vocabulary: a People filter put on the library would change the address and filter nothing.
	 */
	on(kind: string): SavedSearch[] {
		return this.items.filter((item) => (item.kind ?? ASSET_WALL) === kind);
	}

	/** Fetch the list once. Safe to call on every modal open; it only asks the first time. */
	async ensure(): Promise<void> {
		if (this.loaded || this.busy) return;
		await this.reload();
	}

	/*
	 * WHAT IS KEPT IS KEPT AS FILTERS, whichever way it was written when it was saved.
	 *
	 * `q` is the search box's own text. A filter saved while something was typed carries that text,
	 * so applying it would put seven chips back into the search bar from a gesture that was not
	 * typing, and nothing could say what such a filter HOLDS, because what shows a kept filter
	 * reads named parameters and every one of those seven is inside `q`.
	 *
	 * So the list is normalised as it arrives and again on the way out. Both, deliberately: the way
	 * IN fixes what is stored the next time anything writes, and the way OUT fixes the ones already
	 * stored, which is every filter anybody has, and they are not going to be re-saved by hand.
	 *
	 * It costs one `/search/parse` per kept filter that has a `q`, once per load, and none at all
	 * for one that has not. See `asNamedFilters` for why the two spellings are the same query and
	 * for what it deliberately leaves alone.
	 */
	async reload(): Promise<void> {
		this.busy = true;
		try {
			const answer = await api.get<components['schemas']['SavedSearches']>('/search/saved');
			this.items = await Promise.all(
				answer.items.map(async (item) => ({ ...item, query: await asNamedFilters(item.query) }))
			);
			SAID.clear();
			for (const item of answer.items) remember(item.named ?? []);
			this.loaded = true;
		} finally {
			this.busy = false;
		}
	}

	/* Keep a query under a name. Saving under a name already used replaces it (the server upserts
	 * on the name), so the list is reloaded rather than appended to, which is what keeps an edit
	 * from showing as a second row until the next fetch. */
	async save(name: string, query: string, kind: string = ASSET_WALL): Promise<void> {
		await api.post('/search/saved', {
			body: { name, kind, query: await asNamedFilters(asAQuestion(query)) }
		});
		await this.reload();
	}

	/* Change what a saved search is CALLED, keeping the query it points at.
	 *
	 * Saving under an existing name already replaces that name's query, so editing the query has
	 * always been possible; this is the other half. Optimistic, because a rename is a label moving
	 * and a failed one is put back by the reload in the caller.
	 */
	async rename(id: string, name: string): Promise<void> {
		const before = this.items;
		this.items = this.items.map((item) => (item.id === id ? { ...item, name } : item));
		try {
			await api.patch(`/search/saved/${id}`, { body: { name } });
		} catch (failure) {
			this.items = before;
			throw failure;
		}
	}

	/*
	 * WHICH ONE IS BEING EDITED, and the query it is being edited INTO.
	 *
	 * ## The draft, and why it is not the address
	 *
	 * Putting the filter ON the screen to edit it costs the whole screen: what somebody was
	 * filtering by has to be remembered and given back, the chips row says the filter rather than
	 * the screen, and opening an editor throws away the view they were working in. The chips on the
	 * bar are the person's own and the ones in the edit are the filter's.
	 *
	 * So the edit has a draft of its own. The columns read and write the draft while it is open,
	 * the bar goes on describing the screen, and nothing navigates, which also means there is
	 * nothing to restore, no jump when it opens, and no way for an edit to cost somebody their
	 * place.
	 *
	 * Here rather than in the panel, and it is the same reason the list is: the panel is unmounted
	 * every time the menu shuts, and a person editing a filter will open and close it more than
	 * once before they are done. The draft survives that.
	 */
	editing = $state<{ id: string; name: string; draft: string; kind: string } | null>(null);

	/*
	 * Keep a query under a name that already exists, which is what "update" and "save an edit" both
	 * are.
	 *
	 * `save` already does it (the server upserts on the name), and this exists so the two call
	 * sites read as what they mean rather than as a save that happens to overwrite.
	 */
	async update(name: string, query: string, kind: string = ASSET_WALL): Promise<void> {
		await this.save(name, query, kind);
	}

	async remove(id: string): Promise<void> {
		// Optimistic: it is gone from the list at once, and the server is told. A failed delete is
		// reconciled by the next load rather than left mid-animation.
		this.items = this.items.filter((item) => item.id !== id);
		await api.del(`/search/saved/${id}`);
	}
}

/**
 * The address with its position taken off: a saved search is a question, not a place in the answer.
 *
 * The grid writes `from=<id>` into the address as somebody scrolls, so a search saved with the
 * whole address would re-open at the row it was saved at: row 57 of 58. `offset` is the same
 * thing in the other spelling.
 */
export function asAQuestion(query: string): string {
	const params = new URLSearchParams(query);
	params.delete('from');
	params.delete('offset');
	return params.toString();
}

export const savedSearches = new SavedSearches();

/* Saved somewhere else, and this list is the same account's.
 *
 * Only once it has been read at all: a store that has never been opened has nothing to bring up to
 * date, and asking would be a request on behalf of a screen nobody has looked at.
 */
mine.subscribe(() => {
	if (savedSearches.loaded) void savedSearches.reload();
});

/* Something in the library was renamed, created or deleted. A kept filter reads the names things
 * have now, so a tag renamed on another screen has to reach the chips without anybody reloading
 * the page: the list is read again, under today's names. Only once it has been read at all, for the
 * reason above. */
libraryChanges.subscribe(() => {
	if (savedSearches.loaded) void savedSearches.reload();
});
