/* The Saved Layouts: a wall's shape and what each cell was set to, under a name. The server
 * calls them `arrangements`. A name already used is refused rather than overwritten, unlike a saved
 * search, because a wall is far more to lose than one line of text. */

import { api, ApiError } from '$lib/api/client';
import { mine } from '$lib/library/changes.svelte';
import type { SavedCell } from './cell.svelte';
import type { components } from '$lib/api/schema';

export type Preset = components['schemas']['ArrangementOut'];

/** A name this account already uses. Its own error, because the sentence is what somebody acts on. */
export class NameTaken extends Error {}

export type { SavedCell };

/** A wall as it is kept: the places first, then `strip` previews. */
type WallBody = Omit<components['schemas']['ArrangementBody'], 'name'>;

const ROOT = '/theater/arrangements';

async function refusing<T>(work: () => Promise<T>): Promise<T> {
	try {
		return await work();
	} catch (failure) {
		// A conflict is an ordinary answer, and the server's sentence names the Saved Layout.
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
	/** The save dialog's question, or null while it is shut; `over` is the one being updated. */
	asking = $state<{ over: Preset | null } | null>(null);

	/** Open the save dialog, which the screen holds because the panel shuts under it. */
	ask(over: Preset | null = null): void {
		this.asking = { over };
	}

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

	async save(name: string, wall: WallBody): Promise<Preset> {
		const made = await refusing(() => api.post<Preset>(ROOT, { body: { name, ...wall } }));
		await this.reload();
		return made;
	}

	/** Change a wall that is already saved: its name, its shape, its cells. */
	async update(id: string, name: string, wall: WallBody): Promise<Preset> {
		const changed = await refusing(() =>
			api.patch<Preset>(`${ROOT}/${id}`, { body: { name, ...wall } })
		);
		await this.reload();
		return changed;
	}

	/** Change only the name: the route takes a whole wall, so the kept one goes back, strip and all. */
	async rename(kept: Preset, name: string): Promise<Preset> {
		return this.update(kept.id, name, {
			layout: kept.layout,
			shape: kept.shape as never,
			strip: kept.strip,
			cells: kept.cells
		});
	}

	/** One saved wall by the id an address names (`/theater?wall=<id>`), or null. */
	async byId(id: string): Promise<Preset | null> {
		await this.ensure();
		return this.items.find((item) => item.id === id) ?? null;
	}

	async remove(id: string): Promise<void> {
		// Gone at once; a failed delete is put right by the next load.
		this.items = this.items.filter((item) => item.id !== id);
		await api.del(`${ROOT}/${id}`);
	}
}

export const presets = new Presets();

/* Another window changed this account's list. */
mine.subscribe(() => {
	if (presets.loaded) void presets.reload();
});
