/*
 * The things that can be done to a FILE, for the bar and the menu alike
 * (`$lib/components/common/verbs`).
 */

import type { IconName } from '$lib/design/icons';
import {
	addToVerb,
	favoriteVerb,
	pinVerb,
	ratingVerb,
	type Verb,
	type VerbPick
} from '$lib/components/common/verbs';
import { autoEnrichRows, songAgainRow, songLookupRow } from '$lib/entity/enrichment.svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import type { RunNowGroup } from '$lib/jobs/run-now.svelte';

export { barVerbs, flatVerbs, menuVerbs } from '$lib/components/common/verbs';
export type { Verb } from '$lib/components/common/verbs';

/** Stable: what the drift test names them by. */
export type VerbId =
	| 'tag'
	| 'add'
	| 'collect'
	| 'assign'
	| 'site'
	| 'photo_set'
	| 'song'
	| 'favorite'
	| 'pin'
	| 'rate'
	| 'move'
	| 'compress'
	| 'edit'
	| 'gif'
	| 'share'
	| 'visibility'
	| 'hide'
	| 'save'
	| 'link'
	| 'auto-enrich'
	| 'enrich'
	| 'keep-local'
	| 'keep-from-swaps'
	| 'run-now'
	| 'delete';

interface FileVerbsContext {
	isAdmin: boolean;
	canSave: boolean;
	/** On Hidden the hide verb points the other way. */
	showingHidden: boolean;
	/** Every file already kept local, so the verb reverses; mixed reads as not. */
	keptLocal?: boolean;
	/** As `keptLocal`, for swaps. */
	keptFromSwaps?: boolean;
	/** Whether anything may leave the machine at all: enrichment rows are drawn REFUSED. */
	enrichRefused?: boolean;
	enrichWhy?: string;
	/** One file only: forty files have forty answers. */
	lastEnriched?: string;
	/** The server's configured boxes; empty leaves a plain press. */
	enrichBoxes?: readonly { word: string; name: string }[];
	/** Null draws none; offered when off, so the refusal says where to turn it on. */
	songLookup?: { word: string; name: string } | null;
	/** Empty leaves Run task out (`$lib/jobs/run-now`). */
	runGroups?: readonly RunNowGroup[];
	/** With the vault open hidden files sit on ordinary walls, so the screen alone cannot say. */
	allHidden?: boolean;
	/**
	 * A LOCKED TILE: Hide is false and Unhide needs the PIN, so the row offers the PIN or nothing.
	 */
	allLocked?: boolean;
	allFavorite?: boolean;
	/** A MIXED set reads as not pinned and pins the lot. */
	allPinned?: boolean;
	/** The one file a menu was opened on, for "Remove from favorites". */
	subject?: {
		media_type: string;
		favorite: boolean;
		pinned?: boolean;
		concealed: boolean;
	} | null;
	/** Save's wording differs for one. */
	count: number;
	rating?: number | null;
	/** False leaves Move out rather than greying it. */
	canMove?: boolean;
	/** Compressing and editing write a new file, so they need a writable folder. */
	canCompress?: boolean;
	handlers: FileVerbHandlers;
}

/** The five places are LISTS (`VerbPick`), so every surface draws the list. */
export interface FileVerbHandlers {
	tag: VerbPick;
	collect: VerbPick;
	assign: VerbPick;
	/** The flyout's cleared row takes a wrong Site off the whole set. */
	site: VerbPick;
	photoSet: VerbPick;
	/** A file carries one song. */
	song: VerbPick;
	favorite: (ids: string[]) => void;
	/** OPTIONAL: a pin belongs only on curated walls. Takes the target state. */
	pin?: (ids: string[], pinned: boolean) => void;
	rate: (ids: string[], rating: number | null) => void;
	move: (ids: string[]) => void;
	rename: (ids: string[]) => void;
	compress: (ids: string[]) => void;
	edit: (ids: string[]) => void;
	/** OPTIONAL: Create GIF's own door. */
	gif?: (ids: string[]) => void;
	/** OPTIONAL: the Files wall at `like:<id>`. */
	similar?: (ids: string[]) => void;
	share: (ids: string[]) => void;
	visibility: (ids: string[]) => void;
	hide: (ids: string[]) => void;
	/** OPTIONAL: absent, a locked tile's menu has no row about hiding. */
	unlock?: () => void;
	save: (ids: string[]) => void;
	link: (ids: string[]) => void;
	/** The press is the consent: an exact-hash match is written; the rest waits under Organize. */
	autoEnrich: (ids: string[], box?: string) => void;
	/** Decides nothing: answers wait under Organize. */
	enrich: (ids: string[]) => void;
	/** OPTIONAL: AcoustID's lookup. */
	lookUpSongs?: (ids: string[]) => void;
	lookUpSongsAgain?: (ids: string[]) => void;
	/** Takes the target state; mixed keeps the lot local. */
	keepLocal?: (ids: string[], kept: boolean) => void;
	/** OPTIONAL: the Visibility panel's "Don't swap", as a row. */
	keepFromSwaps?: (ids: string[], kept: boolean) => void;
	runNow?: (ids: string[], run: string) => void;
	remove: (ids: string[]) => void;
}

