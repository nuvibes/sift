/* The songs a file's verbs and the merge sheet ask about, and the writes they make. */

import { recorded } from '$lib/library/changes.svelte';
import { overChunks, type BulkWriteDone } from '$lib/library/bulk';
import { api } from '$lib/api/client';
import { PICK_PAGE } from '$lib/search/frequent.svelte';
import type { components } from '$lib/api/schema';

/** A song, taken from the server's own definition rather than described again here. */
export type Song = components['schemas']['SongSummary'];

class Songs {
	/** One page for a PICKER, alphabetical, filtered by what is being typed anywhere in a name. */
	async choices(prefix = '', limit = PICK_PAGE): Promise<{ items: Song[]; total: number }> {
		const page = await api.get<components['schemas']['SongList']>('/songs', {
			query: { prefix, anywhere: 'true', limit, offset: 0, sort: 'name_az' }
		});
		const named = page.items.filter((one) => !one.locked);
		return { items: named, total: page.total - (page.items.length - named.length) };
	}

	/** One song by id, as the person asking may know it. */
	async one(id: string): Promise<Song> {
		return api.get<Song>(`/songs/${encodeURIComponent(id)}`);
	}

	/** Several by id, for the merge sheet, which reads every pick from the server itself. */
	async several(ids: string[]): Promise<Song[]> {
		return Promise.all(ids.map((id) => this.one(id)));
	}

	/** A song by name. A name a song already carries answers that song rather than a second one. */
	async create(name: string): Promise<Song> {
		return api.post<Song>('/songs', { body: { name } });
	}

	/** Put files on one song. Returns how many the server actually changed: a file already on it
	 *  is not written twice, and a file on another song moves to this one. */
	async add(id: string, assetIds: string[]): Promise<BulkWriteDone> {
		const done = await overChunks(assetIds, (chunk) =>
			api.post<BulkWriteDone>(`/songs/${encodeURIComponent(id)}/files`, {
				body: { asset_ids: chunk }
			})
		);
		// A line in each file's history, which the server may tell nobody about (see `recorded`).
		recorded.changed();
		return done;
	}

	/** Take files off one song. The same route as `add`, told to remove: the file's own record
	 *  uses it for the chip's cross, as the song's page does for its rows. */
	async remove(id: string, assetIds: string[]): Promise<BulkWriteDone> {
		const done = await overChunks(assetIds, (chunk) =>
			api.post<BulkWriteDone>(`/songs/${encodeURIComponent(id)}/files`, {
				query: { remove: true },
				body: { asset_ids: chunk }
			})
		);
		recorded.changed();
		return done;
	}
}

export const songs = new Songs();
