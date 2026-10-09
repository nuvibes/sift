/* The tag list, and the writes that change it. */

import { coverBody, type CoverMore } from '$lib/entity/cover-frame';
import { overChunks, type BulkWriteDone } from '$lib/library/bulk';
import { api } from '$lib/api/client';
import { recorded } from '$lib/library/changes.svelte';
import { PICK_PAGE } from '$lib/search/frequent.svelte';
import type { components } from '$lib/api/schema';
import { ENTITY_OPINION_SORTS, UNIVERSAL_SORTS } from '$lib/grid/sort-state.svelte';
import { asked } from '$lib/grid/anchor';
import { fillHeld, type CardPaging } from '$lib/grid/cards.svelte';

/** A tag, taken from the server's own definition rather than described again here. */
export type Tag = components['schemas']['TagView'];

/* How many Tags one page of the wall holds. */
export const TAGS_PER_PAGE = 60;

/** Where a tag sits in a picker: how far in, and the parent it is filed under when that parent is
 *  not drawn above it. */
interface InTree {
	depth: number;
	within: string | null;
}

/** A page of tags with every branch drawn under its parent: a PICKER's order. */
export function underParents<
	T extends { id: string; parent_id?: string | null; parent_name?: string | null }
>(page: readonly T[]): Array<T & InTree> {
	const here = new Set(page.map((one) => one.id));
	const children = new Map<string, T[]>();
	for (const one of page) {
		if (one.parent_id && here.has(one.parent_id) && one.parent_id !== one.id) {
			children.set(one.parent_id, [...(children.get(one.parent_id) ?? []), one]);
		}
	}
	const placed = new Set<string>();
	const out: Array<T & InTree> = [];
	const place = (one: T, depth: number, within: string | null) => {
		if (placed.has(one.id)) return;
		placed.add(one.id);
		out.push({ ...one, depth, within });
		for (const child of children.get(one.id) ?? []) place(child, depth + 1, null);
	};
	for (const one of page) {
		const underOneHere = !!one.parent_id && here.has(one.parent_id) && one.parent_id !== one.id;
		if (!underOneHere) place(one, 0, one.parent_name ?? null);
	}
	/* Anything left is in a loop of tags that are all on this page: drawn at the top as they came. */
	for (const one of page) place(one, 0, one.parent_name ?? null);
	return out;
}

export class Tags {
	items = $state<Tag[]>([]);
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
	sort = $state<string>('largest');

