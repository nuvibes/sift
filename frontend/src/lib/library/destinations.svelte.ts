// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Where the next download may go: the folders a download can be sent to, as a chooser lists them.
 *
 * ## Why this is its own module
 *
 * There are two places somebody says where a download goes before starting it: the Add button's
 * "Download folder", from any screen, and the Downloads screen's own, a row of its Options menu.
 * They are the same question, so one list answers both: two copies of how the default row is
 * named and which folders come first would drift apart.
 *
 * Choosing one makes it the stored default (`saveDownloadFolder`), so the row at the top of the
 * list, and Settings, Downloads, name it from then on.
 */

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

/**
 * Make a folder (null for none) the default download folder: the default row is read and written
 * whole with only the folder replaced, since a field left out is saved as empty.
 */
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

/*
 * What the download folder reads when none is set, in every place it is shown: Settings,
 * Downloads, and the "Download folder" on the Add button and on the Downloads screen.
 *
 * ONE PHRASE, AND IT SAYS WHAT HAPPENS. A word like "Sift" on this row would read as a folder
 * called Sift, and there is no such folder: with nothing set a download has nowhere to land, and
 * the server refuses it rather than choose one. "Not set" is the state; "each download asks" is
 * what that means for the next one, which the Downloads screen then does (`PasteBox`). A folder is
 * never chosen for somebody silently.
 */
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

	/*
	 * Which folder applies when nothing is chosen, so the first row can NAME it.
	 *
	 * Undefined while it is unknown (before the read, or after one that failed), and null once
	 * it is known that nothing is set. The three states are different things to say, and a null
	 * standing for both would have the row claim there is no download folder whenever the read fell
	 * over.
	 *
	 * Read from `/site-options`, which is where the answer is STORED: one row per site plus the
	 * scope everything else follows, and its `dest_folder_id` is the default. Asked of the folder
	 * list instead it would be a second answer to the same question, free to disagree, which is
	 * why this is not a field on `FolderView`.
	 */
	get defaultFolderId(): string | null | undefined {
		return stored.folderId;
	}

	#loaded = false;

	/**
	 * The folders as a chooser takes them, with the least path that tells two of one name apart.
	 *
	 * With the label the folder's own name and nothing else, a library with a folder per creator
	 * would list nine rows reading "Images". `disambiguate` is the one answer to that, shared with
	 * every other place a folder is chosen, so the choosers cannot tell them apart differently.
	 */
	readonly placed = $derived(
		disambiguate(
			this.folders.map((folder) => ({
				value: folder.id,
				label: folder.name,
				path: folder.rel_path || folder.name
			}))
		)
	);

	/*
	 * The first row SAYS which folder it means, and it is the same phrase everywhere one is offered.
	 *
	 * "Default download folder" would name a setting rather than a place: the one thing somebody wants
	 * to know before accepting it is WHERE it lands, and the answer would be two screens away.
	 * The name plus "(default)" says both together: which folder, and that it is the one that
	 * applies by itself.
	 *
	 * The disambiguating phrase is taken from the row `disambiguate` already made for that folder
	 * rather than worked out again here: the two rows are the same folder, so a library with nine
	 * folders called Images must not tell them apart one way at the top of the list and another way
	 * down it.
	 */
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

	/*
	 * What this account downloaded into last, then everything else alphabetically.
	 *
	 * The server answers in the order the library walks, which is neither alphabetical nor anything
	 * a person could predict, so on a library with one folder per person this chooser would be a
	 * hundred rows to read to find one. The rule is `recentFirst`, shared with the naming panel's
	 * chooser so the two cannot come to order one list differently.
	 *
	 * The default row is not passed through it. It is not a folder among the folders: it is what
	 * applies when nothing is chosen, so it stays at the top whatever the order below it is.
	 */
	readonly options = $derived<DestinationOption[]>([
		this.defaultOption,
		...recentFirst(this.placed, recentFolders(), RECENT_FOLDERS_KEPT)
	]);

	/**
	 * Read them again when they move: a folder added or handed over is said on the library bell,
	 * a default folder chosen in another window on the settings bell. Called by the component that
	 * holds this list, while it sets up, so the listening ends with it.
	 */
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

	/*
	 * Which folder the empty choice means, asked separately and allowed to fail on its own.
	 *
	 * Not folded into the call above, and not `Promise.all` with it: the folder list is what the
	 * chooser cannot work without, and the name of the default is a nicety on one row of it. A
	 * failure here leaves that row saying what it always said and everything else working.
	 */
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

	/*
	 * The Sites given a download folder of their own, by the supported list's key.
	 *
	 * Read in the same answer as the default, because it is the other half of one question: where a
	 * download from THIS Site lands when nobody chooses. A Site with its own folder has an answer
	 * even with no default set, so a paste from it has nothing to ask.
	 */
	get sitesWithAFolder(): string[] {
		return stored.sites;
	}

	/*
	 * Whether a download from these Sites, sent with this choice, has NOWHERE to land, so the
	 * screen must ask before sending it rather than let the server refuse it afterwards.
	 *
	 * Only when that is KNOWN: a default still being read (undefined) is not "nothing set", and
	 * asking on a guess would stop a download that had somewhere to go. A line from no Site Sift
	 * recognizes follows the default alone, so an empty list asks exactly when the default is unset.
	 */
	hasNowhereFor(chosen: string, siteKeys: readonly string[], unrecognized: boolean): boolean {
		if (chosen !== '' || this.defaultFolderId !== null) return false;
		if (unrecognized) return true;
		return siteKeys.some((key) => !this.sitesWithAFolder.includes(key));
	}
}
