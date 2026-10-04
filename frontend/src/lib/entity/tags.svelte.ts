/* The tag list, and the writes that change it.
 *
 * One place, because four screens read the same list (the tag screen, the chips on a tile, the
 * detail view and the autocomplete), and a second copy would let one of them show a tag another
 * had just deleted.
 *
 * Counts are the server's and are scoped to whoever is asking, so they are never recomputed here.
 * A count worked out in the client would be a count over the rows this session happens to have
 * fetched, which for anybody but an admin is not the same number.
 */

import { coverBody, type CoverMore } from '$lib/entity/cover-frame';
import { overChunks, type BulkWriteDone } from '$lib/library/bulk';
import { api } from '$lib/api/client';
import { recorded } from '$lib/library/changes.svelte';
import { PICK_PAGE } from '$lib/search/frequent.svelte';
import type { components } from '$lib/api/schema';
import { ENTITY_OPINION_SORTS, UNIVERSAL_SORTS } from '$lib/grid/sort-state.svelte';
import { asked } from '$lib/grid/anchor';
import { fillHeld, type CardPaging } from '$lib/grid/cards.svelte';

/**
 * A tag, taken from the server's own definition rather than described again here.
 *
 * A copy written out by hand costs two things even when accurate: a comment explaining why a colour
 * is stored would be a copy of the server's, free to rot without either half moving, and fields
 * marked as possibly absent where the server always sends them leave every screen handling an
 * `undefined` that never arrives.
 */
export type Tag = components['schemas']['TagView'];

/* How many Tags one page of the wall holds.
 *
 * The same figure the People wall uses, and for the same reason: each row costs the server a scoped
 * count and a visibility question, so the fallback before anything has been measured wants to be a
 * plausible screenful rather than the whole table. `CardPaging` replaces it with whole rows of the
 * actual window the moment the wall has been laid out once.
 */
export const TAGS_PER_PAGE = 60;

/** Where a tag sits in a picker: how far in, and the parent it is filed under when that parent is
 *  not drawn above it. */
interface InTree {
	depth: number;
	within: string | null;
}

