/* What the Library screen knows, and how it asks the server for it.
 *
 * Kept out of the component because the interesting parts are decisions rather than markup: what a
 * failure does to the screen, and what happens between asking for a move and being told it worked.
 */

import { UNREACHABLE } from '$lib/shell/unreachable';
import { api, ApiError, request } from '$lib/api/client';
import { toasts } from '$lib/shell/toasts.svelte';
import { thing, type ToastPiece } from '$lib/components/common/toast-pieces';
import { heldFolder } from '$lib/shell/toasts-benchmark.svelte';
import { session } from '$lib/shell/session.svelte';
import { libraryChanges } from './changes.svelte';
import { buildTree, type Folder, type FolderNode } from './tree';
import type { components } from '$lib/api/schema';

export type Root = components['schemas']['RootView'];

export type FolderDetail = components['schemas']['FolderDetail'];

export class Library {
	roots = $state<Root[]>([]);
	folders = $state<Folder[]>([]);
	/**
	 * Whether the server answered when this asked for the roots.
	 *
	 * Not `roots.length > 0`, which is the same thing only until somebody has no folders yet, and
	 * then the one screen that could add the first one refuses to draw itself, forever. An empty
	 * library is the state every install starts in.
	 */
	canManage = $state(false);
	loading = $state(true);
	/** Set when the whole screen could not load. A screen with no data and no explanation is worse. */
	failed = $state<string | null>(null);
	busy = $state(false);

	tree = $derived<FolderNode[]>(buildTree(this.folders));

	async load(): Promise<void> {
		/*
		 * `loading` is true until the first answer and never again, which is not the same as "a
		 * request is in flight": this screen re-reads on every library announcement.
		 *
		 * Set on every read, an announcement would replace the whole screen with a skeleton for the
		 * length of two round trips: the folder list, whatever was expanded on it, and any menu or
		 * dialog somebody had open all gone and back a moment later. Set on every read of an EMPTY
		 * library, that is the Add a folder dialog closing under the person adding their first one.
		 *
		 * The screen keeps what it has and swaps in the new rows when they arrive. `failed` is the
		 * other half of that promise: a screen with no data and no explanation is worse than
		 * either. Nothing here reads the lists it writes, so calling it from an `$effect` cannot
		 * make that effect depend on them and run again on its own write.
		 */
		try {
			// The tree first: a guest may have it and may not have the roots, and the tree is the
			// part of this screen they are here for.
			this.folders = (
				await api.get<components['schemas']['FoldersView']>('/library/folders')
			).folders;
			this.failed = null;
		} catch (error) {
			this.failed = messageOf(error);
			this.loading = false;
			return;
		}

		/*
		 * THE ROOTS ARE AN ADMIN'S QUESTION, SO ONLY AN ADMIN ASKS IT.
		 *
		 * The list of roots describes the server's disk and the route answers an admin alone. Asked
		 * by a guest's browser, it is a refusal in the server's log and the browser's console on
		 * every visit to Browse, for an answer this screen already has: a guest manages nothing.
		 * The role is known here (the shell draws nothing until the session has answered), so the
		 * store asks only what its viewer may ask. Kept in the store rather than at each call site
		 * because every screen that reads the library comes through `load`, and the next one would
		 * not know to check. Deciding what to ask, never a permission: the route refuses on its own.
		 */
		if (!session.isAdmin) {
			this.roots = [];
			this.canManage = false;
			this.loading = false;
			return;
		}

		try {
			this.roots = (await api.get<components['schemas']['RootsView']>('/library/roots')).roots;
			// Answered, so this account may manage the library. Deciding what to draw, never a
			// permission: every endpoint behind this screen refuses on its own, and would refuse a
			// request this screen never made.
			this.canManage = true;
		} catch (error) {
			// A refusal here is an account whose role changed after the session was read (an admin
			// made a guest in another tab): not a failure of this screen, they get the tree and the
			// settings simply are not theirs. Anything else is worth saying out loud.
			if (!(error instanceof ApiError && (error.status === 403 || error.status === 401))) {
				toasts.show(messageOf(error), { tone: 'error' });
			}
			this.roots = [];
			this.canManage = false;
		}
		this.loading = false;
	}

