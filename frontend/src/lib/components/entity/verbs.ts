/*
 * The things that can be done to a person, a Site, a collection or a tag: the sibling of the file
 * list, drawn by the same two renderers. A verb with no handler is not offered.
 */

import {
	addToVerb,
	favoriteVerb,
	pinVerb,
	ratingVerb,
	type Verb,
	type VerbPick
} from '$lib/components/common/verbs';
import { autoEnrichRows } from '$lib/entity/enrichment.svelte';

export interface EntityVerbHandlers {
	/** One at a time, always. */
	rename?: (ids: string[]) => void;
	/** The tags the Tag row opens out into. Absent where the thing cannot carry a tag. */
	tag?: VerbPick;
	favorite?: (ids: string[]) => void;
	/** Takes the target state: toggling each row of a mixed pick would leave it more mixed. */
	pin?: (ids: string[], pinned: boolean) => void;
	rate?: (ids: string[], rating: number | null) => void;
	share?: (ids: string[]) => void;
	/** One at a time: forty things have forty answers to who can see them. */
	visibility?: (ids: string[]) => void;
	/**
	 * Auto-enrich. Safe over a selection: only a single exact match is written, anything else waits
	 * under Organize. Absent where no stash-box knows the kind, as for a collection.
	 */
	enrich?: (ids: string[], box?: string) => void;
	/** Enrich: the same question with the answers on screen for a person to choose. One at a time. */
	lookUp?: (ids: string[]) => void;
	/** Takes the target state, as `pin` does. */
	keepLocal?: (ids: string[], kept: boolean) => void;
	hide?: (ids: string[], hide: boolean) => void;
	/** One row folds into somebody picked next; several fold into one of themselves. */
	merge?: (ids: string[]) => void;
	remove?: (ids: string[]) => void;
}

interface EntityVerbsContext {
	isAdmin: boolean;
	/** Whether the wall is showing hidden rows, so the verb says Unhide there. */
	showingHidden?: boolean;
	/** The rating everything being acted on shares, or null when they do not share one. */
	rating?: number | null;
	/** Whether everything is ALREADY a favorite; mixed counts as not. */
	favorite?: boolean;
	/** Whether everything is ALREADY pinned; mixed counts as not, so the press pins the lot. */
	pinned?: boolean;
	/** Whether everything is ALREADY kept local; mixed counts as not, the direction that sends nothing. */
	keptLocal?: boolean;
	/**
	 * Whether anything about these may leave the machine, so the Enrich rows are drawn refused. The
	 * same as `keptLocal` for an entity; its own field because the file menu is handed two answers.
	 */
	enrichRefused?: boolean;
	/** Why, in the words the row says under itself. See `Verb.why`. */
	enrichWhy?: string;
	/** When this was last enriched, already worded; handed over only for one row. */
	lastEnriched?: string;
	/** The server's configured stash-boxes, for the Auto-enrich flyout; empty leaves a plain press. */
	enrichBoxes?: readonly { word: string; name: string }[];
	handlers: EntityVerbHandlers;
}

/** The verbs a set of rows is offered, by family: each family builds its rows in the order drawn. */
type EntityVerbFamily = (context: EntityVerbsContext) => Verb[];

/**
 * Every verb that applies, each in the group of the menu it is drawn in. The groups and the rows
 * shared with the file list come from the same builders, so both menus read alike.
 */
export function entityVerbs(context: EntityVerbsContext): Verb[] {
	// No Open: a card is a link, and a row could only repeat the click.
	const families: EntityVerbFamily[] = [
		renameVerbs,
		placeVerbs,
		ownVerbs,
		enrichVerbs,
		shareVerbs,
		hideVerbs,
		mergeVerbs,
		deleteVerbs
	];
	return families.flatMap((family) => family(context));
}

/** Rename, an admin's: a name here is shared vocabulary. One at a time. */
function renameVerbs({ isAdmin, handlers }: EntityVerbsContext): Verb[] {
	const verbs: Verb[] = [];
	if (isAdmin && handlers.rename) {
		verbs.push({
			id: 'rename',
			label: 'Rename',
			icon: 'edit_square',
			group: 'change',
			singleOnly: true,
			run: handlers.rename
		});
	}
	return verbs;
}

/**
 * Tag and the heart behind the Add to door a file's menu opens with. The door is drawn only when it
 * holds two rows: a guest, or a tag, gets the heart as a row of its own.
 */
function placeVerbs(context: EntityVerbsContext): Verb[] {
	const { isAdmin, favorite = false, handlers } = context;
	const verbs: Verb[] = [];
	const tagging = isAdmin ? handlers.tag : undefined;
	const insideAddTo = (tagging ? 1 : 0) + (handlers.favorite ? 1 : 0) > 1;
	const places: Verb[] = [];
	if (tagging) {
		places.push({
			id: 'tag',
			label: 'Tag',
			icon: 'shoppingmode',
			// A row of its own only where there is no door: then it is the one the bar names.
			...(insideAddTo ? {} : { group: 'file' as const, primary: true }),
			pick: tagging
		});
	}
	if (handlers.favorite) places.push(favoriteVerb(favorite, handlers.favorite, insideAddTo));
	if (insideAddTo) verbs.push(addToVerb(places));
	else verbs.push(...places);
	return verbs;
}

