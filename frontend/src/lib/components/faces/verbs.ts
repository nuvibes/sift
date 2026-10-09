/*
 * The things that can be done to a face, and to a pile of them: a crop is not the file, so the file
 * verbs are not inherited. Delete, not Remove, because the face ceases to exist; the id stays
 * `remove`.
 */

import type { Verb } from '$lib/components/common/verbs';
import type { IconName } from '$lib/design/icons';

/** Also named by the server (`queue.py`), held together by `test_declared_icons.py`. */
export const DISCARD_ICON: IconName = 'remove';

/** One sentence each, for the card's tooltip and the question each press asks; keyed by id. */
export const ONE_FACE_SAYS: Readonly<Record<'set-aside' | 'remove', string>> = {
	'set-aside': 'Move this face to Discarded. It stays listed there and can be restored.',
	remove: 'Delete this face from the file permanently. The file itself is untouched.'
};

export function oneFaceSays(id: string): string | undefined {
	return id === 'set-aside' || id === 'remove' ? ONE_FACE_SAYS[id] : undefined;
}

export interface FaceVerbHandlers {
	/** Only where the face has a pile: the rest of the faces Sift thinks match it. */
	showGroup?: (ids: string[]) => void;
	/** Opens the naming field. */
	name?: (ids: string[]) => void;
	/** Only where a name nobody has confirmed sits on the face, matched as well as suggested. */
	agree?: (ids: string[]) => void;
	undo?: (ids: string[]) => void;
	/** Unset where the faces carry different names. */
	who?: string;
	move?: (ids: string[]) => void;
	setAside?: (ids: string[]) => void;
	restore?: (ids: string[]) => void;
	/** Deletes the FACE; the file is not touched. */
	remove?: (ids: string[]) => void;
}

/** A verb with no handler is not offered. */
export function faceVerbs(handlers: FaceVerbHandlers): Verb[] {
	const verbs: Verb[] = [];

	// No Open: pressing a face opens its file. The group is a different destination.
	if (handlers.showGroup) {
		verbs.push({
			id: 'show-group',
			// The id is unchanged: code and tests address the row by it.
			label: 'Show the face group',
			icon: 'inbox',
			group: 'open',
			run: handlers.showGroup
		});
	}

	if (handlers.name) {
		verbs.push({
			id: 'name',
			label: 'Add as person',
			icon: 'person_add',
			group: 'file',
			primary: true,
			run: handlers.name
		});
	}
	// The words say what the press does to a name, and whose, so the undo is not read as Delete.
	if (handlers.agree) {
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
	// One row wearing two words, with the minus glyph: a discarded face is not hidden.
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

interface PileVerbHandlers {
	name?: (ids: string[]) => void;
	/** Offer the naming row dimmed, with where naming is done as its `why`. */
	nameElsewhere?: string;
	/** The stored status is still `ignored` (`queue.py`). */
	setAside?: (ids: string[]) => void;
	restore?: (ids: string[]) => void;
	remove?: (ids: string[]) => void;
}

/** A pile wall may hand over BOTH and dim one, so its bar keeps its shape across tabs. */
export function pileVerbs(handlers: PileVerbHandlers): Verb[] {
	const verbs: Verb[] = [];

	if (handlers.name || handlers.nameElsewhere) {
		verbs.push({
			id: 'name',
			label: 'Add as person',
			icon: 'person_add',
			group: 'file',
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