/**
 * A page of tags with every branch drawn under its parent: a PICKER's order.
 *
 * The page arrives alphabetical. A tag whose parent is on the same page is moved to just under it,
 * one step in, and so on down; a tag whose parent is not on the page keeps its place and says the
 * parent's name instead (`within`), so a tag found by typing still says where it is filed. A
 * parent loop, which the server refuses, cannot hang this: a tag is placed once.
 */
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
	 * It presents as the thing you just made vanishing a moment after it appeared, and only on a
	 * slow answer, which is why it shows up in a loaded suite and never alone.
	 */
	#settled(): void {
		this.#generation += 1;
	}

	/** How many this account may see in total, which is what the paginator is drawn from. */
	total = $state(0);
	/** The order the wall is showing, held here so a reload keeps it. */
	sort = $state<string>('largest');

	/*
	 * One page of the list, in a chosen order, asked of the SERVER.
	 *
	 * Fetching a fixed two hundred and ordering them in the browser would be wrong twice over in
	 * the same way. Two hundred would be a silent ceiling: tag two hundred and one would not
	 * exist as far as this screen was concerned, with nothing on the page saying so. And an order
	 * applied here can only rearrange the rows in hand, so the moment there is a second page the
	 * client would be sorting fifty rows and calling it the order of a thousand.
	 */
	/** What the wall is filtered by. See `People.narrowing` for why it lives on the store. */
	narrowing = $state<Record<string, string[]>>({});

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
	 * A size handed in would ask at the fallback count on a first visit and again, whole, once a
	 * card has been measured. Through `CardPaging.fill` the second ask trims the rows held or asks
	 * for the remainder only, and an anchored arrival lands its offset without asking again. `load`
	 * stays for the other screens that read this list at a size of their own.
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
			const page = await api.get<components['schemas']['TagList']>('/tags', {
				// The filtering first; the paging parameters are this store's and cannot be displaced.
				// One of `offset` and `from`, never both: a request carrying an anchor AND an offset
				// asks two questions, and which one wins becomes a detail of the server.
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

	/**
	 * One page for a PICKER, alphabetical, filtered by what is being typed.
	 *
	 * See `Collections.choices`, which is the same method for the same reason: it answers the caller
	 * and keeps nothing, so a keystroke in a flyout cannot move the wall behind it, and it asks for
	 * `name_az` rather than the wall's own order because a picker is read by somebody who already
	 * has a name in mind. `total` comes back so the picker can say how many it is NOT showing.
	 */
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

	/* Made, and put on the page in front of whoever made it.
	 *
	 * The total is moved too, and that is not bookkeeping for its own sake: the pager is drawn from
	 * it, so a wall that grew by one while the total stayed put says "No tags" underneath a tag. */
	async create(name: string): Promise<Tag> {
		const created = await api.post<Tag>('/tags', { body: { name } });
		this.#settled();
		this.items = [...this.items, created].sort(byUseThenName);
		this.total += 1;
		return created;
	}

	/**
	 * A new name.
	 *
	 * Only the name is sent. A tag has no colour, so there is nothing to carry alongside it and
	 * nothing to read first.
	 */
	async rename(id: string, name: string): Promise<Tag> {
		const updated = await api.put<Tag>(`/tags/${id}`, { body: { name } });
		// The server does not recount on a rename, so the count this list already holds is kept
		// rather than taking the zero the write replies with.
		const existing = this.items.find((tag) => tag.id === id);
		const merged = { ...updated, asset_count: existing?.asset_count ?? updated.asset_count };
		this.items = this.items.map((tag) => (tag.id === id ? merged : tag)).sort(byUseThenName);
		return merged;
	}

	/**
	 * Rename and write the record, in one call.
	 *
	 * One request rather than two, because one ROUTE takes all of it: the name and the record live
	 * on the same row and the same statement replaces them. Two calls would be two writes and two
	 * chances for the second to fail after the first had landed.
	 *
	 * Every field is sent every time, and that is what the record form means: it draws the whole
	 * record and saves it in one press, so a field it does not send is a field somebody emptied.
	 */
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
		// Only if it was really on this page. Deleting from a menu over a row the wall is not showing
		// would otherwise take one off the count for a row that was never in it.
		if (this.items.length < held) this.total = Math.max(0, this.total - 1);
	}

	/*
	 * The heart and the stars, which are THIS account's and nobody else's.
	 *
	 * Written straight back into the row rather than re-reading the list: the server answers with
	 * what it ended up holding, so the card settles on the truth without a second request, and a
	 * whole re-read would throw away a wall somebody is looking at to change one glyph.
	 *
	 * Both are offered to a guest. An opinion changes where a row appears on a wall and never
	 * whether it appears, so there is nothing here for a permission to protect. Restricting is
	 * what keeps something from another account.
	 */
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

	/** Point a tag at the still it is drawn as, or clear it. Answers the whole tag back, scoped, so
	 *  a cover the asker may not open comes back as none rather than as an id they cannot use. */
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
	): Promise<Tag> {
		const tag = await api.put<Tag>(`/tags/${id}/cover`, {
			body: coverBody(assetId, atMs, more)
		});
		this.items = this.items.map((held) => (held.id === id ? tag : held));
		return tag;
	}

	/**
	 * A cover picture from OUTSIDE the library, chosen from the disk.
	 *
	 * Multipart rather than JSON, because the body is a file. The reply is the same row a pick
	 * answers with, so the screen replaces what it is holding either way.
	 */
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
		await this.load();
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

	/** Names beginning with what has been typed, for the autocomplete. Filtered here rather than
	 * refetched: the whole list is already loaded and a request per keystroke buys nothing. */
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

/*
 * The orders this wall can be put in.
 *
 * ## They are the application's, not this wall's
 *
 * `UNIVERSAL_SORTS` is the same four the file grid offers, taken from its own list rather than
 * written out again, so `Newest first` on Browse and `Newest first` here are literally one string.
 * Somebody who has learned an order on one screen has learned it on all of them, which cannot be
 * true if each wall invents its own key and its own wording for the same idea. The heart and the
 * stars follow, from the list every wall of entities shares.
 *
 * ## Labels only. No comparisons, and their absence is the point
 *
 * A comparison applied in the browser works only while the whole list arrives in one answer. The
 * wall pages, and paging takes client-side ordering with it: a comparison applied here would sort
 * the rows in hand and call it the order of the library, so page two would open on names belonging
 * on page one.
 *
 * These values are the SERVER's own keys. It refuses one it does not recognise rather than quietly
 * ordering some other way, which is what makes a list of labels safe to keep apart from the ordering
 * itself: the failure mode that argument was written against cannot be silent here.
 */
export const TAG_ORDERS: readonly { value: string; label: string }[] = [
	...UNIVERSAL_SORTS,
	...ENTITY_OPINION_SORTS
];

/** The one instance the screens share. */
export const tags = new Tags();
