/*
 * The things that can be done to a person, a Site, a collection or a tag.
 *
 * The sibling of the file list, against the same `Verb` type and the same two renderers, so a verb
 * cannot exist in a wall's right-click menu and not its bar.
 *
 * The walls differ by which handlers they pass rather than by each writing its own markup: a verb
 * with no handler is not offered. A tag cannot carry a tag, so that wall hands nothing over for it.
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
	/** Change its name. One at a time, always. */
	rename?: (ids: string[]) => void;
	/**
	 * The list of tags the Tag row opens out into, one pick writing to every one of them. Absent
	 * where the thing cannot carry a tag. The same list on the bar and the menu, as a file's Add to
	 * has: there is no sheet beside it.
	 */
	tag?: VerbPick;
	/** Make them all favorites. Absent on a wall whose rows cannot be hearted. */
	favorite?: (ids: string[]) => void;
	/**
	 * Keep them at the top of this wall, or take the pin out.
	 *
	 * Takes what it should become rather than toggling each row, which is what the hide verb below
	 * does and for the same reason: over a selection where some are pinned and some are not, a
	 * toggle-each would leave the selection MORE mixed than it started, and there is no sentence
	 * that describes what the row would do.
	 */
	pin?: (ids: string[], pinned: boolean) => void;
	/** Set the stars on all of them. Absent alongside `favorite`, for the same reason. */
	rate?: (ids: string[], rating: number | null) => void;
	share?: (ids: string[]) => void;
	/**
	 * Report who can actually reach this one, and through what.
	 *
	 * One at a time, like Rename, and for a reason of its own rather than for room: forty things
	 * have forty different answers to "who can see this and how", so a report over a selection
	 * would have to pick one of them or collapse them, and either is a sentence about a set that is
	 * not true of its members.
	 */
	visibility?: (ids: string[]) => void;
	/**
	 * Ask the stash-boxes about these and keep what comes back. Absent on a wall whose rows no
	 * stash-box knows about: a collection is somebody's own grouping and no stash-box has one.
	 *
	 * It decides nothing. Where exactly one entry across every switched-on stash-box matches every
	 * word of the name, the link is kept and blanks are filled by the same per-field rules the
	 * reconcile screen uses; where two answer to the name, nothing is written and the subject waits
	 * under Organize for somebody to choose. So it is safe over a selection of forty, which is the
	 * whole reason it is a verb rather than a button on one page.
	 */
	enrich?: (ids: string[], box?: string) => void;
	/**
	 * Ask the stash-boxes and let a PERSON choose. The sister of the verb above, and the place the
	 * one above sends anything it declined to decide.
	 *
	 * Auto-enrich is the half with no judgement in it: exactly one match, or nothing happens. That
	 * is the right rule and it is also why it can look broken: a Site with twenty possible
	 * matches produces a finished job and an unchanged record, which reads as a button that does
	 * nothing rather than as one that correctly refused to guess. This is the other half: the same
	 * question, with the answers on screen and the choice left to whoever knows which one their
	 * files are of.
	 *
	 * One at a time, like Rename. The chooser is a conversation about ONE subject: a sheet
	 * showing twenty candidates for each of forty people is not a screen anybody can use, and the
	 * verb that IS safe across forty is the one above.
	 */
	lookUp?: (ids: string[]) => void;
	/**
	 * Keep these out of every stash-box, for ever, or let them be enriched again.
	 *
	 * Takes what it should BECOME rather than toggling each row, which is what the pin and the hide
	 * verbs above already do and for the same reason: over a selection where some are kept local and
	 * some are not, a toggle-each would leave the selection more mixed than it started and there is
	 * no sentence that describes what the row would do.
	 */
	keepLocal?: (ids: string[], kept: boolean) => void;
	hide?: (ids: string[], hide: boolean) => void;
	/**
	 * Fold these into one. Absent on a wall whose rows cannot turn out to be each other.
	 *
	 * Takes the whole selection rather than a pair, because both cases are the same act: one row
	 * means "fold this into somebody I am about to pick", and several mean "fold these into one of
	 * themselves". Which one survives is asked either way: a merge cannot be taken back, so the
	 * one thing that must never be inferred is which of them is kept.
	 */
	merge?: (ids: string[]) => void;
	remove?: (ids: string[]) => void;
}