	/* One page of the list, in a chosen order, asked of the SERVER. */
	/** What the wall is filtered by. See `People.narrowing` for why it lives on the store. */
	narrowing = $state<Record<string, string[]>>({});

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
				api.get<components['schemas']['TagList']>('/tags', {
					// The box's words match anywhere in a name, as the People wall's do.
					query: { ...narrowing, prefix, anywhere: 'true', sort, ...asked(ask) }
				}),
			(answer) => ({ rows: answer.items, total: answer.total, offset: answer.offset }),
			() => generation === this.#generation
		);
	}

	async load(
		prefix = '',
		offset = 0,
		limit = 200,
		sort = this.sort,
		narrowing: Record<string, string[]> = this.narrowing,
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
			const page = await api.get<components['schemas']['TagList']>('/tags', {
				// The filtering first; the paging parameters are this store's and cannot be
				// displaced.
				query: { ...narrowing, prefix, limit, sort, ...(from === null ? { offset } : { from }) }
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
	async choices(
		prefix = '',
		limit = PICK_PAGE
	): Promise<{ items: Array<Tag & InTree>; total: number }> {
		const page = await api.get<components['schemas']['TagList']>('/tags', {
			query: { prefix, limit, offset: 0, sort: 'name_az' }
		});
		// A locked row (a name the vault keeps back) is no choice: it would be a blank line in an
		// "Add to" list, and picking it would act on something whose name cannot be seen.
		const named = page.items.filter((one) => !one.locked);
		return { items: underParents(named), total: page.total - (page.items.length - named.length) };
	}

	/* Made, and put on the page in front of whoever made it. */
	async create(name: string): Promise<Tag> {
		const created = await api.post<Tag>('/tags', { body: { name } });
		this.#settled();
		this.items = [...this.items, created].sort(byUseThenName);
		this.total += 1;
		return created;
	}

	/** A new name, drawn on the press and put back if the server refuses it. */
	async rename(id: string, name: string): Promise<Tag> {
		const existing = this.items.find((tag) => tag.id === id);
		const named = (to: string) =>
			(this.items = this.items
				.map((tag) => (tag.id === id ? { ...tag, name: to } : tag))
				.sort(byUseThenName));
		if (existing) named(name);
		let updated: Tag;
		try {
			updated = await api.put<Tag>(`/tags/${id}`, { body: { name } });
		} catch (error) {
			if (existing) named(existing.name);
			throw error;
		}
		// The server does not recount on a rename, so the count this list already holds is kept
		// rather than taking the zero the write replies with.
		const merged = { ...updated, asset_count: existing?.asset_count ?? updated.asset_count };
		this.items = this.items.map((tag) => (tag.id === id ? merged : tag)).sort(byUseThenName);
		return merged;
	}

	/** Rename and write the record, in one call. One request rather than two, because one ROUTE
	 * takes all of it: the name and the record live on the same row and the same statement
	 * replaces them. */
	async update(
		id: string,
		record: {
			name: string;
			description: string;
			category: string;
			aliases: string[];
			/** The tag this one is filed under, by name; empty for the top of the tree. */
			parent: string;
		}
	): Promise<Tag> {
		const updated = await api.put<Tag>(`/tags/${id}`, { body: record });
		// The server does not recount on an edit, so the count this list already holds is kept
		// rather than taking the zero the write replies with.
		const existing = this.items.find((tag) => tag.id === id);
		const merged = { ...updated, asset_count: existing?.asset_count ?? updated.asset_count };
		this.items = this.items.map((tag) => (tag.id === id ? merged : tag)).sort(byUseThenName);
		return merged;
	}

	async remove(id: string): Promise<void> {
		await api.del<void>(`/tags/${id}`);
		this.#settled();
		const held = this.items.length;
		this.items = this.items.filter((tag) => tag.id !== id);
		// Only if it was really on this page. Deleting from a menu over a row the wall is not
		// showing would otherwise take one off the count for a row that was never in it.
		if (this.items.length < held) this.total = Math.max(0, this.total - 1);
	}

	/* The heart and the stars, which are THIS account's and nobody else's. */
	async setFavorite(id: string, favorite: boolean): Promise<void> {
		const state = await api.put<components['schemas']['TagStateView']>(`/tags/${id}/favorite`, {
			body: { favorite }
		});
		this.#settle(id, state);
	}

	async setRating(id: string, rating: number | null): Promise<void> {
		const state = await api.put<components['schemas']['TagStateView']>(`/tags/${id}/rating`, {
			body: { rating }
		});
		this.#settle(id, state);
	}

	/** Point a tag at the still it is drawn as, or clear it. */
	/** Point it at a file, and optionally at WHICH MOMENT of that file. */
	async setCover(
		id: string,
		assetId: string | null,
		atMs: number | null = null,
		more?: CoverMore
	): Promise<Tag> {
		const tag = await api.put<Tag>(`/tags/${id}/cover`, {
			body: coverBody(assetId, atMs, more)
		});
		this.items = this.items.map((held) => (held.id === id ? tag : held));
		return tag;
	}

	/** A cover picture from OUTSIDE the library, chosen from the disk. */
	async uploadCover(id: string, file: File): Promise<Tag> {
		const form = new FormData();
		form.set('file', file);
		const tag = await api.post<Tag>(`/tags/${id}/cover-picture`, { body: form });
		this.items = this.items.map((held) => (held.id === id ? tag : held));
		return tag;
	}

	#settle(id: string, state: components['schemas']['TagStateView']): void {
		this.items = this.items.map((tag) => (tag.id === id ? { ...tag, ...state } : tag));
	}

	/** Attach or detach, for one asset or a whole selection. Moves no file. */
	async assign(assetIds: string[], tagIds: string[], add = true): Promise<BulkWriteDone> {
		// The whole answer. See `Collections.#edit` for why the count alone is not enough.
		const done = await overChunks(assetIds, (chunk) =>
			api.post<BulkWriteDone>('/assets/tags', {
				body: { asset_ids: chunk, tag_ids: tagIds, add }
			})
		);
		// A line in each file's history, which the server may tell nobody about (see `recorded`).
		recorded.changed();
		// The counts follow; the press answers on the write.
		void this.load();
		return done;
	}

	/** One tag, from the server, by id. See `People.one` for why a detail page reads this rather
	 * than picking its subject out of the wall's page. */
	async one(id: string): Promise<Tag> {
		return api.get<Tag>(`/tags/${id}`);
	}

	byId(id: string): Tag | undefined {
		return this.items.find((tag) => tag.id === id);
	}

	/** Names beginning with what has been typed, for the autocomplete. */
	matching(prefix: string): Tag[] {
		const needle = prefix.trim().toLowerCase();
		if (!needle) return this.items;
		return this.items.filter((tag) => tag.name.toLowerCase().startsWith(needle));
	}
}

function byUseThenName(a: Tag, b: Tag): number {
	if (a.asset_count !== b.asset_count) return b.asset_count - a.asset_count;
	return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
}

/* The orders this wall can be put in. */
export const TAG_ORDERS: readonly { value: string; label: string }[] = [
	...UNIVERSAL_SORTS,
	...ENTITY_OPINION_SORTS
];

/** The one instance the screens share. */
export const tags = new Tags();
