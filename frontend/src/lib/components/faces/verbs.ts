/*
 * The things that can be done to a face, and to a pile of them.
 *
 * The third declared verb list, after the file list and the four entity walls, so every face
 * surface offers the same verbs in its bar and its right-click menu rather than each drawing its
 * own buttons.
 *
 * A face's verbs are the face's own and do not inherit the file verb list: a crop is not the file
 * it was cut from. A face can be named, agreed with, undone, moved to another grouping, discarded
 * or deleted, all through functions in `faces.svelte.ts`; the server needs nothing new.
 *
 * A pile is a different object with a shorter list, kept here because the two share the same three
 * answers about a grouping (ignore it, bring it back, take it out), which would otherwise live in
 * two places.
 *
 * Each verb names the group of the menu it is drawn in, as the file list's do: go to the grouping,
 * say who it is, change where it stands. Inside a group the rows are alphabetical, and Delete is
 * last and alone whatever order it is pushed in, because it is destructive (see `menuGroups`).
 *
 * Delete, not Remove: the words rule is Delete when the thing ceases to exist and Remove when only
 * a membership or setting changes, and this verb ends the face itself (the detection row and its
 * crop). Every confirmation names the face and says no file is deleted. The id stays `remove`, as
 * does the route it calls; an id is what code addresses a row by, and one that followed its label
 * could not be relied on.
 */

import type { Verb } from '$lib/components/common/verbs';
import type { IconName } from '$lib/design/icons';

/**
 * The glyph of the Discard act, wherever it is drawn: both verb lists below, and the Discarded tab,
 * whose icon the server names (`icon="remove"` in the faces slice's `queue.py`: Python cannot
 * import this, so that one line is the second spelling, and `tests/gates/test_declared_icons.py`
 * is what proves the client ships the glyph it names).
 */
export const DISCARD_ICON: IconName = 'remove';

/**
 * What Discard and Delete do to one face, in one sentence each.
 *
 * The single source for two surfaces that must say the same thing: the tooltip over the glyph on a
 * card under "Who is in this", and the consequence line of the question each press asks first.
 *
 * Keyed by the verb's `id`, not carried on the verb as `why` or `note`: `why` is the reason a
 * disabled verb cannot be pressed, and the menus draw it whenever a verb is disabled
 * (`VerbMenuItems` `noteOf`), which every face verb is while a request is in flight; `note` is a
 * second line under a menu row about the subject. A field borrowed for a second meaning is read in
 * its first.
 *
 * About one face, so only the card that addresses one face reads it; a wall acting on a selection
 * words its own question with the count.
 */
export const ONE_FACE_SAYS: Readonly<Record<'set-aside' | 'remove', string>> = {
	'set-aside': 'Move this face to Discarded. It stays listed there and can be restored.',
	remove: 'Delete this face from the file permanently. The file itself is untouched.'
};

/** The sentence for one face's verb, or nothing for a verb that has none. */
export function oneFaceSays(id: string): string | undefined {
	return id === 'set-aside' || id === 'remove' ? ONE_FACE_SAYS[id] : undefined;
}

