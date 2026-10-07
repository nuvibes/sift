/*
 * The things that can be done to a FILE, declared once for both surfaces that offer them: which
 * verbs exist, who is offered them, what each is called. The shape and the bar-or-menu rule are
 * `$lib/components/common/verbs`.
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

/** What a verb is called in code. Stable, and what the drift test names them by. */
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

/** What the screen knows that decides which verbs apply and what they are called. */
interface FileVerbsContext {
	isAdmin: boolean;
	/** Whether this account may take copies out of Sift at all. */
	canSave: boolean;
	/** Whether the screen showing these is Hidden, where the hide verb points the other way. */
	showingHidden: boolean;
	/**
	 * Whether every file being acted on is ALREADY kept local, so the verb says the other thing;
	 * mixed reads as not (`EntityVerbHandlers.keepLocal`).
	 */
	keptLocal?: boolean;
	/** Whether every file being acted on is already kept out of swaps, so the verb says the other
	 *  thing. Mixed reads as not kept out, the direction `keptLocal` takes. */
	keptFromSwaps?: boolean;
	/**
	 * Whether anything about these files may leave the machine AT ALL: the whole rule (its own
	 * switch, or the Site, person or tag it is filed under), wider than `keptLocal`, which the row
	 * reverses on. Enrichment rows are drawn REFUSED rather than failing on the press.
	 */
	enrichRefused?: boolean;
	/** Why, in the words the row says under itself. See `Verb.why`. */
	enrichWhy?: string;
	/** When this file was last enriched, already worded. See `$lib/entity/enrichment`. One file only:
	 *  a menu over forty has forty answers, so the screen hands it over where it opened on one. */
	lastEnriched?: string;
	/**
	 * The stash-boxes this install has configured, as `(word, name)`, for the Auto-enrich flyout:
	 * the SERVER's list. Empty leaves a plain press that asks what Settings says.
	 */
	enrichBoxes?: readonly { word: string; name: string }[];
	/**
	 * The song lookup, as the server names it (`GET /music/lookup`), for its row under Auto-enrich;
	 * null draws none. Offered even when off, so the server's refusal says where to turn it on.
	 */
	songLookup?: { word: string; name: string } | null;
	/**
	 * The Importing stages and their per-file passes, for the Run task flyout: the server's list
	 * (`$lib/jobs/run-now`). Empty leaves Run task out: a door onto nothing is not a verb.
	 */
	runGroups?: readonly RunNowGroup[];
	/**
	 * Whether every file this verb will act on is ALREADY hidden: with the vault open hidden files
	 * sit on ordinary walls, so the screen alone would offer a Hide that does nothing.
	 */
	allHidden?: boolean;
	/**
	 * Whether every file this verb will act on is a LOCKED TILE (`concealed`): the placeholder says
	 * nothing about itself, Hide is false and Unhide needs the PIN (`slices/vault/router.py`), so
	 * the row offers the PIN, or nothing where the surface cannot ask for it.
	 */
	allLocked?: boolean;
	/**
	 * Whether every file this verb will act on is ALREADY a favorite, as `allHidden`: a set all one
	 * way has one answer.
	 */
	allFavorite?: boolean;
	/**
	 * Whether every file this verb will act on is ALREADY pinned; a MIXED set reads as not pinned and
	 * pins the lot, as on the entity walls.
	 */
	allPinned?: boolean;
	/**
	 * The one file a menu was opened on, when the surface has one, so a menu can say "Remove from
	 * favorites" while the bar over forty files says "Favorite".
	 */
	subject?: {
		media_type: string;
		favorite: boolean;
		/** Optional, beside `Actionable`'s own: a screen that never draws the pin does not carry
		 *  it, and the verb there simply never reads "Unpin". */
		pinned?: boolean;
		concealed: boolean;
	} | null;
	/** How many files the verbs will act on. Decides Save's wording, which differs for one. */
	count: number;
	/** The rating every file being acted on shares, or null when they do not share one. */
	rating?: number | null;
	/** Whether this screen can move files at all. False leaves Move out rather than greying it. */
	canMove?: boolean;
	/**
	 * Whether anywhere in this library can be written to at all, since compressing and editing
	 * write a new file beside the original. Apart from `canMove`, a different question with the same
	 * answer today.
	 */
	canCompress?: boolean;
	handlers: FileVerbHandlers;
}

/**
 * One function per verb, and one list per place a file can be put, all the screen's. The five
 * places are LISTS (`VerbPick`), declared as the list, so every surface that draws the row draws
 * the list.
 */