	/** A library folder named as the way to its files, or its name before its folder is read. */
	named(rootId: string, name: string): ToastPiece | string {
		const top = this.folders.find((one) => one.root_id === rootId && one.parent_id === null);
		return top ? thing('folder', top.id, name) : name;
	}

	folder(id: string): Folder | undefined {
		return this.folders.find((each) => each.id === id);
	}

	async detail(id: string): Promise<FolderDetail | null> {
		try {
			return await api.get<FolderDetail>(`/library/folders/${id}`);
		} catch {
			return null;
		}
	}

	/**
	 * Point Sift at a folder.
	 *
	 * `scan` false adds the folder without reading it yet. See `NewRoot.scan` on the server. It
	 * is the only second argument: whether Sift may change the files there is decided elsewhere, so
	 * a caller passing `false` here is asking for no read, and nothing else (see `AddFolder`).
	 */
	async addRoot(absPath: string, scan = true): Promise<string | null> {
		this.busy = true;
		try {
			const made = await api.post<Root>('/library/roots', { body: { abs_path: absPath, scan } });
			await this.load();
			/* What the folder is called on disk, which is the only name it has.
			 *
			 * Split on EITHER separator: a Windows path has no forward slash in it at all, so a split
			 * on `/` alone would hand back the whole path as the folder's name, and the message would
			 * read "Sift is reading C:\Users\me\Videos." A network share written with backslashes
			 * takes the same answer. */
			const called = absPath.split(/[\\/]/).filter(Boolean).pop() ?? absPath;
			/* WHAT ACTUALLY HAPPENED, which is not the same on both paths. Adding a folder from
			   Settings queues a scan and "Sift is reading" is true. Adding one during setup
			   deliberately does not (see `scan`), so the same words there promise work that is not
			   running, and somebody waits for a library that is never going to fill. */
			/* AND WHAT IS TRUE OF THE FIRST FOLDER ON A DEVICE NEVER MEASURED: nothing of it is read
			   until the benchmark it queued has settled, so "Sift is reading" would promise work
			   that is minutes away. The server's sentence for that wait instead, the one the wall
			   and Activity say (`performance/benchmark.py HELD`). */
			const held = await heldFolder();
			const named = this.named(made.id, called);
			toasts.show(held ?? [scan ? 'Sift is reading ' : 'Sift has added ', named], {
				tone: 'success'
			});
			return null;
		} catch (error) {
			// Handed back rather than toasted: every refusal here is about the folder somebody just
			// typed, so it belongs under the box they typed it in, where they can fix it.
			//
			// And the server's own words, not the client's one-liner. This is the one endpoint on
			// this screen whose refusals are written for the person reading them: "Sift is already
			// watching that folder as part of Videos" says what to change, where "That request was
			// not valid" leaves them stuck holding a path and no idea what is wrong with it.
			return refusalOf(error);
		} finally {
			this.busy = false;
		}
	}

	/**
	 * Tell Sift where a library folder is now, after it was moved or renamed outside Sift.
	 *
	 * A folder INSIDE a library is recognised on the next walk, from what is inside it. The library
	 * folder itself cannot be: Sift is pointed at it by its path, so once that path stops existing
	 * it is not looking anywhere near the new one.
	 *
	 * Nothing under it is re-read, and nothing recorded about any folder in it is lost. Removing the
	 * library and adding it back recovers the files by their digests and drops every share on every
	 * folder, deliberately.
	 */
	async moved(root: Root, absPath: string): Promise<string | null> {
		this.busy = true;
		try {
			await api.post(`/library/roots/${root.id}/moved`, { body: { abs_path: absPath } });
			await this.load();
			toasts.show(['Sift is reading ', this.named(root.id, root.name), ' where it is now'], {
				tone: 'success'
			});
			return null;
		} catch (error) {
			// Handed back rather than toasted: every refusal here is about the folder somebody just
			// chose, so it belongs under the picker they chose it in.
			return refusalOf(error);
		} finally {
			this.busy = false;
		}
	}

