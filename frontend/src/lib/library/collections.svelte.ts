/* The collection list, and the writes that change it. */

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

/** A collection, taken from the server's own definition rather than described again here: a
 * hand-written copy drifts (`cover_asset_id` declared always present where the server sends it
 * only when there is one). */
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

/** A collection's files as a wall of files pages them, in every order Browse offers; the route
 *  takes no `from` or `after`. */
export function contentsSource(id: string): RowSource {
	return { path: `/collections/${id}/items`, anchored: false, sorts: null, filterable: true };
}

/* How many Collections one page of the wall holds. */
export const COLLECTIONS_PER_PAGE = 60;

export class Collections {
	items = $state<Collection[]>([]);
	loading = $state(false);
	failed = $state(false);
	loaded = $state(false);

	/* Empty this cache, so the next screen that wants it fetches again. */
	forget(): void {
		this.items = [];
		this.total = 0;
		this.loaded = false;
	}

	/** Rising counter so a slow response cannot overwrite a newer one. */
	#generation = 0;

	/* Any page still in the air is not the answer any more. */
	#settled(): void {
		this.#generation += 1;
	}

	/** How many this account may see in total, which is what the paginator is drawn from. */
	total = $state(0);
	/** The order the wall is showing, held here so a reload keeps it. */
	sort = $state<string>('name_az');

	/* One page of the list, in a chosen order, asked of the SERVER: an order applied here could
	 * only rearrange the rows in hand, and a fixed large page would be a silent ceiling past
	 * which a collection does not exist as far as this screen is concerned. */
	/** What the shelf is filtered by. See `People.narrowing` for why it lives on the store. */
	narrowing = $state<Record<string, string[]>>({});

	/** One page of the wall. */
	/** Where the server actually started the page it last handed back. */
	at = $state(0);

	/** The wall's page, through the wall's own paging rather than at a size handed in. */
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
		 * server against the same scoped, ordered list the page comes out of. */
		from: string | null = null
	): Promise<void> {
		const generation = ++this.#generation;
		this.loading = true;
		this.failed = false;
		this.sort = sort;
		this.narrowing = narrowing;
		try {
			const page = await api.get<components['schemas']['CollectionList']>('/collections', {
				// The filtering first; the paging parameters are this store's and cannot be
				// displaced.
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

	/** One page for a PICKER, alphabetical, filtered by what is being typed. */
	async choices(prefix = '', limit = PICK_PAGE): Promise<{ items: Collection[]; total: number }> {
		const page = await api.get<components['schemas']['CollectionList']>('/collections', {
			query: { prefix, limit, offset: 0, sort: 'name_az' }
		});
		// A locked row (a name the vault keeps back) is no choice: it would be a blank line in an
		// "Add to" list, and picking it would act on something whose name cannot be seen.
		const named = page.items.filter((one) => !one.locked);
		return { items: named, total: page.total - (page.items.length - named.length) };
	}

	/* Made, and put on the page in front of whoever made it. */
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
	   re-reading the list. */
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

	/* Conceal a collection, or reveal it again. */
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

	/** Point it at a file, and optionally at WHICH MOMENT of that file. */
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

	/** A cover picture from OUTSIDE the library, chosen from the disk. */
	async uploadCover(id: string, file: File): Promise<Collection> {
		const form = new FormData();
		form.set('file', file);
		const updated = await api.post<Collection>(`/collections/${id}/cover-picture`, {
			body: form
		});
		this.items = this.items.map((one) => (one.id === id ? updated : one));
		return updated;
	}

	/** What is in one collection, filtered by the query language (`contentsAsked`). */
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

/* The orders this wall can be put in. */
export const COLLECTION_ORDERS: readonly { value: string; label: string }[] = [
	...UNIVERSAL_SORTS,
	...ENTITY_OPINION_SORTS
];

/** The one instance the screens share. */
export const collections = new Collections();