export interface FileVerbHandlers {
	tag: VerbPick;
	collect: VerbPick;
	assign: VerbPick;
	/**
	 * Say which Site these files came from, or take a wrong one back off the whole set: the
	 * flyout's cleared row is the same write told to remove.
	 */
	site: VerbPick;
	photoSet: VerbPick;
	/** Say which song these files are. A file carries one, so a pick moves it off any other. */
	song: VerbPick;
	favorite: (ids: string[]) => void;
	/**
	 * Keep these files at the top of whatever wall they are on, or take the pin off. OPTIONAL: an
	 * opt-IN, since a pin belongs only on curated walls. Takes the target state (`allPinned`).
	 */
	pin?: (ids: string[], pinned: boolean) => void;
	rate: (ids: string[], rating: number | null) => void;
	move: (ids: string[]) => void;
	/** Rename the files in one go, every new name shown before anything moves. */
	rename: (ids: string[]) => void;
	compress: (ids: string[]) => void;
	edit: (ids: string[]) => void;
	/**
	 * Open the editor straight into making a GIF out of this clip, a door that would otherwise be a
	 * switch inside Trim. OPTIONAL, as `pin`: absent rather than dead.
	 */
	gif?: (ids: string[]) => void;
	/**
	 * Open the Files wall filtered to the files similar to this one (`like:<id>`): the strip under
	 * a file at full length. OPTIONAL, the mechanism `gif` uses: a surface that cannot leave for a
	 * wall does not hand one in, and the row is absent rather than dead.
	 */
	similar?: (ids: string[]) => void;
	share: (ids: string[]) => void;
	/** Report who else can see this one, and through what. One file: see the verb. */
	visibility: (ids: string[]) => void;
	hide: (ids: string[]) => void;
	/** Ask for the PIN that opens the vault. OPTIONAL: where it is absent, a locked tile's menu has
	 *  no row about hiding at all rather than one that cannot be kept. See `allLocked`. */
	unlock?: () => void;
	save: (ids: string[]) => void;
	link: (ids: string[]) => void;
	/**
	 * AUTO-ENRICH: ask the stash-boxes what these FILES are (by their bytes), and accept what is
	 * certain. The press is the consent: an exact-hash match is written; anything less waits under
	 * Organize, and a differing field is a question there, never overwritten. `box` is the flyout's
	 * row (a box, `EVERY_BOX`, or Settings' choice).
	 */
	autoEnrich: (ids: string[], box?: string) => void;
	/**
	 * ENRICH: ask, and let a person choose. One file opens the chooser on it; several are asked
	 * about and every answer waits in the pile under Organize, exact or not, because this is the
	 * verb that decides nothing.
	 */
	enrich: (ids: string[]) => void;
	/**
	 * Ask AcoustID which song each of these files is: the song lookup pressed for these files.
	 * OPTIONAL, the mechanism `gif` uses: a surface that cannot press it draws no row.
	 */
	lookUpSongs?: (ids: string[]) => void;
	/**
	 * Ask AcoustID again about these files, where it did not know them: the lookup pressed with
	 * `again`. OPTIONAL, as `lookUpSongs` is, and drawn only beside that row.
	 */
	lookUpSongsAgain?: (ids: string[]) => void;
	/**
	 * Keep these out of every stash-box, for ever, or let them be enriched again: the target state;
	 * mixed keeps the lot local, the direction that sends nothing by accident.
	 */
	keepLocal?: (ids: string[], kept: boolean) => void;
	/**
	 * Keep these out of every swap with another Sift, or let them back in: the Visibility panel's
	 * "Don't swap" switch, as a row. OPTIONAL, the mechanism `keepLocal` uses: a surface that
	 * cannot write it draws no row.
	 */
	keepFromSwaps?: (ids: string[], kept: boolean) => void;
	/** Run one of Importing's per-file passes now, for these. See `$lib/jobs/run-now`. */
	runNow?: (ids: string[], run: string) => void;
	remove: (ids: string[]) => void;
}

/** The glyph each Importing stage's press already wears on the Importing pane, so "Generate now"
 *  on a file is the same mark as "Generate now" in Settings. */
const STAGE_ICON: Record<string, IconName> = {
	scan: 'split_scene',
	generate: 'error_med',
	identify: 'person'
};

/**
 * "Run task": Importing's own presses (Scan now, Generate now, Identify now), each opening onto its
 * every-pass press and the passes that can run for one file. One declaration, drawn by every
 * surface's renderers, in the server's words and the pane's glyphs (filled for every-pass). Null
 * where there is nothing to offer.
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
				/* "Identify all" first: the server names and expands it (`:all`), so it means exactly the
				   rows under it; its glyph is filled, chosen by the server's `every`, not by its words. */
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

