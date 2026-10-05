/* The collection list, and the writes that change it.
 *
 * One place, because the list screen, a collection's own page and the drop target on the grid all
 * read the same thing, and a second copy would let one of them show a collection another had just
 * deleted.
 *
 * Counts are the server's and are scoped to whoever is asking, so they are never recomputed here.
 * A count worked out in the client would be a count over the rows this session happens to have
 * fetched, which for anybody but an admin is not the same number. And getting that wrong is how
 * a screen ends up telling somebody how much they are not being shown.
 */

import { coverBody, type CoverMore } from '$lib/entity/cover-frame';
import { overChunks, type BulkWriteDone } from '$lib/library/bulk';
import { api } from '$lib/api/client';
import { recorded } from '$lib/library/changes.svelte';
import { PICK_PAGE } from '$lib/search/frequent.svelte';
import { FIELDS } from '$lib/search/search.svelte';
import type { components } from '$lib/api/schema';
import { ENTITY_OPINION_SORTS, UNIVERSAL_SORTS } from '$lib/grid/sort-state.svelte';
import { asked } from '$lib/grid/anchor';
import { fillHeld, type CardPaging } from '$lib/grid/cards.svelte';
import type { RowSource } from '$lib/grid/grid.svelte';

/**
 * A collection, taken from the server's own definition rather than described again here: a
 * hand-written copy drifts (`cover_asset_id` declared always present where the server sends it only
 * when there is one). The tag store has the same rule: see the note above `Tag`.
 */
export type Collection = components['schemas']['CollectionSummary'];

export type CollectionItem = components['schemas']['CollectionItem'];

/** The address's filters as a collection's contents take them: the words and every field. */
export function contentsAsked(url: URL): Record<string, string[]> {
	const asked: Record<string, string[]> = {};
	for (const name of ['q', ...FIELDS]) {
		const values = url.searchParams.getAll(name).filter((value) => value.trim());
		if (values.length > 0) asked[name] = values;
	}
	return asked;
}

export type CollectionContents = components['schemas']['CollectionContents'];

/** A collection's arrangement as a wall of files pages it; the route takes no `from` or `after`. */
export function contentsSource(id: string): RowSource {
	return { path: `/collections/${id}/items`, anchored: false, sorts: [], filterable: true };
}

/* How many Collections one page of the wall holds. See the People wall for the reasoning; this is
 * the fallback before the wall has been laid out and a card measured. */
export const COLLECTIONS_PER_PAGE = 60;

export class Collections {
	items = $state<Collection[]>([]);
	loading = $state(false);
	failed = $state(false);
	loaded = $state(false);

	/* Empty this cache, so the next screen that wants it fetches again.
	 *
	 * For the vault: what is concealed never arrives, so a list held from while the vault was open
	 * still holds it after the vault shuts. Clearing the rows as well as the flag matters: a
	 * screen reading `items` directly would otherwise draw the old ones until the fetch lands.
	 */
	forget(): void {
		this.items = [];
		this.total = 0;
		this.loaded = false;
	}

	/** Rising counter so a slow response cannot overwrite a newer one. */
	#generation = 0;