interface EntityVerbsContext {
	isAdmin: boolean;
	/**
	 * Whether the wall is currently showing hidden rows, so the verb says Unhide there.
	 *
	 * The same rule the file grid follows: inside Hidden, offering to Hide again is offering to do
	 * nothing, twice.
	 */
	showingHidden?: boolean;
	/** The rating everything being acted on shares, or null when they do not share one. */
	rating?: number | null;
	/**
	 * Whether everything being acted on is ALREADY a favorite, so the heart's row says the other
	 * thing. Mixed counts as not, so the press over a mixed pick hearts the lot, which is what the
	 * handler does with it (`favoriteAll`).
	 */
	favorite?: boolean;
	/**
	 * Whether everything being acted on is ALREADY pinned, so the row says the other thing.
	 *
	 * The same question the hidden flag below answers, asked of the rows rather than of the wall:
	 * a wall knows whether it is showing hidden things, and nothing knows whether these particular
	 * rows are pinned except whoever is holding them. Mixed counts as not pinned, so the verb over
	 * a mixed selection pins the lot, which is the reading that leaves the selection in a state
	 * somebody can describe.
	 */
	pinned?: boolean;
	/**
	 * Whether everything being acted on is ALREADY kept local, so the verb says the other thing.
	 *
	 * Mixed counts as NOT kept local, so the verb over a mixed selection keeps the lot local. That
	 * is the opposite reading from the pin's, which takes the same shape, and the difference is
	 * deliberate: this is the direction that cannot send anything anywhere by accident.
	 */
	keptLocal?: boolean;
	/**
	 * Whether anything about these may leave the machine at all, so the two Enrich rows are drawn
	 * refused rather than refused on the press.
	 *
	 * For an entity it is the row's own switch and nothing else (nothing is filed under a tag but
	 * files, and a person is not inside a Site), so it and `keptLocal` say the same thing here. It
	 * stays its own field because the file menu beside this one is handed two different answers,
	 * and one context read two ways is how the two surfaces come apart. See `FileVerbHandlers`.
	 */
	enrichRefused?: boolean;
	/** Why, in the words the row says under itself. See `Verb.why`. */
	enrichWhy?: string;
	/**
	 * When this was last enriched, already worded. See `$lib/entity/enrichment`. Undefined draws no line.
	 *
	 * One at a time in practice, because it is a fact about one row: a menu opened on forty has
	 * forty answers, so the surface hands it over only where it opened on one.
	 */
	lastEnriched?: string;
	/**
	 * The stash-boxes this install has configured, as `(word, name)`, for the Auto-enrich flyout.
	 *
	 * Empty or absent leaves Auto-enrich the plain press it has always been, which is what every
	 * surface that has not been given the list still gets. The list is the SERVER'S (the boxes
	 * actually configured here) and never three names written into this file: two installs have
	 * different boxes, and a menu offering one that is not there is a menu that asks nobody.
	 */
	enrichBoxes?: readonly { word: string; name: string }[];
	handlers: EntityVerbHandlers;
}

/**
 * Every verb that applies, each in the group of the menu it is drawn in.
 *
 * The groups and their order are `menuGroups`'s, shared with the file list, so a wall's menu and a
 * file's menu come in the same parts in the same order, and the rows the two share are made by the
 * same builders (`addToVerb`, `favoriteVerb`, `pinVerb`, `ratingVerb`), so they open with the same
 * row and say the same words. The destructive one is last on its own because it is destructive,
 * not because it is pushed last.
 */