/** How Save is worded and drawn, passed in so this module stays free of the copy-out rules. */
interface SaveWording {
	label: (mediaType: string, count: number) => string;
	icon: (mediaType: string, count: number) => IconName;
}

/**
 * What the one editing panel is called over this kind of file: a video is trimmed, a picture
 * MODIFIED, never "Edit", which half the app uses for renaming one row below. Written once for the
 * bar, the menu and the file's own page.
 */
export function editLabel(mediaType: string | undefined): string {
	return mediaType === 'video' ? 'Trim' : 'Modify';
}

/**
 * What the row that makes a GIF is called, written once for the list and the file's own screen.
 */
const gifLabel = 'Create GIF';

/** Why Edit, Create GIF and Compress are greyed: each writes a new file, and nowhere may be written. */
export const NO_WRITABLE_FOLDER = 'No writable folder';

/**
 * Whether cutting a clip (Trim) and making a GIF from one (Create GIF) are offered here: not at a
 * phone's width, where a finger covers the very frame it is choosing on the timeline. Asked by both
 * lists that offer them.
 */
export function clipEditsOffered(): boolean {
	return !phoneWidth.yes;
}

/** What the row that makes a smaller copy is called, written once for the reason `gifLabel` is: the
 *  declared list and the file's own screen both draw it. */
const compressLabel = 'Compress';

/**
 * Every verb that applies, each in the group of the menu it is drawn in (`menuGroups`), decided
 * once here so both surfaces offer a guest the same set.
 */