/** The Importing pane's glyphs, so a press looks the same in both places. */
const STAGE_ICON: Record<string, IconName> = {
	scan: 'split_scene',
	generate: 'error_med',
	identify: 'person'
};

/**
 * "Run task": Importing's presses, each with its every-pass row and its per-file passes; null when
 * empty.
 */
export function runNowVerb(
	groups: readonly RunNowGroup[],
	run: (ids: string[], pass: string) => void
): Verb | null {
	const stages = groups
		.filter((group) => group.passes.length > 0)
		.map((group) => {
			const icon = STAGE_ICON[group.family] ?? 'play_arrow';
			return {
				id: `run-now:${group.family}`,
				label: group.label,
				icon,
				alone: true,
				/* The every-pass row first, filled by the server's `every`. */
				children: [
					{ pass: group.every, filled: true },
					...group.passes.map((pass) => ({ pass, filled: false }))
				].map(({ pass, filled }) => ({
					id: `run-now:${group.family}:${pass.key}`,
					label: pass.label,
					icon,
					filled,
					run: (ids: string[]) => run(ids, pass.key)
				}))
			};
		});
	if (stages.length === 0) return null;
	return {
		id: 'run-now',
		label: 'Run task',
		icon: 'play_arrow',
		group: 'change',
		children: stages
	};
}

interface SaveWording {
	label: (mediaType: string, count: number) => string;
	icon: (mediaType: string, count: number) => IconName;
}

/** Trim or Modify, never "Edit", which means renaming elsewhere. */
export function editLabel(mediaType: string | undefined): string {
	return mediaType === 'video' ? 'Trim' : 'Modify';
}

const gifLabel = 'Create GIF';

export const NO_WRITABLE_FOLDER = 'No writable folder';

/** Not at a phone's width, where a finger covers the frame it is choosing. */
export function clipEditsOffered(): boolean {
	return !phoneWidth.yes;
}

const compressLabel = 'Compress';

type VerbFamily = (context: FileVerbsContext, saving: SaveWording) => Verb[];

/** Every verb that applies, decided here so both surfaces offer a guest the same set. */
export function fileVerbs(context: FileVerbsContext, saving: SaveWording): Verb[] {
	// No Open: clicking a tile does that.
	const families: VerbFamily[] = [
		vocabularyVerbs,
		keepVerbs,
		diskVerbs,
		changeVerbs,
		enrichVerbs,
		runTaskVerbs,
		shareVerbs,
		hideVerbs,
		removeVerbs
	];
	return families.flatMap((family) => family(context, saving));
}

function vocabularyVerbs(context: FileVerbsContext): Verb[] {
	const { isAdmin, allFavorite = false, allPinned = false, subject, handlers } = context;
	const verbs: Verb[] = [];
	const favoriteAlready = Boolean(subject?.favorite || allFavorite);

	if (isAdmin) {
		verbs.push(
			addToVerb([
				{ id: 'assign', label: 'Person', icon: 'person', pick: handlers.assign },
				{ id: 'site', label: 'Site', icon: 'public', pick: handlers.site },
				{ id: 'collect', label: 'Collection', icon: 'box', pick: handlers.collect },
				{ id: 'photo_set', label: 'Photo Set', icon: 'photo_library', pick: handlers.photoSet },
				{ id: 'tag', label: 'Tag', icon: 'shoppingmode', pick: handlers.tag },
				{ id: 'song', label: 'Song', icon: 'music_note_2', pick: handlers.song },
				favoriteVerb(favoriteAlready, handlers.favorite, true)
			])
		);
	}

	if (!isAdmin) {
		verbs.push(favoriteVerb(favoriteAlready, handlers.favorite, false));
	}

	if (handlers.pin) {
		verbs.push(pinVerb(Boolean(subject?.pinned || allPinned), handlers.pin));
	}

	verbs.push(ratingVerb(context.rating ?? null, handlers.rate));
	return verbs;
}