	/** Make a folder inside a library, on the disk and in the tree together. */
	async createFolder(parentId: string, name: string): Promise<string | null> {
		return await this.write(() =>
			api.post('/library/folders', { body: { parent_id: parentId, name } })
		);
	}

	/**
	 * Rename a folder, move it, or both. One act on the disk and one rewrite of the rows.
	 *
	 * The folder keeps its id, so a share, a restriction, a concealment and the rule saying whose
	 * files land in it all survive being rearranged. That is the whole reason this exists rather
	 * than leaving people to do it in a file manager and have Sift work out what happened after.
	 */
	async renameFolder(folderId: string, name: string): Promise<string | null> {
		return await this.write(() =>
			request('PATCH', `/library/folders/${folderId}`, { body: { name } })
		);
	}

	async moveFolder(folderId: string, parentId: string): Promise<string | null> {
		return await this.write(() =>
			request('PATCH', `/library/folders/${folderId}`, { body: { parent_id: parentId } })
		);
	}

	/**
	 * Delete a folder from the disk, with every file Sift indexed under it.
	 *
	 * `/folders/{id}` and not `/library/folders/{id}`, which is where every other folder call goes:
	 * removing a file somebody else put on a disk is owned by one service in Sift, and its routes
	 * live together. It is the only call on this class that changes bytes rather than rows.
	 *
	 * Hands back what happened rather than only a refusal, because this is the one delete that can
	 * do most of what was asked and not all of it: a folder holding something Sift never indexed
	 * is left standing, with its files gone.
	 */
	async deleteFolder(
		folderId: string
	): Promise<{ said: components['schemas']['FolderDeleted'] | null; refusal: string | null }> {
		this.busy = true;
		try {
			const said = await api.del<components['schemas']['FolderDeleted']>(`/folders/${folderId}`);
			await this.load();
			// The wall behind this has to lose the folder's tiles, and nothing else would tell it to.
			libraryChanges.changed();
			return { said, refusal: null };
		} catch (error) {
			return { said: null, refusal: refusalOf(error) };
		} finally {
			this.busy = false;
		}
	}

	/** One shape for the three writes above: do it, re-read, and hand back the server's own words.
	 *
	 * The refusals here are written for the person who typed the name ("that name is reserved",
	 * "there is already something called that"), so they are handed back to the dialog rather than
	 * toasted past. `messageOf` would replace them with a one-liner about a request. */
	private async write(send: () => Promise<unknown>): Promise<string | null> {
		this.busy = true;
		try {
			await send();
			await this.load();
			// The grid behind this has to lose or gain the folder's tiles, and nothing else would
			// tell it to: rearranging a folder enqueues no job.
			libraryChanges.changed();
			return null;
		} catch (error) {
			return refusalOf(error);
		} finally {
			this.busy = false;
		}
	}

	private async change(root: Root, body: Record<string, unknown>, said: string): Promise<void> {
		this.busy = true;
		try {
			// The client exposes no PATCH verb, and it is not this screen's file to add one to.
			await request<Root>('PATCH', `/library/roots/${root.id}`, { body });
			await this.load();
			toasts.show(said, { tone: 'success' });
		} catch (error) {
			// Reloaded on failure too, so the switch goes back to what the server actually thinks
			// rather than staying where the click left it and lying about it.
			await this.load();
			toasts.show(messageOf(error), { tone: 'error' });
		} finally {
			this.busy = false;
		}
	}