export function fileVerbs(context: FileVerbsContext, saving: SaveWording): Verb[] {
	const {
		isAdmin,
		canSave,
		showingHidden,
		allHidden = false,
		allLocked = false,
		allFavorite = false,
		allPinned = false,
		keptLocal = false,
		keptFromSwaps = false,
		enrichRefused = false,
		enrichWhy,
		enrichBoxes = [],
		subject,
		count,
		handlers
	} = context;
	const verbs: Verb[] = [];

	// No Open: clicking a tile does that, and a row repeating a click trains people into the menu.

	// Only a surface pointed at ONE file knows which way the heart will go; over a set (and on a
	// tile that is part of a selection, which passes no subject) it adds. See `AssetActions.favorite`.
	const favoriteAlready = Boolean(subject?.favorite || allFavorite);

	if (isAdmin) {
		// Tags, collections and people are shared vocabulary, so an admin's; the heart and the stars
		// are not. Tagging is inside "Add to": the rows there are the list itself, one hover away,
		// like the other four, carrying `pick` and no press, so the bar draws them as the menus do.
		// Read flat it is "Add to Tag". The rail's order. The door, heart, pin and stars come from the
		// one builder both menus call (`common/verbs.ts`); the door is `primary` (`barShape`).
		verbs.push(
			addToVerb([
				{ id: 'assign', label: 'Person', icon: 'person', pick: handlers.assign },
				{ id: 'site', label: 'Site', icon: 'public', pick: handlers.site },
				{ id: 'collect', label: 'Collection', icon: 'box', pick: handlers.collect },
				{ id: 'photo_set', label: 'Photo Set', icon: 'photo_library', pick: handlers.photoSet },
				{ id: 'tag', label: 'Tag', icon: 'shoppingmode', pick: handlers.tag },
				/* The song, after Tag, in the order the rail lists the Songs page. */
				{ id: 'song', label: 'Song', icon: 'music_note_2', pick: handlers.song },
				favoriteVerb(favoriteAlready, handlers.favorite, true)
			])
		);
	}

	// A guest gets the heart on its own: the rest is shared vocabulary. Alone, its words are whole.
	if (!isAdmin) {
		verbs.push(favoriteVerb(favoriteAlready, handlers.favorite, false));
	}

	/* Keeping a file at the top of its wall, beside the heart and the stars, as on the entity walls.
	   Only where the surface handed a handler in: a pin belongs to a curated wall. Its words reverse
	   on a pinned file, as Hide's do. */
	if (handlers.pin) {
		verbs.push(pinVerb(Boolean(subject?.pinned || allPinned), handlers.pin));
	}

	verbs.push(ratingVerb(context.rating ?? null, handlers.rate));

	/* The files similar to one file, under the strip's own name; never on a locked tile. */
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

	// Moving touches real files on a real disk, so it is offered only where the server has said the
	// files can be organized at all: most libraries are indexed read-only and could never take it.
	if (isAdmin && context.canMove) {
		// The folder with an arrow, as the explorer's "Move to...": a plain folder says WHERE a file is.
		verbs.push({
			id: 'move',
			label: 'Move',
			icon: 'drive_file_move',
			group: 'change',
			run: handlers.move
		});
		// Renaming is the same act on the same disk, so it is offered under the same condition.
		verbs.push({
			id: 'rename',
			label: 'Rename',
			icon: 'edit_square',
			group: 'change',
			run: handlers.rename
		});
	}

	// Changing the file: edit, Create GIF, Compress. An admin's, each landing a new file beside the
	// original, so each needs a writable folder. One table for every surface; where nothing is
	// writable they are drawn greyed with the reason. On one file: Edit, Create GIF (a video, under
	// Trim's conditions) and Compress; on a set, Compress alone. A GIF cannot be cut (`editable`).
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

	// Asking the stash-boxes what these files are: an admin's, since it sends a fingerprint out.
	// The same three rows on every surface: Auto-enrich, Enrich, Do not enrich, both `primary`,
	// named on the bar (`barShape`).
	if (isAdmin) {
		const autoEnriching = handlers.autoEnrich;
		/* REFUSED ON THE ROW, with the reason, and the flyout with it: the decision is known now. */
		const refused = enrichRefused
			? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) }
			: {};
		/* The boxes' rows, then AcoustID's, one more source, by the sound; its own route and switch. */
		const lookingUp = handlers.lookUpSongs;
		const songRow =
			context.songLookup && lookingUp
				? songLookupRow(context.songLookup, (ids) => lookingUp(ids))
				: null;
		/* And Ask again, right under it: the same lookup for a file AcoustID did not know. */
		const askingAgain = handlers.lookUpSongsAgain;
		const againRow =
			songRow && context.songLookup && askingAgain
				? songAgainRow(context.songLookup, (ids) => askingAgain(ids))
				: null;
		/* With no box listed the stash-boxes keep one row of their own (All stash-boxes) beside
		   AcoustID's, so the plain press is still there once the verb opens out. */
		const autoChildren = [
			...(enrichBoxes.length > 0 || songRow
				? autoEnrichRows(enrichBoxes, (ids, box) => autoEnriching(ids, box))
				: []),
			...(songRow ? [songRow] : []),
			...(againRow ? [againRow] : [])
		];
		verbs.push({
			id: 'auto-enrich',
			label: 'Auto-enrich',
			icon: 'auto_fix_high',
			group: 'enrich',
			primary: true,
			...refused,
			...(context.lastEnriched ? { note: context.lastEnriched } : {}),
			/* WHICH BOX, as rows (three, nothing typed); with no list a plain press asks Settings. */
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

		/* KEPT LOCAL, beside the verb it refuses: it stops what would leave, not what has gone
		   (`$lib/entity/enrichment`). Only where the surface handed a writer in. */
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

		/* KEPT OUT OF SWAPS, beside Don't enrich, in the Visibility panel's order and by its write. */
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

	/* RUN TASK, an admin's, as Importing is: real work whose findings everybody sees (`runNowVerb`). */
	if (isAdmin && handlers.runNow && context.runGroups) {
		const running = runNowVerb(context.runGroups, handlers.runNow);
		if (running) verbs.push(running);
	}

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

	/* Who can ACTUALLY reach this file, however it was arranged (a folder, a tag, a set, a network),
	   apart from Share, where a decision is made. `singleOnly`: forty files have forty answers. */
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

	// Hiding is every account's: it changes only the asking account's screen. Unhide on the Hidden
	// screen or when everything picked is hidden. A LOCKED TILE offers the PIN instead (`allLocked`).
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

	/* REMOVE, the word of the sheet's first answer (the file leaves Sift, stays on disk); Delete is
	   the second, named where offered. The id stays `delete`, which code and tests address. */
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

/**
 * Whether this surface is aimed at exactly one file the editor can do anything with: not a GIF,
 * whose frames each depend on the one before; Compress still works on one.
 */
function editable(subject: FileVerbsContext['subject'], count: number): boolean {
	return count === 1 && !!subject && subject.media_type !== 'gif';
}

/**
 * The verbs a screen offers once it has taken some away, inside doors too (Tag is inside Add to);
 * a door left empty goes with its last child.
 */
export function withoutVerbs(verbs: readonly Verb[], gone: readonly string[]): Verb[] {
	if (gone.length === 0) return [...verbs];
	return verbs.flatMap((verb) => {
		if (gone.includes(verb.id)) return [];
		if (verb.children === undefined) return [verb];
		const children = withoutVerbs(verb.children, gone);
		return children.length > 0 ? [{ ...verb, children }] : [];
	});
}
