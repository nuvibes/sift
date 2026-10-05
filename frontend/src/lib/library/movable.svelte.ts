/* Where files can be moved to.
 *
 * Moving is offered only where it could succeed. Most libraries are indexed read-only (Sift is
 * pointed at somebody's existing folders and told not to touch them), and on those installs a
 * Move that is always refused is an invitation to a dead end, repeated on every file. So the
 * destinations are the folders inside a root that was handed over read-write, and a library with
 * none of those does not offer the verb at all.
 *
 * A module singleton, loaded once and reused: the answer is a property of the library rather than
 * of a screen, and every grid asking for itself would be the same request several times over.
 * Nothing here is a control. The server refuses a move into a folder it was not given, with a
 * sentence saying so, whatever this list happens to hold.
 */

import { api, ApiError } from '$lib/api/client';
import { libraryChanges } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';
import type { FoldersRead } from '$lib/library/destinations.svelte';

type MovableFolder = Pick<components['schemas']['FolderView'], 'id' | 'name'> & { path: string };

type FolderRow = Pick<
	components['schemas']['FolderView'],
	'id' | 'root_id' | 'parent_id' | 'name' | 'rel_path'
>;

type RootRow = Pick<components['schemas']['RootView'], 'id' | 'name'>;

class Movable {
	folders = $state<MovableFolder[]>([]);
	loaded = $state(false);
	#read = $state<FoldersRead>('unread');
	#pending: Promise<void> | null = null;

	/** An empty list means no folder only once read: a failed read is not "no folder". */
	get foldersRead(): FoldersRead {
		return this.#read;
	}

	/** Whether there is anywhere to move a file to at all. */
	get possible(): boolean {
		return this.folders.length > 0;
	}

	/**
	 * Ask once; a second ask waits for the read under way. The root list is an admin's, so a
	 * refusal is a list read with nothing in it, and any other failure is a failed read.
	 */
	ensure(): Promise<void> {
		if (this.loaded) return Promise.resolve();
		this.#pending ??= this.#ask().finally(() => (this.#pending = null));
		return this.#pending;
	}

	async #ask(): Promise<void> {
		try {
			const [roots, folders] = await Promise.all([
				api.get<components['schemas']['RootsView']>('/library/roots'),
				api.get<components['schemas']['FoldersView']>('/library/folders', {
					query: { writable: true }
				})
			]);
			const named = new Map(roots.roots.map((root) => [root.id, root.name]));
			/* Only folders the disk lets Sift write in; the move is still refused in a sentence
			   if that changes before it is made. */
			this.folders = folders.folders
				.filter((folder) => folder.writable !== false)
				.map((folder) => ({
					id: folder.id,
					name: folder.name,
					// The root's name in front of the path within it, because a path on its own says
					// nothing about which of somebody's libraries it is in.
					path: [named.get(folder.root_id), folder.rel_path].filter(Boolean).join('/')
				}))
				.sort((one, other) => one.path.localeCompare(other.path));
			this.loaded = true;
			this.#read = 'read';
		} catch (error) {
			this.folders = [];
			const refused = error instanceof ApiError && (error.status === 401 || error.status === 403);
			this.#read = refused ? 'read' : 'failed';
		}
	}

	/** Forget what was read, so a folder added or handed over is picked up on the next ask. */
	forget(): void {
		this.loaded = false;
		this.folders = [];
		this.#read = 'unread';
	}
}

export const movable = new Movable();

/* A folder added, removed or handed over is said on the library bell. The list is the session's,
   so one lasting listener: a list already read is read again, one never read asks nothing. */
libraryChanges.subscribe(() => {
	if (!movable.loaded) return;
	// Not `forget`: the list on screen stays until the new one lands, rather than flashing empty.
	movable.loaded = false;
	void movable.ensure();
});