export interface FaceVerbHandlers {
	/**
	 * Open the group of look-alike faces this one is waiting in.
	 *
	 * Offered only where the face HAS one: unnamed, and in a pile that still exists. It is the
	 * step before naming rather than an alternative to it: a face on a file is one appearance, and
	 * what somebody wants before saying who it is, is the rest of the faces Sift thinks match it.
	 */
	showGroup?: (ids: string[]) => void;
	/**
	 * Say who these faces are.
	 *
	 * Opens the naming field rather than naming anything, because who they are is a question with
	 * no answer in a menu. Absent on a surface where the faces already have a name.
	 */
	name?: (ids: string[]) => void;
	/**
	 * Agree with what Sift decided about these.
	 *
	 * Only where there is a decision to agree with: a face carrying a name nobody has confirmed,
	 * whether suggested or matched outright. A match is Sift putting a name on a file without being
	 * asked, so it is the decision most worth agreeing with. A face already confirmed has nothing
	 * to agree to.
	 */
	agree?: (ids: string[]) => void;
	/**
	 * Take back an attribution that was agreed with.
	 *
	 * The exact reverse of the row above, and the pair is why both are here rather than one being
	 * a button somewhere: a screen that can agree and cannot un-agree is a screen somebody stops
	 * pressing.
	 */
	undo?: (ids: string[]) => void;
	/**
	 * Who `undo` takes off, so the row can say so: "Remove Cassia Lynn from this". Unset where the
	 * faces acted on carry different names, and then the row names the act rather than a person.
	 */
	who?: string;
	/** Into another grouping, or into one of their own. Both are the same question about where. */
	move?: (ids: string[]) => void;
	/** Out of the way, without saying they are wrong. Reversible by `restore`. */
	setAside?: (ids: string[]) => void;
	/** Back out of Discarded. Offered instead of `setAside`, never beside it. */
	restore?: (ids: string[]) => void;
	/**
	 * Delete these faces.
	 *
	 * The only destructive verb here, and what it destroys is the FACE: the crop and the row that
	 * says somebody was found at that moment. The file it was cut from is not touched, which is
	 * exactly why this list does not borrow the file verbs: the file's Delete ends a video, and this
	 * one ends a face; each confirmation names which.
	 */
	remove?: (ids: string[]) => void;
}

/**
 * Every face verb the surface handed a handler for, each in its menu group.
 *
 * A verb with no handler is not offered, which is how the three face walls differ from each other
 * without any of them writing markup: the pile can name and cannot agree, a person's identified
 * wall can agree and has nothing to name, and the strip under a file can do neither.
 */
export function faceVerbs(handlers: FaceVerbHandlers): Verb[] {
	const verbs: Verb[] = [];

	// No Open. Pressing a face already goes to the file at the moment it was found, on every
	// surface that draws one, and a menu row that repeats a plain click teaches people to open the
	// menu for things they did not need it for. The same call the file list makes, for the same
	// reason.
	//
	// The group is a DIFFERENT destination and no click reaches it, which is the whole of why the
	// rule above does not cover it: without this row the pile of faces Sift matched a face to
	// could only be found by going to Organize and recognising the crop.
	if (handlers.showGroup) {
		verbs.push({
			id: 'show-group',
			// "Show the face group", not "Show the group": the row is read beside verbs about a
			// person and a file. The id is unchanged, because the code and the tests address the
			// row by it and an id renamed to follow a label would leave a mutation case pointing at
			// nothing.
			label: 'Show the face group',
			// The Organize glyph, which is where the group is: a verb's mark says where the press
			// lands, and the rail spends this glyph on exactly that destination.
			icon: 'inbox',
			group: 'open',
			run: handlers.showGroup
		});
	}

	if (handlers.name) {
		// The verb the whole screen is for, so the bar names it rather than hiding it behind its
		// three dots. See `Verb.primary`.
		verbs.push({
			id: 'name',
			label: 'Add as person',
			icon: 'person_add',
			group: 'file',
			primary: true,
			run: handlers.name
		});
	}
	// The two words say what the press does to a name, which a one-word "Agree" or "Undo" over a
	// crop of somebody's face does not. Confirm is saying yes to what Sift suggested (leaving it
	// Confirmed), and the undo is the name leaving a face that stays. It says whose name and
	// from what ("Remove Cassia Lynn from this"), which is what keeps it from reading as the face's
	// own Delete one row below: the person is named, and "from this" says the face stays.
	if (handlers.agree) {
		// Named for the same reason `name` is: on the wall that offers it, agreeing IS the work.
		verbs.push({
			id: 'agree',
			label: 'Confirm name',
			icon: 'check',
			group: 'file',
			primary: true,
			run: handlers.agree
		});
	}
	if (handlers.undo) {
		verbs.push({
			id: 'undo',
			label: handlers.who ? `Remove ${handlers.who} from this` : 'Remove the name from this',
			icon: 'person_remove',
			group: 'file',
			run: handlers.undo
		});
	}
	if (handlers.move) {
		verbs.push({
			id: 'move',
			label: 'Move them',
			icon: 'call_split',
			group: 'change',
			run: handlers.move
		});
	}
	// Discard and Restore are one row wearing two words, never two rows. A surface offers whichever
	// its faces can take: a wall of discarded faces cannot discard them again, and an open pile has
	// nothing to bring back.
	//
	// The glyph is `remove`, the minus, wherever the act is drawn (see `DISCARD_ICON`), not the
	// crossed-out eye, which is the hidden mark and the Hide verb everywhere else: a discarded face
	// is taken off the list of faces waiting for a name, not hidden from anybody.
	if (handlers.setAside) {
		verbs.push({
			id: 'set-aside',
			label: 'Discard',
			icon: DISCARD_ICON,
			group: 'change',
			run: handlers.setAside
		});
	}
	if (handlers.restore) {
		verbs.push({
			id: 'restore',
			label: 'Restore',
			icon: 'history',
			group: 'change',
			run: handlers.restore
		});
	}
	if (handlers.remove) {
		verbs.push({
			id: 'remove',
			label: 'Delete',
			icon: 'delete',
			filled: true,
			destructive: true,
			run: handlers.remove
		});
	}

	return verbs;
}

