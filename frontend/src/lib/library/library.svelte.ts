/* What the Library screen knows, and how it asks the server for it. */

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
	/** Whether the server answered when this asked for the roots. */
	canManage = $state(false);
	loading = $state(true);
	/** Set when the whole screen could not load. A screen with no data and no explanation is worse. */
	failed = $state<string | null>(null);
	busy = $state(false);

	tree = $derived<FolderNode[]>(buildTree(this.folders));

	async load(): Promise<void> {
		/* `loading` is true until the first answer and never again, which is not the same as "a
		 * request is in flight": this screen re-reads on every library announcement. */
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

		/* THE ROOTS ARE AN ADMIN'S QUESTION, SO ONLY AN ADMIN ASKS IT. */
		if (!session.isAdmin) {
			this.roots = [];
			this.canManage = false;
			this.loading = false;
			return;
		}

		try {
			this.roots = (await api.get<components['schemas']['RootsView']>('/library/roots')).roots;
			// Answered, so this account may manage the library.
			this.canManage = true;
		} catch (error) {
			// A refusal here is an account whose role changed after the session was read (an admin
			// made a guest in another tab): not a failure of this screen, they get the tree and the
			// settings simply are not theirs.
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

	/** Point Sift at a folder. `scan` false adds the folder without reading it yet. */
	async addRoot(absPath: string, scan = true): Promise<string | null> {
		this.busy = true;
		try {
			const made = await api.post<Root>('/library/roots', { body: { abs_path: absPath, scan } });
			await this.load();
			/* What the folder is called on disk, which is the only name it has. */
			const called = absPath.split(/[\\/]/).filter(Boolean).pop() ?? absPath;
			/* WHAT ACTUALLY HAPPENED, which is not the same on both paths. */
			/* AND WHAT IS TRUE OF THE FIRST FOLDER ON A DEVICE NEVER MEASURED: nothing of it is
			   read until the benchmark it queued has settled, so "Sift is reading" would promise
			   work that is minutes away. */
			const held = await heldFolder();
			const named = this.named(made.id, called);
			toasts.show(held ?? [scan ? 'Sift is reading ' : 'Sift has added ', named], {
				tone: 'success'
			});
			return null;
		} catch (error) {
			// Handed back rather than toasted: every refusal here is about the folder somebody just
			// typed, so it belongs under the box they typed it in, where they can fix it.
			return refusalOf(error);
		} finally {
			this.busy = false;
		}
	}

	/** Tell Sift where a library folder is now, after it was moved or renamed outside Sift. */
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

	/** Rename a folder, move it, or both. One act on the disk and one rewrite of the rows. */
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

	/** Delete a folder from the disk, with every file Sift indexed under it. */
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

	/** One shape for the three writes above: do it, re-read, and hand back the server's own
	 * words. */
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
			// The grid on the page behind this one has to lose the removed folder's tiles, and
			// nothing else would tell it to: a removal enqueues no job the way adding one does.
			libraryChanges.changed();
			toasts.show(`Sift has forgotten ${root.name}. The files are untouched.`, { tone: 'success' });
		} catch (error) {
			toasts.show(messageOf(error), { tone: 'error' });
		} finally {
			this.busy = false;
		}
	}

	/** Walk one folder again, rather than the whole root it is in. */
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

	/* There is no whole-library rescan here. */

	/** `scanOnly` asks for the READING and nothing after it. */
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

/** `scan_only` as a query, and left OUT rather than sent false. */
function asked(options: { scanOnly?: boolean }): { scan_only: boolean } | undefined {
	return options.scanOnly ? { scan_only: true } : undefined;
}

function messageOf(error: unknown): string {
	if (error instanceof ApiError) return error.message;
	return UNREACHABLE;
}

/** A refusal in the server's words where it has any, and the client's otherwise. */
function refusalOf(error: unknown): string {
	if (error instanceof ApiError) return error.detail ?? error.message;
	return messageOf(error);
}
