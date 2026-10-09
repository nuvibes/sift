// SPDX-License-Identifier: AGPL-3.0-or-later
/* Who sees a folder, and what may leave this device from it, as the rows every folder menu draws
 * (Browse, the folder tree and the library rows in `Settings > Folders`, the ground inside a
 * folder), so a folder offers the same verbs in the same words wherever it is right-clicked. */

import type { components } from '$lib/api/schema';
import { menuGroups, type Verb } from '$lib/components/common/verbs';
import { setKeptLocal } from '$lib/entity/enrichment.svelte';
import { setKeptFromSwaps } from '$lib/components/swap/swap';
import { toasts } from '$lib/shell/toasts.svelte';

interface FolderVerbHandlers {
	/** Hide the folder from this account, or bring it back. Takes what it should become. */
	hide?: (hidden: boolean) => void;
	/** Open the sharing panel on the folder. */
	share?: () => void;
	/** Open the report of who can reach the folder and through what (`VisibilityDialog`). */
	visibility?: () => void;
	/** Put "Don't enrich" on the folder or take it off. Takes what it should become. */
	keepLocal?: (kept: boolean) => void;
	/** Put "Don't swap" on the folder or take it off. Takes what it should become. */
	keepFromSwaps?: (kept: boolean) => void;
}

interface FolderVerbsContext {
	isAdmin: boolean;
	/** Whether the folder is hidden now, so the row offers the way back out. */
	hidden?: boolean;
	/** The library rows say "Sharing", the panel it opens. */
	shareLabel?: string;
	/** The folder's own "Don't enrich" and "Don't swap", so each row offers the way back out. */
	keptLocal?: boolean;
	keptFromSwaps?: boolean;
	handlers: FolderVerbHandlers;
}

/** The folder's who-sees-it rows. Sharing and the report are an admin's, like on every wall. */
export function folderVerbs(context: FolderVerbsContext): Verb[] {
	const {
		isAdmin,
		hidden = false,
		shareLabel = 'Share',
		keptLocal = false,
		keptFromSwaps = false,
		handlers
	} = context;
	const verbs: Verb[] = [];
	const { hide, share, visibility, keepLocal, keepFromSwaps } = handlers;
	if (hide && !hidden) {
		verbs.push({
			id: 'hide',
			label: 'Hide',
			icon: 'visibility_off',
			group: 'share',
			run: () => hide(true)
		});
	}
	if (isAdmin && share) {
		verbs.push({
			id: 'share',
			label: shareLabel,
			icon: 'group',
			filled: true,
			group: 'share',
			run: () => share()
		});
	}
	if (hide && hidden) {
		verbs.push({
			id: 'hide',
			label: 'Unhide',
			icon: 'visibility',
			group: 'share',
			run: () => hide(false)
		});
	}
	if (isAdmin && visibility) {
		verbs.push({
			id: 'visibility',
			label: 'Visibility',
			icon: 'policy',
			group: 'share',
			singleOnly: true,
			run: () => visibility()
		});
	}
	/* WHAT MAY LEAVE THIS DEVICE, the entity pages' two rows in their words and glyphs: an
	   admin's, since the mark is read by every pass and every swap, and it reaches every file
	   under the folder. */
	if (isAdmin && keepLocal) {
		verbs.push({
			id: 'keep-local',
			label: keptLocal ? 'Allow enrichment' : "Don't enrich",
			icon: keptLocal ? 'public' : 'shield',
			group: 'enrich',
			run: () => keepLocal(!keptLocal)
		});
	}
	if (isAdmin && keepFromSwaps) {
		verbs.push({
			id: 'keep-from-swaps',
			label: keptFromSwaps ? 'Allow swapping' : "Don't swap",
			icon: keptFromSwaps ? 'swap_horiz' : 'do_not_disturb_on',
			group: 'enrich',
			run: () => keepFromSwaps(!keptFromSwaps)
		});
	}
	return verbs;
}

/** A folder as the marks need it: its own two switches, as the folder list carries them. */
type FolderView = components['schemas']['FolderView'];
type FolderMarked = Pick<FolderView, 'id'> &
	Partial<Pick<FolderView, 'keep_local' | 'keep_from_swaps'>>;

/** The mark a folder row wears (`SharingMark`'s `refused`): Don't enrich first, it keeps back more. */
export function refusedOf(folder: FolderMarked): 'local' | 'swap' | undefined {
	if (folder.keep_local) return 'local';
	return folder.keep_from_swaps ? 'swap' : undefined;
}

/** `folderVerbs` with a folder's two marks, in the parts a menu draws (`menuGroups`). */
export function folderMenuParts(context: FolderVerbsContext, marks?: FolderMarks): Verb[][] {
	if (!marks) return menuGroups(folderVerbs(context));
	return menuGroups(
		folderVerbs({
			...context,
			keptLocal: marks.keptLocal,
			keptFromSwaps: marks.keptFromSwaps,
			handlers: {
				...context.handlers,
				keepLocal: marks.keepLocal,
				keepFromSwaps: marks.keepFromSwaps
			}
		})
	);
}

/** What `folderVerbs` takes for the two marks on one folder. */
export interface FolderMarks {
	keptLocal: boolean;
	keptFromSwaps: boolean;
	keepLocal: (kept: boolean) => void;
	keepFromSwaps: (kept: boolean) => void;
}

/** The two marks on one folder, read off its row and written through the same doors as a person's
 * (`setKeptLocal`, `setKeptFromSwaps`), so every folder menu presses them one way. */
export function folderMarks(folder: FolderMarked, after: () => void): FolderMarks {
	return {
		keptLocal: folder.keep_local === true,
		keptFromSwaps: folder.keep_from_swaps === true,
		keepLocal: (kept) => void setKeptLocal('folder', folder.id, kept).then(after),
		keepFromSwaps: (kept) =>
			void setKeptFromSwaps('folder', folder.id, kept).then(
				() => {
					toasts.show(
						kept
							? 'Kept out of swaps, with every file inside it'
							: 'It can be offered in a swap again',
						{ tone: 'success' }
					);
					after();
				},
				() => toasts.show("That couldn't be changed", { tone: 'error' })
			)
	};
}