export function entityVerbs(context: EntityVerbsContext): Verb[] {
	const {
		isAdmin,
		showingHidden = false,
		pinned = false,
		favorite = false,
		keptLocal = false,
		enrichRefused = false,
		enrichWhy,
		lastEnriched,
		enrichBoxes = [],
		handlers
	} = context;
	const verbs: Verb[] = [];

	// No Open. A card is a link and clicking it goes there, so the row could only repeat what the
	// click already does, and the same reasoning removed it from the file list beside this one.

	// Renaming is an admin's: a name here is shared vocabulary, and changing it changes what
	// everybody else's screens and searches say. One at a time: forty things cannot share a name.
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

	/*
	 * WHERE THESE CAN BE PUT, behind the one door a file's menu opens with: Add to, holding a tag
	 * and the heart. The same door in the same place on both menus, so somebody who has learned
	 * where the heart is on a file finds it on a person.
	 *
	 * Tagging is an admin's (a tag is shared vocabulary); the heart is every account's own. The door
	 * is drawn only when it holds two rows: a door onto one row is a press for nothing, so a guest
	 * (or a tag, which carries no tag) gets the heart as a row of its own, worded as the whole
	 * instruction. The Tag row opens out into the list of tags on the bar and the menu alike, the
	 * same row a file's Add to grows, one pick writing to everything picked.
	 */
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

	// Keeping something at the top of its wall, and the stars: this account's own, like the heart,
	// so not an admin's, and the same rows the file menu carries.
	if (handlers.pin) verbs.push(pinVerb(pinned, handlers.pin));
	if (handlers.rate) verbs.push(ratingVerb(context.rating ?? null, handlers.rate));

	// Enriching is an admin's, like renaming: what a stash-box fills in is shared vocabulary, and
	// it sends a name to somebody else's service.
	if (isAdmin && handlers.enrich) {
		const enriching = handlers.enrich;
		/* WHICH BOX, as rows under the verb, where the surface handed the list over.
		 *
		 * Children rather than a `pick`, and the two were both considered. A pick is for choosing
		 * one of a THOUSAND things (a person, a collection) and it brings a search box with it.
		 * There are three stash-boxes. Rows under the verb are what "Add to" already does with five,
		 * they need nothing typed, and the bar draws them from the same declaration.
		 *
		 * "All stash-boxes" is a row rather than the parent being pressable, because a row that both
		 * opened out and did something would do it on the way past. See `Verb.children`. */
		verbs.push({
			id: 'auto-enrich',
			label: 'Auto-enrich',
			icon: 'auto_fix_high',
			group: 'enrich',
			/*
			 * Named on the bar beside Tag, never behind the three dots. The flyout makes it a named
			 * group where the boxes are known; this names it where they are not yet, so the bar
			 * does not change shape with a list still loading.
			 */
			primary: true,
			/* Refused on the row when nothing about this may leave, with the reason under it, and the
			   flyout goes with it, because a parent drawn refused whose children still ran would be the
			   same press one level down. */
			...(enrichRefused ? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) } : {}),
			...(lastEnriched ? { note: lastEnriched } : {}),
			/* The rows are the shared declaration, and every one hands its box on: "All stash-boxes"
			   sends `EVERY_BOX`, a box's row its word, and a plain press (no list) sends nothing,
			   which the server reads as the Settings choice. See `autoEnrichRows`. */
			...(enrichBoxes.length > 0 && !enrichRefused
				? { children: autoEnrichRows(enrichBoxes, (ids, box) => enriching(ids, box)) }
				: { run: (ids: string[]) => enriching(ids) })
		});
	}

	// And the one that asks the same question and lets somebody answer it. Immediately after the
	// automatic one, in both surfaces, because the pair is the point: when the first says there
	// were twenty matches, the thing to press next is right underneath it.
	//
	// The backlight (`backlight_low`) against the other's wand. The wand is Sift deciding; this is
	// asking and being shown the answers. The two entity pages declare this verb by hand;
	// `design/one-glyph-per-verb.test.ts` keeps their glyph the same as this one.
	if (isAdmin && handlers.lookUp) {
		verbs.push({
			id: 'enrich',
			label: 'Enrich',
			icon: 'backlight_low',
			group: 'enrich',
			// Named on the bar beside Auto-enrich, for the reason given there.
			primary: true,
			...(enrichRefused ? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) } : {}),
			...(lastEnriched ? { note: lastEnriched } : {}),
			run: handlers.lookUp
		});
	}

	/* KEPT LOCAL. Beside the two verbs it refuses, because that is the pair: press Enrich, decide
	   this one should never leave, and the row that says so is right underneath.

	   An admin's, like the two above it, and for a reason of its own rather than by association: it
	   is not one account's privacy preference. It is recorded on the row and every pass and every
	   press in the installation reads it.

	   The label reverses on something already kept local, exactly as Hide and Pin do, and the glyph
	   reverses with it: a row reading "Do not enrich" on something already refused is a row that
	   appears to do nothing. */
	if (isAdmin && handlers.keepLocal) {
		const keeping = handlers.keepLocal;
		verbs.push({
			id: 'keep-local',
			label: keptLocal ? 'Allow enrichment' : "Don't enrich",
			/* `shield` for the act of keeping it in, `public` for letting it out again. Both are in
			   `icons.ts` already, and neither is a new glyph: `public` is the app's own mark for a
			   Site, which is a thing out on the internet, and that is exactly what this row
			   offers to let the thing be described by. Nothing is minted for one menu row. */
			icon: keptLocal ? 'public' : 'shield',
			group: 'enrich',
			run: (ids) => keeping(ids, !keptLocal)
		});
	}

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

	// What the sharing above it comes to. Share is where a decision is MADE; this reports who can
	// reach the thing however the reach was arranged: a person is on a guest's wall because some
	// file of theirs is reachable, which nothing written on the person says. Beside Share because
	// the pair is the point: read the report, and the way to change what it says is right above it.
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

	// Hiding is every account's, not an admin's: it changes nobody's view but the view of whoever
	// asked. The two around it change the library for everybody.
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

	/*
	 * Folding people who turn out to be one. An admin's, like renaming: it changes what the library
	 * says is true rather than what one account thinks, and unlike everything else here it cannot
	 * be taken back, which the press itself guards by asking which of them survives. Beside Rename
	 * in the menu, because both change what a row IS.
	 *
	 * Not `singleOnly`: somebody who has selected the two rows they just noticed are the same
	 * person must reach it from the bar.
	 *
	 * `primary`: `barShape` names the primary rows and folds the rest behind More, and with one
	 * card selected Merge is the door somebody came to open. The flag is declared here beside the
	 * words, not as a case inside `barShape`, which is one rule for every wall. The bar names rows
	 * in the menu's order, so Merge is named beside Rename.
	 */
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