	/* Any page still in the air is not the answer any more.
	 *
	 * A write that edits the list in place (a row made, a row removed) has to invalidate a
	 * load that was already outstanding, or that load's answer arrives afterwards and overwrites
	 * the edit. The generation counter is what a SLOW response is checked against; bumping it
	 * here makes a write count as a newer question, so an answer to the older one is dropped
	 * exactly as a stale page already is.
	 *
	 * Otherwise the thing you just made would vanish a moment after it appeared, and only on a
	 * slow answer.
	 */
	#settled(): void {
		this.#generation += 1;
	}

	/** How many this account may see in total, which is what the paginator is drawn from. */
	total = $state(0);
	/** The order the wall is showing, held here so a reload keeps it. */
	sort = $state<string>('name_az');

	/*
	 * One page of the list, in a chosen order, asked of the SERVER: an order applied here could
	 * only rearrange the rows in hand, and a fixed large page would be a silent ceiling past which
	 * a collection does not exist as far as this screen is concerned.
	 */
	/** What the shelf is filtered by. See `People.narrowing` for why it lives on the store. */
	narrowing = $state<Record<string, string[]>>({});

	/**
	 * One page of the wall.
	 *
	 * `prefix` is what the search box above the wall has been typed into, and it is SENT rather
	 * than applied to the rows in hand: this is a page of a longer list, so filtering what has
	 * already arrived would quietly hide everything past the page.
	 */
	/**
	 * Where the server actually started the page it last handed back.
	 *
	 * Sent an anchor rather than an offset, the client does not know the answer until it arrives,
	 * and the pager has to read the real one, or a link would open the right rows under a readout
	 * claiming they are the first. The People store's own field, for the People wall's own reason.
	 */
	at = $state(0);

	/**
	 * The wall's page, through the wall's own paging rather than at a size handed in.
	 *
	 * Through `CardPaging.fill` the second ask trims the rows held or asks for the remainder only,
	 * and an anchored arrival lands its offset without asking again, rather than asking at the
	 * fallback count and again, whole, once a card has been measured. `load` stays for the other
	 * screens that read this list at a size of their own.
	 */
	async fill(
		paging: CardPaging,
		sort: string = this.sort,
		narrowing: Record<string, string[]> = this.narrowing,
		prefix = ''
	): Promise<void> {
		const generation = ++this.#generation;
		this.sort = sort;
		this.narrowing = narrowing;
		await fillHeld(
			this,
			paging,
			JSON.stringify([sort, narrowing, prefix]),
			(ask) =>
				// The filtering first; the paging parameters are this store's and cannot be displaced.
				api.get<components['schemas']['CollectionList']>('/collections', {
					// The box's words match anywhere in a name, as the People wall's do.
					query: { ...narrowing, prefix, anywhere: 'true', sort, ...asked(ask) }
				}),
			(answer) => ({ rows: answer.items, total: answer.total, offset: answer.offset }),
			() => generation === this.#generation
		);
	}

	async load(
		offset = 0,
		limit = 200,
		sort = this.sort,
		narrowing: Record<string, string[]> = this.narrowing,
		prefix = '',
		/* Start at this row instead of at the offset: what the address carries, resolved by the
		 * server against the same scoped, ordered list the page comes out of. Only the server knows
		 * that list; working it out from a page already in hand would give a position in the page. */
		from: string | null = null
	): Promise<void> {
		const generation = ++this.#generation;
		this.loading = true;
		this.failed = false;
		this.sort = sort;
		this.narrowing = narrowing;
		try {
			const page = await api.get<components['schemas']['CollectionList']>('/collections', {
				// The filtering first; the paging parameters are this store's and cannot be displaced.
				// One of `offset` and `from`, never both. See the Tags store for what two questions
				// in one request would cost.
				query: { ...narrowing, limit, sort, prefix, ...(from === null ? { offset } : { from }) }
			});
			if (generation !== this.#generation) return;
			this.items = page.items;
			this.total = page.total;
			this.at = page.offset;
			this.loaded = true;
		} catch {
			if (generation !== this.#generation) return;
			this.failed = true;
		} finally {
			if (generation === this.#generation) this.loading = false;
		}
	}

	/**
	 * One page for a PICKER, alphabetical, filtered by what is being typed.
	 *
	 * Deliberately does NOT touch `items`: a picker is a flyout over a screen that is showing its
	 * own page of the same wall, and letting a keystroke in the flyout rewrite what is behind it
	 * would move the thing somebody was looking at. So this answers the caller and keeps nothing.
	 *
	 * `name_az` rather than the wall's own order: a picker is read by somebody looking for a name
	 * they already have in mind, and an order by size puts the answer somewhere nobody can predict.
	 * What they reach for often comes first anyway (`$lib/search/frequent` puts those in front on the
	 * client), so the page under that wants to be the one order a person can navigate without
	 * reading every row.
	 *
	 * `total` comes back beside the page so the picker can say how many it is NOT showing.
	 */
	async choices(prefix = '', limit = PICK_PAGE): Promise<{ items: Collection[]; total: number }> {
		const page = await api.get<components['schemas']['CollectionList']>('/collections', {
			query: { prefix, limit, offset: 0, sort: 'name_az' }
		});
		// A locked row (a name the vault keeps back) is no choice: it would be a blank line in an
		// "Add to" list, and picking it would act on something whose name cannot be seen.
		const named = page.items.filter((one) => !one.locked);
		return { items: named, total: page.total - (page.items.length - named.length) };
	}

	/* Made, and put on the page in front of whoever made it. The total moves too: the pager reads
	 * it, so a shelf that grew by one while the total stayed put says "No collections" under one. */
	async create(name: string): Promise<Collection> {
		const created = await api.post<Collection>('/collections', { body: { name } });
		this.#settled();
		this.items = [...this.items, created].sort(byName);
		this.total += 1;
		return created;
	}

	async remove(id: string): Promise<void> {
		await api.del<void>(`/collections/${id}`);
		this.#settled();
		const held = this.items.length;
		this.items = this.items.filter((one) => one.id !== id);
		if (this.items.length < held) this.total = Math.max(0, this.total - 1);
	}

	/* The heart and the stars, THIS account's. Settled from the server's own answer rather than
	   re-reading the list. See the tag store, which does the same for the same reasons. */
	async setFavorite(id: string, favorite: boolean): Promise<void> {
		const state = await api.put<components['schemas']['CollectionStateView']>(
			`/collections/${id}/favorite`,
			{ body: { favorite } }
		);
		this.#settle(id, state);
	}

	async setRating(id: string, rating: number | null): Promise<void> {
		const state = await api.put<components['schemas']['CollectionStateView']>(
			`/collections/${id}/rating`,
			{ body: { rating } }
		);
		this.#settle(id, state);
	}

	#settle(id: string, state: components['schemas']['CollectionStateView']): void {
		this.items = this.items.map((one) => (one.id === id ? { ...one, ...state } : one));
	}

	/* Conceal a collection, or reveal it again. Answered with no body, so the list is reloaded,
	 * and the reload is what moves the row, since a concealed collection is absent from the list
	 * rather than marked on it.
	 *
	 * Passing `false` works only while the vault is unlocked. That is not a rule this call knows:
	 * a concealed collection resolves at all only to somebody who has opened the vault, so to
	 * anybody else the request gets the same 404 an unknown id gets. Screens offer the reveal only
	 * where they can already see the collection, which is exactly when it will be accepted.
	 */
	async setVault(id: string, vault: boolean): Promise<void> {
		await api.put<void>(`/collections/${id}/vault`, { body: { vault } });
		await this.load();
	}

	/** Put items in. Moves no file. */
	async add(id: string, assetIds: string[]): Promise<BulkWriteDone> {
		return await overChunks(assetIds, (chunk) => this.#edit(id, chunk, 'add'));
	}

	async removeItems(id: string, assetIds: string[]): Promise<BulkWriteDone> {
		return await overChunks(assetIds, (chunk) => this.#edit(id, chunk, 'remove'));
	}

	/** One file a place in the stored arrangement; nothing else changes, so nothing is reloaded. */
	async move(id: string, assetId: string, by: -1 | 1): Promise<void> {
		await api.post<BulkWriteDone>(`/collections/${id}/items`, {
			body: { asset_ids: [assetId], action: 'move', direction: by < 0 ? 'earlier' : 'later' }
		});
	}

	async #edit(id: string, assetIds: string[], action: string): Promise<BulkWriteDone> {
		/* The WHOLE answer, not `changed` off the front of it: the server says how many it
		   skipped and why (a file left behind by a locked vault), and a store that reads one
		   field throws the other two away before any screen can see them. */
		const done = await api.post<BulkWriteDone>(`/collections/${id}/items`, {
			body: { asset_ids: assetIds, action }
		});
		// A line in each file's history, which the server may tell nobody about (see `recorded`).
		recorded.changed();
		await this.load();
		return done;
	}

	/**
	 * Point it at a file, and optionally at WHICH MOMENT of that file.
	 *
	 * The moment is null for a photograph and for "the file's own picture", which are one
	 * instruction as far as the server is concerned: no moment stored, so the still it already has
	 * is the one served. Sent with the file every time rather than separately: a moment belongs to
	 * the file it was taken from, and the server writes both columns in one statement so one cannot
	 * outlive the other.
	 */
	async setCover(
		id: string,
		assetId: string | null,
		atMs: number | null = null,
		more?: CoverMore
	): Promise<Collection> {
		const updated = await api.put<Collection>(`/collections/${id}/cover`, {
			body: coverBody(assetId, atMs, more)
		});
		this.items = this.items.map((one) => (one.id === id ? updated : one));
		return updated;
	}

	/**
	 * A cover picture from OUTSIDE the library, chosen from the disk.
	 *
	 * Multipart rather than JSON, because the body is a file. The reply is the same row a pick
	 * answers with, so the screen replaces what it is holding either way.
	 */
	async uploadCover(id: string, file: File): Promise<Collection> {
		const form = new FormData();
		form.set('file', file);
		const updated = await api.post<Collection>(`/collections/${id}/cover-picture`, {
			body: form
		});
		this.items = this.items.map((one) => (one.id === id ? updated : one));
		return updated;
	}

	/** What is in one collection, in its arranged order, filtered by the query language (`contentsAsked`). */
	async contents(
		id: string,
		limit = 200,
		offset = 0,
		narrowing: Readonly<Record<string, readonly string[]>> = {}
	): Promise<CollectionContents> {
		return await api.get<CollectionContents>(`/collections/${id}/items`, {
			query: { ...narrowing, limit, offset }
		});
	}

	async one(id: string): Promise<Collection> {
		return await api.get<Collection>(`/collections/${id}`);
	}

	byId(id: string): Collection | undefined {
		return this.items.find((one) => one.id === id);
	}
}

function byName(a: Collection, b: Collection): number {
	return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
}

/*
 * The orders this wall can be put in.
 *
 * The first four are `UNIVERSAL_SORTS`: the same four the file grid offers, taken from its list
 * rather than written out again, so `Newest first` here and on Browse are one string rather than two
 * that happen to match today. The heart and the stars follow, from the list every wall shares.
 *
 * Labels only. Comparisons applied in the browser would work only while the whole list arrived in
 * one answer, and a paged shelf never has the whole list: it asks the server for its order; see
 * the tag wall's note for why the two cannot coexist. These values ARE the server's keys, and it
 * refuses one it does not know rather than quietly ordering some other way.
 *
 * Note what is NOT ordered here: the items INSIDE a collection. Those are arranged by hand, that
 * arrangement is the whole point of a collection, and nothing on this screen touches it. This
 * orders the shelf, never the books.
 */
export const COLLECTION_ORDERS: readonly { value: string; label: string }[] = [
	...UNIVERSAL_SORTS,
	...ENTITY_OPINION_SORTS
];

/** The one instance the screens share. */
export const collections = new Collections();
