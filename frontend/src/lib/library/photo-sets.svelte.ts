/* The photo-set list, and the two writes a file's verbs make against it.
 *
 * Narrower than the collection store beside it, and deliberately: the Photo Sets wall reads its own
 * page with its own paging, sorting and filters, and nothing here is trying to replace that. What
 * this is for is the question the FILE side asks (what sets are there, make one, put these
 * pictures in it), which is asked from the verbs and from nowhere else.
 *
 * Counts are not held at all, for the same reason the collection store does not compute them: a
 * number worked out from the rows this session happens to have fetched is not the number the
 * server would give, and getting that wrong is how a screen tells somebody how much they are not
 * being shown.
 */

import { recorded } from '$lib/library/changes.svelte';
import { overChunks, type BulkWriteDone } from '$lib/library/bulk';
import { api } from '$lib/api/client';
import { PICK_PAGE } from '$lib/search/frequent.svelte';
import type { components } from '$lib/api/schema';

/** A set, taken from the server's own definition rather than described again here. */
export type PhotoSet = components['schemas']['PhotoSetSummary'];

/* How many to ask for when the verbs need a list to choose from.
 *
 * The pick sheet filters what is already loaded rather than re-asking as somebody types, so this is
 * the ceiling on what can be chosen from it. The same number the collection store uses, for the
 * same reason: it is comfortably past any hand-made list and short of a page nobody could read.
 */
const ENOUGH_TO_CHOOSE_FROM = 200;

function byName(one: PhotoSet, other: PhotoSet): number {
	return one.name.localeCompare(other.name);
}

/* Not exported. Nothing outside this file names the shape: the one instance below is the whole
   public surface, and a class on the way out is a claim that somebody else may build a second. */
class PhotoSets {
	items = $state<PhotoSet[]>([]);
	loading = $state(false);
	failed = $state(false);
	loaded = $state(false);

	/* Rising counter so a slow answer cannot overwrite a newer one, and so a write that edits the
	   list in place discards a page that was already in the air. Every list store in this client
	   carries one. */
	#generation = 0;

	/** Empty the cache, so the next screen that wants it asks again. For the vault: what is
	 *  concealed never arrives, so a list held from while it was open still holds it afterwards. */
	forget(): void {
		this.items = [];
		this.loaded = false;
		this.#generation += 1;
	}

	async load(): Promise<void> {
		const generation = ++this.#generation;
		this.loading = true;
		this.failed = false;
		try {
			const page = await api.get<components['schemas']['PhotoSetList']>('/photo-sets', {
				query: { limit: ENOUGH_TO_CHOOSE_FROM, offset: 0 }
			});
			if (generation !== this.#generation) return;
			this.items = [...page.items].sort(byName);
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
	async choices(prefix = '', limit = PICK_PAGE): Promise<{ items: PhotoSet[]; total: number }> {
		const page = await api.get<components['schemas']['PhotoSetList']>('/photo-sets', {
			query: { prefix, limit, offset: 0, sort: 'name_az' }
		});
		// A locked row (a name the vault keeps back) is no choice: it would be a blank line in an
		// "Add to" list, and picking it would act on something whose name cannot be seen.
		const named = page.items.filter((one) => !one.locked);
		return { items: named, total: page.total - (page.items.length - named.length) };
	}

	async create(name: string): Promise<PhotoSet> {
		const created = await api.post<PhotoSet>('/photo-sets', { body: { name } });
		this.#generation += 1;
		this.items = [...this.items, created].sort(byName);
		return created;
	}

	/** Put pictures into one set. Returns how many rows the server actually wrote, which is not
	 *  how many were asked for: a picture already in the set is not written twice. */
	async add(id: string, assetIds: string[]): Promise<BulkWriteDone> {
		const done = await overChunks(assetIds, (chunk) =>
			api.post<BulkWriteDone>(`/photo-sets/${encodeURIComponent(id)}/items`, {
				body: { asset_ids: chunk }
			})
		);
		// A line in each file's history, which the server may tell nobody about (see `recorded`).
		recorded.changed();
		return done;
	}

	/** Take pictures out of one set. The same route as `add`, told to remove: the file's own
	 *  screen uses it for the chip's cross, as the set's page does for its rows. */
	async remove(id: string, assetIds: string[]): Promise<BulkWriteDone> {
		const done = await overChunks(assetIds, (chunk) =>
			api.post<BulkWriteDone>(`/photo-sets/${encodeURIComponent(id)}/items`, {
				query: { remove: true },
				body: { asset_ids: chunk }
			})
		);
		recorded.changed();
		return done;
	}
}

export const photoSets = new PhotoSets();