/** The pin and the stars: this account's own, like the heart. */
function ownVerbs(context: EntityVerbsContext): Verb[] {
	const { pinned = false, handlers } = context;
	const verbs: Verb[] = [];
	if (handlers.pin) verbs.push(pinVerb(pinned, handlers.pin));
	if (handlers.rate) verbs.push(ratingVerb(context.rating ?? null, handlers.rate));
	return verbs;
}

/** Auto-enrich, Enrich and Don't enrich: an admin's, since a name is sent to another service. */
function enrichVerbs(context: EntityVerbsContext): Verb[] {
	const {
		isAdmin,
		keptLocal = false,
		enrichRefused = false,
		enrichWhy,
		lastEnriched,
		enrichBoxes = [],
		handlers
	} = context;
	const verbs: Verb[] = [];
	if (isAdmin && handlers.enrich) {
		const enriching = handlers.enrich;
		verbs.push({
			id: 'auto-enrich',
			label: 'Auto-enrich',
			icon: 'auto_fix_high',
			group: 'enrich',
			// Named even before the boxes load, so the bar does not change shape.
			primary: true,
			// A refused parent refuses its flyout too, or the press would happen one level down.
			...(enrichRefused ? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) } : {}),
			...(lastEnriched ? { note: lastEnriched } : {}),
			/* A plain press sends no box, which the server reads as the Settings choice. */
			...(enrichBoxes.length > 0 && !enrichRefused
				? { children: autoEnrichRows(enrichBoxes, (ids, box) => enriching(ids, box)) }
				: { run: (ids: string[]) => enriching(ids) })
		});
	}

	// The two entity pages declare this verb by hand; `design/one-glyph-per-verb.test.ts` keeps
	// their glyph the same as this one.
	if (isAdmin && handlers.lookUp) {
		verbs.push({
			id: 'enrich',
			label: 'Enrich',
			icon: 'backlight_low',
			group: 'enrich',
			primary: true,
			...(enrichRefused ? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) } : {}),
			...(lastEnriched ? { note: lastEnriched } : {}),
			run: handlers.lookUp
		});
	}

	/* An admin's: recorded on the row, and every pass in the installation reads it. */
	if (isAdmin && handlers.keepLocal) {
		const keeping = handlers.keepLocal;
		verbs.push({
			id: 'keep-local',
			label: keptLocal ? 'Allow enrichment' : "Don't enrich",
			icon: keptLocal ? 'public' : 'shield',
			group: 'enrich',
			run: (ids) => keeping(ids, !keptLocal)
		});
	}
	return verbs;
}

/** Share, and Visibility beside it: who can reach this one, however that was arranged. */
function shareVerbs({ isAdmin, handlers }: EntityVerbsContext): Verb[] {
	const verbs: Verb[] = [];
	if (isAdmin && handlers.share) {
		verbs.push({
			id: 'share',
			label: 'Share',
			icon: 'group',
			filled: true,
			group: 'share',
			run: handlers.share
		});
	}

	if (isAdmin && handlers.visibility) {
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

/** Hide, every account's: it changes only the view of whoever asked. */
function hideVerbs(context: EntityVerbsContext): Verb[] {
	const { showingHidden = false, handlers } = context;
	const verbs: Verb[] = [];
	if (handlers.hide) {
		const hiding = handlers.hide;
		verbs.push({
			id: 'hide',
			label: showingHidden ? 'Unhide' : 'Hide',
			icon: showingHidden ? 'visibility' : 'visibility_off',
			group: 'share',
			run: (ids) => hiding(ids, !showingHidden)
		});
	}
	return verbs;
}

/**
 * Merge, an admin's, and not `singleOnly`: two rows just noticed to be one must reach it from the
 * bar. It cannot be taken back, so the press asks which survives.
 */
function mergeVerbs({ isAdmin, handlers }: EntityVerbsContext): Verb[] {
	const verbs: Verb[] = [];
	if (isAdmin && handlers.merge) {
		verbs.push({
			id: 'merge',
			label: 'Merge',
			icon: 'merge',
			group: 'change',
			primary: true,
			run: handlers.merge
		});
	}
	return verbs;
}

function deleteVerbs({ isAdmin, handlers }: EntityVerbsContext): Verb[] {
	const verbs: Verb[] = [];
	if (isAdmin && handlers.remove) {
		verbs.push({
			id: 'delete',
			label: 'Delete',
			icon: 'delete',
			destructive: true,
			run: handlers.remove
		});
	}
	return verbs;
}