	async removeRoot(root: Root): Promise<void> {
		this.busy = true;
		try {
			await api.del(`/library/roots/${root.id}`);
			await this.load();
			// The grid on the page behind this one has to lose the removed folder's tiles, and nothing
			// else would tell it to: a removal enqueues no job the way adding one does. Bump the shared
			// signal it watches, so it re-reads and the files drop off without a reload.
			libraryChanges.changed();
			toasts.show(`Sift has forgotten ${root.name}. The files are untouched.`, { tone: 'success' });
		} catch (error) {
			toasts.show(messageOf(error), { tone: 'error' });
		} finally {
			this.busy = false;
		}
	}

	/**
	 * Walk one folder again, rather than the whole root it is in.
	 *
	 * The scan can walk one folder (the watcher uses it for a single arriving file), and a
	 * person asking about one folder should not have to walk the whole root: on a large library
	 * that is minutes of walking to notice one folder.
	 *
	 * Does nothing at all when the folder's root is unknown, which can only happen if the row was
	 * removed while its menu was open. Silence is right there: the folder is gone, and an error
	 * about it would be about something that is not on screen any more.
	 */
	async rescanFolder(rootId: string | undefined, folderId: string, name: string): Promise<void> {
		if (!rootId) return;
		this.busy = true;
		try {
			/* Reading and nothing after it, like every other Scan: see `rescan` below. */
			await api.post(`/library/roots/${rootId}/rescan`, {
				query: { folder_id: folderId, scan_only: true }
			});
			toasts.show(['Checking ', thing('folder', folderId, name), ' for new files'], {
				tone: 'info'
			});
		} catch (error) {
			toasts.show(messageOf(error), { tone: 'error' });
		} finally {
			this.busy = false;
		}
	}

	/*
	 * There is no whole-library rescan here. Importing's Scan now and the empty library's Scan Now
	 * are the Scan task's own press (`pressTask('scan', ...)` in `$lib/jobs/tasks.svelte`), which reads
	 * every folder and stops. One way to start the whole-library scan, not two.
	 */

	/**
	 * `scanOnly` asks for the READING and nothing after it.
	 *
	 * A scan is where every other stage is started from: it hands out a read of each file, and each
	 * read hands out the pictures, the faces and the descriptions. That is right for a file
	 * arriving, and wrong for somebody who pressed a button that says Scan and got all three. It is
	 * off by default, so a scan from the watcher or the Folders screen starts every stage.
	 */
	async rescan(root: Root, options: { scanOnly?: boolean } = {}): Promise<void> {
		this.busy = true;
		try {
			await api.post(`/library/roots/${root.id}/rescan`, { query: asked(options) });
			toasts.show(['Checking ', this.named(root.id, root.name), ' for new files'], {
				tone: 'info'
			});
		} catch (error) {
			toasts.show(messageOf(error), { tone: 'error' });
		} finally {
			this.busy = false;
		}
	}
}

/**
 * `scan_only` as a query, and left OUT rather than sent false.
 *
 * The client has one way to add a parameter to a request and this is it, so the address stays the
 * typed thing the schema knows about. Glued onto the path by hand it would type-check on one of the
 * two routes and not on the other: the openings in `ApiPath` stand in for an id, and a route with
 * no id in it has no opening for a query to hide in.
 *
 * Absent when false, so an ordinary rescan asks for exactly the plain address. The server matches a
 * job's payload exactly to decide whether work is already coming; see `rescan_root`.
 */
function asked(options: { scanOnly?: boolean }): { scan_only: boolean } | undefined {
	return options.scanOnly ? { scan_only: true } : undefined;
}

function messageOf(error: unknown): string {
	if (error instanceof ApiError) return error.message;
	return UNREACHABLE;
}

/**
 * A refusal in the server's words where it has any, and the client's otherwise.
 *
 * For adding a folder and for moving one. Both are refused for reasons that name the next thing to
 * do, and both were written for the person reading them. Everywhere else on this screen the
 * one-liner is what should be shown. See `ApiError.detail` for why saying more is usually saying
 * too much.
 */
function refusalOf(error: unknown): string {
	if (error instanceof ApiError) return error.detail ?? error.message;
	return messageOf(error);
}