function keepVerbs(context: FileVerbsContext, saving: SaveWording): Verb[] {
	const { canSave, subject, count, handlers } = context;
	const verbs: Verb[] = [];
	if (handlers.similar && !subject?.concealed) {
		verbs.push({
			id: 'similar',
			label: 'Similar to this',
			icon: 'image_search',
			group: 'open',
			singleOnly: true,
			run: handlers.similar
		});
	}

	verbs.push({
		id: 'link',
		label: 'Copy link',
		icon: 'link',
		group: 'keep',
		singleOnly: true,
		run: handlers.link
	});

	if (canSave && !subject?.concealed) {
		verbs.push({
			id: 'save',
			label: saving.label(subject?.media_type ?? 'video', count),
			icon: saving.icon(subject?.media_type ?? 'video', count),
			group: 'keep',
			run: handlers.save
		});
	}
	return verbs;
}

function diskVerbs(context: FileVerbsContext): Verb[] {
	const { isAdmin, handlers } = context;
	const verbs: Verb[] = [];
	if (isAdmin && context.canMove) {
		verbs.push({
			id: 'move',
			label: 'Move',
			icon: 'drive_file_move',
			group: 'change',
			run: handlers.move
		});
		verbs.push({
			id: 'rename',
			label: 'Rename',
			icon: 'edit_square',
			group: 'change',
			run: handlers.rename
		});
	}
	return verbs;
}

/** Each lands a new file, so greyed with the reason where nothing is writable. */
function changeVerbs(context: FileVerbsContext): Verb[] {
	const { isAdmin, subject, count, handlers } = context;
	const verbs: Verb[] = [];
	if (isAdmin) {
		const refused = context.canCompress ? {} : { disabled: true, why: NO_WRITABLE_FOLDER };
		const one = count === 1 && !!subject;
		const clips = clipEditsOffered();
		if (editable(subject, count) && (subject?.media_type !== 'video' || clips)) {
			verbs.push({
				id: 'edit',
				label: editLabel(subject?.media_type),
				icon: subject?.media_type === 'video' ? 'content_cut' : 'design_services',
				group: 'change',
				singleOnly: true,
				run: handlers.edit,
				...refused
			});
		}
		if (handlers.gif && editable(subject, count) && subject?.media_type === 'video' && clips) {
			verbs.push({
				id: 'gif',
				label: gifLabel,
				icon: 'gif_box',
				group: 'change',
				singleOnly: true,
				run: handlers.gif,
				...refused
			});
		}
		if (!one || subject?.media_type !== 'image') {
			verbs.push({
				id: 'compress',
				label: compressLabel,
				icon: 'compress',
				group: 'change',
				run: handlers.compress,
				...refused
			});
		}
	}
	return verbs;
}

function autoEnrichChildren(context: FileVerbsContext): Verb[] {
	const { enrichBoxes = [], handlers } = context;
	const autoEnriching = handlers.autoEnrich;
	const lookingUp = handlers.lookUpSongs;
	const songRow =
		context.songLookup && lookingUp
			? songLookupRow(context.songLookup, (ids) => lookingUp(ids))
			: null;
	const askingAgain = handlers.lookUpSongsAgain;
	const againRow =
		songRow && context.songLookup && askingAgain
			? songAgainRow(context.songLookup, (ids) => askingAgain(ids))
			: null;
	return [
		...(enrichBoxes.length > 0 || songRow
			? autoEnrichRows(enrichBoxes, (ids, box) => autoEnriching(ids, box))
			: []),
		...(songRow ? [songRow] : []),
		...(againRow ? [againRow] : [])
	];
}