/* Not exported: every caller hands `pileVerbs` a literal, and a name nothing outside this file
   reads is a claim to be shareable that was never made good. `FaceVerbHandlers` beside it IS
   exported, because the surfaces annotate their handler objects with it. */
interface PileVerbHandlers {
	/** Say who a whole group is. */
	name?: (ids: string[]) => void;
	/**
	 * Offer the naming row and leave it dim, saying where the naming is actually done.
	 *
	 * Naming needs a name typed against ONE group, so the wall's field lives on a card rather than
	 * in a bar addressed at a selection. Dropping the row instead would read as a feature that does
	 * not exist: these are the words that say otherwise, and they are the row's `why`. Ignored
	 * when a real handler is given.
	 */
	nameElsewhere?: string;
	/**
	 * Out of the wall of open groups. Discard is the word the whole app uses for it; the stored
	 * status is still `ignored` (see the faces slice's `queue.py`).
	 */
	setAside?: (ids: string[]) => void;
	/** Back onto it. A wall may offer BOTH and dim the one that does not apply. See below. */
	restore?: (ids: string[]) => void;
	/** Delete every face in the grouping, and the grouping with them. */
	remove?: (ids: string[]) => void;
}

/**
 * Every pile verb the wall handed a handler for, in the same groups a face's verbs are drawn in.
 *
 * The same answers a face has about a grouping, addressed at the grouping instead, so the words
 * match the ones on the faces inside it, which is the whole reason this lives beside them.
 *
 * Unlike a face, a pile wall may hand over BOTH `setAside` and `restore` and dim whichever does not
 * apply to the tab it is on. That is the wall's own decision and a good one: its bar must not change
 * shape under somebody switching tabs, because a control that is missing reads as a feature that
 * does not exist where a greyed one reads as one that does not apply to what is picked.
 */
export function pileVerbs(handlers: PileVerbHandlers): Verb[] {
	const verbs: Verb[] = [];

	if (handlers.name || handlers.nameElsewhere) {
		verbs.push({
			id: 'name',
			label: 'Add as person',
			icon: 'person_add',
			group: 'file',
			// Named even while it is dim, because the words ARE the answer to "where do I do this".
			primary: true,
			run: handlers.name,
			disabled: handlers.name === undefined,
			why: handlers.name === undefined ? handlers.nameElsewhere : undefined
		});
	}
	if (handlers.setAside) {
		verbs.push({
			id: 'set-aside',
			label: 'Discard',
			icon: DISCARD_ICON,
			group: 'change',
			run: handlers.setAside
		});
	}
	if (handlers.restore) {
		verbs.push({
			id: 'restore',
			label: 'Restore',
			icon: 'history',
			group: 'change',
			run: handlers.restore
		});
	}
	if (handlers.remove) {
		verbs.push({
			id: 'remove',
			label: 'Delete',
			icon: 'delete',
			filled: true,
			destructive: true,
			run: handlers.remove
		});
	}

	return verbs;
}
