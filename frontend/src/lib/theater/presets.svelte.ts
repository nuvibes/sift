/* The presets somebody saved: a wall's layout and what each cell was set to, under a name.
 *
 * The client half of saved presets. The server's route and its wire types spell them
 * `arrangements`; `Preset` is that shape under the name a person reads. It holds the list and the
 * four things done to it, each of which calls the server and then reconciles the local copy, so the
 * menu showing this never has to guess what the server ended up holding.
 *
 * A module singleton, like the other small stores: there is one account signed in, so there is one
 * list, and a second copy would be a second answer to "what have I saved". It loads lazily.
 *
 * Saving under a name already used is REFUSED here rather than replacing, unlike a saved search. A
 * search is one line of text and overwriting it costs a retype; a wall is four sources, four sets of
 * behaviour and a shape, and overwriting one because the name happened to match is a loss somebody
 * only notices later. Saving over a wall is done by updating the one that was meant.
 */

import { api, ApiError } from '$lib/api/client';
import { mine } from '$lib/library/changes.svelte';
import type { SavedCell } from './cell.svelte';
import type { StoredShape } from './layouts';
import type { components } from '$lib/api/schema';

export type Preset = components['schemas']['ArrangementOut'];

/** A name this account already uses. Its own error, because the sentence is what somebody acts on. */
export class NameTaken extends Error {}

export type { SavedCell };

const ROOT = '/theater/arrangements';

async function refusing<T>(work: () => Promise<T>): Promise<T> {
	try {
		return await work();
	} catch (failure) {
		// A conflict is an ordinary answer (pick another name) rather than a fault, and the
		// server's own sentence names the preset, which the flat one-liner does not. This is one of
		// the few endpoints whose refusals are written for the person reading them.
		if (failure instanceof ApiError && failure.status === 409) {
			throw new NameTaken(failure.detail ?? failure.message);
		}
		throw failure;
	}
}

class Presets {
	items = $state<Preset[]>([]);
	loaded = $state(false);
	busy = $state(false);

	/** Fetch the list once. Safe to call on every menu open; it only asks the first time. */
	async ensure(): Promise<void> {
		if (this.loaded || this.busy) return;
		await this.reload();
	}

	async reload(): Promise<void> {
		this.busy = true;
		try {
			const answer = await api.get<components['schemas']['Arrangements']>(ROOT);
			this.items = answer.items;
			this.loaded = true;
		} finally {
			this.busy = false;
		}
	}

	async save(
		name: string,
		wall: { layout: string; shape: StoredShape; cells: SavedCell[] }
	): Promise<Preset> {
		const made = await refusing(() => api.post<Preset>(ROOT, { body: { name, ...wall } }));
		await this.reload();
		return made;
	}

	/** Change a wall that is already saved: its name, its shape, its cells. */
	async update(
		id: string,
		name: string,
		wall: { layout: string; shape: StoredShape; cells: SavedCell[] }
	): Promise<Preset> {
		const changed = await refusing(() =>
			api.patch<Preset>(`${ROOT}/${id}`, { body: { name, ...wall } })
		);
		await this.reload();
		return changed;
	}

	/**
	 * Change a wall's NAME and nothing else.
	 *
	 * The route takes a whole wall, so this sends the one it already has back with a different name.
	 * Written here rather than at the call site because "send everything back unchanged except one
	 * field" is exactly the shape that quietly loses the other fields when somebody adds one: the
	 * kept wall is the source, so a field added later travels without this function being touched.
	 */
	async rename(kept: Preset, name: string): Promise<Preset> {
		return this.update(kept.id, name, {
			layout: kept.layout,
			shape: kept.shape as never,
			cells: kept.cells
		});
	}

	/**
	 * One saved wall by its id, for an address that names it (`/theater?wall=<id>`, which Insights'
	 * list of walls links to). Null where this account has no wall of that id: deleted since, or
	 * somebody else's.
	 */
	async byId(id: string): Promise<Preset | null> {
		await this.ensure();
		return this.items.find((item) => item.id === id) ?? null;
	}

	async remove(id: string): Promise<void> {
		// Optimistic: it is gone from the list at once, and the server is told. A failed delete is
		// reconciled by the next load rather than left mid-animation.
		this.items = this.items.filter((item) => item.id !== id);
		await api.del(`${ROOT}/${id}`);
	}
}

export const presets = new Presets();

/* A Theater wall saved, renamed or removed in another window is this account's own list moving,
   said on the `mine` bell. The list is the session's, so one lasting listener; a session that
   never opened it asks nothing. */
mine.subscribe(() => {
	if (presets.loaded) void presets.reload();
});