/** An admin's: it sends a fingerprint out. */
function enrichVerbs(context: FileVerbsContext): Verb[] {
	const {
		isAdmin,
		keptLocal = false,
		keptFromSwaps = false,
		enrichRefused = false,
		enrichWhy,
		handlers
	} = context;
	const verbs: Verb[] = [];
	if (isAdmin) {
		const autoEnriching = handlers.autoEnrich;
		const refused = enrichRefused
			? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) }
			: {};
		const autoChildren = autoEnrichChildren(context);
		verbs.push({
			id: 'auto-enrich',
			label: 'Auto-enrich',
			icon: 'auto_fix_high',
			group: 'enrich',
			primary: true,
			...refused,
			...(context.lastEnriched ? { note: context.lastEnriched } : {}),
			...(autoChildren.length > 0 && !enrichRefused
				? { children: autoChildren }
				: { run: (ids: string[]) => autoEnriching(ids) })
		});
		const enriching = handlers.enrich;
		verbs.push({
			id: 'enrich',
			label: 'Enrich',
			icon: 'backlight_low',
			group: 'enrich',
			primary: true,
			...refused,
			run: (ids: string[]) => enriching(ids)
		});

		const keeping = handlers.keepLocal;
		if (keeping) {
			verbs.push({
				id: 'keep-local',
				label: keptLocal ? 'Allow enrichment' : "Don't enrich",
				icon: keptLocal ? 'public' : 'shield',
				group: 'enrich',
				run: (ids: string[]) => keeping(ids, !keptLocal)
			});
		}

		const keepingOut = handlers.keepFromSwaps;
		if (keepingOut) {
			verbs.push({
				id: 'keep-from-swaps',
				label: keptFromSwaps ? 'Allow swapping' : "Don't swap",
				icon: keptFromSwaps ? 'swap_horiz' : 'do_not_disturb_on',
				group: 'enrich',
				run: (ids: string[]) => keepingOut(ids, !keptFromSwaps)
			});
		}
	}
	return verbs;
}

function runTaskVerbs(context: FileVerbsContext): Verb[] {
	const { isAdmin, handlers } = context;
	const verbs: Verb[] = [];
	if (isAdmin && handlers.runNow && context.runGroups) {
		const running = runNowVerb(context.runGroups, handlers.runNow);
		if (running) verbs.push(running);
	}
	return verbs;
}

function shareVerbs(context: FileVerbsContext): Verb[] {
	const { isAdmin, handlers } = context;
	const verbs: Verb[] = [];
	if (isAdmin) {
		verbs.push({
			id: 'share',
			label: 'Share',
			icon: 'group',
			filled: true,
			group: 'share',
			run: handlers.share
		});
	}

	if (isAdmin) {
		verbs.push({
			id: 'visibility',
			label: 'Visibility',
			icon: 'policy',
			group: 'share',
			singleOnly: true,
			run: handlers.visibility
		});
	}
	return verbs;
}

function hideVerbs(context: FileVerbsContext): Verb[] {
	const { showingHidden, allHidden = false, allLocked = false, handlers } = context;
	const verbs: Verb[] = [];
	const unhides = showingHidden || allHidden;
	if (allLocked && !showingHidden) {
		const { unlock } = handlers;
		if (unlock) {
			verbs.push({
				id: 'unlock',
				label: 'Unlock',
				icon: 'lock',
				group: 'share',
				run: () => unlock()
			});
		}
	} else {
		verbs.push({
			id: 'hide',
			label: unhides ? 'Unhide' : 'Hide',
			icon: unhides ? 'visibility' : 'visibility_off',
			group: 'share',
			run: handlers.hide
		});
	}
	return verbs;
}

/** The id stays `delete`. */
function removeVerbs(context: FileVerbsContext): Verb[] {
	const { isAdmin, handlers } = context;
	const verbs: Verb[] = [];
	if (isAdmin) {
		verbs.push({
			id: 'delete',
			label: 'Remove',
			icon: 'delete',
			destructive: true,
			run: handlers.remove
		});
	}
	return verbs;
}

/** Not a GIF, whose frames depend on the one before. */
function editable(subject: FileVerbsContext['subject'], count: number): boolean {
	return count === 1 && !!subject && subject.media_type !== 'gif';
}

/** A door left empty goes with its last child. */
export function withoutVerbs(verbs: readonly Verb[], gone: readonly string[]): Verb[] {
	if (gone.length === 0) return [...verbs];
	return verbs.flatMap((verb) => {
		if (gone.includes(verb.id)) return [];
		if (verb.children === undefined) return [verb];
		const children = withoutVerbs(verb.children, gone);
		return children.length > 0 ? [{ ...verb, children }] : [];
	});
}
