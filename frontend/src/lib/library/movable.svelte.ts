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

import { api } from '$lib/api/client';
import { libraryChanges } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';

type MovableFolder = Pick<components['schemas']['FolderView'], 'id' | 'name'> & { path: string };

type FolderRow = Pick<
	components['schemas']['FolderView'],
	'id' | 'root_id' | 'parent_id' | 'name' | 'rel_path'
>;

type RootRow = Pick<components['schemas']['RootView'], 'id' | 'name'>;

class Movable {
	folders = $state<MovableFolder[]>([]);
	loaded = $state(false);
	#loading = false;

	/** Whether there is anywhere to move a file to at all. */
	get possible(): boolean {
		return this.folders.length > 0;
	}

	/**
	 * Ask once.
	 *
	 * Both requests are an admin's (the root list is a description of the server's disk), and so
	 * is moving, so a guest never gets here. A refusal leaves the list empty, which reads as "no
	 * folder can take this", which is the safe way to be wrong.
	 */
	async ensure(): Promise<void> {
		if (this.loaded || this.#loading) return;
		this.#loading = true;
		try {
			const [roots, folders] = await Promise.all([
				api.get<components['schemas']['RootsView']>('/library/roots'),
				api.get<components['schemas']['FoldersView']>('/library/folders')
			]);
			const named = new Map(roots.roots.map((root) => [root.id, root.name]));
			/* Every folder in every library. Whether the filesystem lets a file land in one is
			   asked when the move is made, and refused in a sentence then; there is no flag to
			   filter the list by. */
			this.folders = folders.folders
				.map((folder) => ({
					id: folder.id,
					name: folder.name,
					// The root's name in front of the path within it, because a path on its own says
					// nothing about which of somebody's libraries it is in.
					path: [named.get(folder.root_id), folder.rel_path].filter(Boolean).join('/')
				}))
				.sort((one, other) => one.path.localeCompare(other.path));
			this.loaded = true;
		} catch {
			this.folders = [];
		} finally {
			this.#loading = false;
		}
	}

	/** Forget what was read, so a folder added or handed over is picked up on the next ask. */
	forget(): void {
		this.loaded = false;
		this.folders = [];
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
