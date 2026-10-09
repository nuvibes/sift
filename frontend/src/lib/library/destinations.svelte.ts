// SPDX-License-Identifier: AGPL-3.0-or-later
/* Where the next download may go: the folders a download can be sent to, as a chooser lists
 * them. */

import { api, ApiError } from '$lib/api/client';
import { toasts } from '$lib/shell/toasts.svelte';
import { thing } from '$lib/components/common/toast-pieces';
import { libraryChanges, settingChanges, whenChanged } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';
import { disambiguate, recentFirst } from '$lib/library/folder-names';
import { recentFolders, RECENT_FOLDERS_KEPT } from '$lib/shell/interface-state.svelte';

type Folder = Pick<components['schemas']['FolderView'], 'id' | 'name' | 'rel_path'>;
type SiteOptions = components['schemas']['SiteOptionsResponse'];

/** The reserved scope everything follows unless a Site is given its own. The server's word. */
const EVERYTHING = '*default*';

/* One copy of the stored default for every chooser on screen, so a pick in one is named by all. */
const stored = $state<{ folderId: string | null | undefined; sites: string[] }>({
	folderId: undefined,
	sites: []
});

/** Make a folder (null for none) the default download folder: the default row is read and written
 * whole with only the folder replaced, since a field left out is saved as empty. */
export async function saveDownloadFolder(folderId: string | null, name?: string): Promise<boolean> {
	try {
		const row = (await api.get<SiteOptions>('/site-options')).default;
		await api.put(`/site-options/${encodeURIComponent(EVERYTHING)}`, {
			body: { naming: row.naming, dest_folder_id: folderId, downloader: row.downloader ?? null }
		});
	} catch (error) {
		const said = error instanceof ApiError ? (error.detail ?? error.message) : null;
		toasts.show(said ?? "Couldn't change that", { tone: 'error' });
		return false;
	}
	stored.folderId = folderId;
	if (folderId) {
		toasts.show(['Downloads will go to ', thing('folder', folderId, name ?? 'that folder')], {
			tone: 'success'
		});
	}
	return true;
}

/* What the download folder reads when none is set, in every place it is shown: Settings,
 * Downloads, and the "Download folder" on the Add button and on the Downloads screen. */
export const NO_DOWNLOAD_FOLDER = 'Not set, so each download asks';

/** One row of the chooser. `value` is '' for the default and a folder id for anything else. */
interface DestinationOption {
	value: string;
	label: string;
	detail?: string;
}

export type FoldersRead = 'unread' | 'read' | 'failed';

export class Destinations {
	folders = $state<Folder[]>([]);
	#read = $state<FoldersRead>('unread');

	/** An empty list means no folder only once read: a failed read is not "no folder". */
	get foldersRead(): FoldersRead {
		return this.#read;
	}

	/* Which folder applies when nothing is chosen, so the first row can NAME it. */
	get defaultFolderId(): string | null | undefined {
		return stored.folderId;
	}

	#loaded = false;

	/** The folders as a chooser takes them, with the least path that tells two of one name apart. */
	readonly placed = $derived(
		disambiguate(
			this.folders.map((folder) => ({
				value: folder.id,
				label: folder.name,
				path: folder.rel_path || folder.name
			}))
		)
	);

	/* The first row SAYS which folder it means, and it is the same phrase everywhere one is
	 * offered. */
	readonly defaultOption = $derived.by((): DestinationOption => {
		if (this.defaultFolderId === null) {
			// Known, and it is nothing. A download with no folder is refused by the server with the
			// same news, so the row must not promise a landing place there is not one of.
			return { value: '', label: NO_DOWNLOAD_FOLDER };
		}
		const named = this.placed.find((one) => one.value === this.defaultFolderId);
		// Unknown, or a default naming a folder this list does not hold: there is nothing honest to
		// put in a name, so the row says what it does instead.
		if (!named) return { value: '', label: 'Download folder' };
		return { value: '', label: `${named.label} (default)`, detail: named.detail };
	});

	/* What this account downloaded into last, then everything else alphabetically. */
	readonly options = $derived<DestinationOption[]>([
		this.defaultOption,
		...recentFirst(this.placed, recentFolders(), RECENT_FOLDERS_KEPT)
	]);

	/** Read them again when they move: a folder added or handed over is said on the library bell,
	 * a default folder chosen in another window on the settings bell. */
	follow(): void {
		const again = () => {
			if (!this.#loaded) return;
			this.#loaded = false;
			void this.load();
		};
		whenChanged(libraryChanges, again);
		whenChanged(settingChanges, again);
	}

	/** Read the folders once, then which of them is the default. Asked again after a failure. */
	async load(): Promise<void> {
		if (this.#loaded) return;
		this.#loaded = true;
		try {
			const body = await api.get<components['schemas']['FoldersView']>('/library/folders', {
				query: { writable: true }
			});
			// Only folders Sift may write in: any other would be a row that fails at the save.
			this.folders = (body?.folders ?? []).filter((folder) => folder.writable !== false);
			this.#read = 'read';
		} catch {
			// Not worth a toast: the default download folder still works without the chooser.
			this.#loaded = false;
			this.#read = 'failed';
		}
		await this.#loadDefault();
	}

	/* Which folder the empty choice means, asked separately and allowed to fail on its own. */
	async #loadDefault(): Promise<void> {
		try {
			const answer = await api.get<SiteOptions>('/site-options');
			stored.folderId = answer.default.dest_folder_id ?? null;
			stored.sites = answer.sites.filter((site) => site.dest_folder_id).map((site) => site.scope);
		} catch {
			stored.folderId = undefined;
			stored.sites = [];
		}
	}

	/** Make a listed folder the default, by the one writer, under the name this list gives it. */
	makeDefault(folderId: string): Promise<boolean> {
		return saveDownloadFolder(folderId, this.placed.find((one) => one.value === folderId)?.label);
	}

	/* The Sites given a download folder of their own, by the supported list's key. */
	get sitesWithAFolder(): string[] {
		return stored.sites;
	}

	/* Whether a download from these Sites, sent with this choice, has NOWHERE to land, so the
	 * screen must ask before sending it rather than let the server refuse it afterwards. */
	hasNowhereFor(chosen: string, siteKeys: readonly string[], unrecognized: boolean): boolean {
		if (chosen !== '' || this.defaultFolderId !== null) return false;
		if (unrecognized) return true;
		return siteKeys.some((key) => !this.sitesWithAFolder.includes(key));
	}
}
