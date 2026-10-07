import { ApiError } from '$lib/api/client';
import { announceSkipped, type BulkWriteDone } from '$lib/library/bulk';
import { libraryChanges } from '$lib/library/changes.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { place } from '$lib/components/common/toast-pieces';
import { counted } from '$lib/entity/entity-counts';
import { vaultPrompt } from '$lib/shell/vault.svelte';

/*
 * Putting a thing in the vault from a menu, wherever the menu is.
 *
 * A file, a folder, a person, a collection: four different endpoints and one gesture, and the parts
 * around the write are identical every time: the same refusal when no PIN has been set, the same
 * sentence afterwards, the same one press back. Written once here so a wall that grows a Hide item
 * gets all of that rather than the part of it a copy would remember.
 *
 * Nothing here decides anything. Concealment is a column the server reads inside the query that
 * hands a row back or does not, and every one of these calls is refused by that server if it should
 * be. What this owns is the sentence.
 */

/** How the caller performs the write for one id. Each kind of thing has its own endpoint. */
type SetVault = (id: string, vault: boolean) => Promise<void>;

/**
 * How the caller performs the write for a WHOLE selection, where its endpoint takes a list.
 *
 * Files have one; folders, people, collections, Sites, tags and photo sets do not, so `set`
 * above stays and the loop below stays with it. This is not a faster spelling of that loop. It is
 * a different request: one round trip, one transaction and one answer for the selection, instead of
 * one of each per file.
 */
type SetVaultMany = (ids: string[], vault: boolean) => Promise<BulkWriteDone>;

interface HideOptions {
	/** What these are, in a sentence: "file", "folder", "person", "collection". */
	noun: string;
	/** Plural of the above, when it is not the obvious one. */
	plural?: string;
	set: SetVault;
	/** The same write for the whole selection in one request, for the kinds whose endpoint takes a
	 *  list. Left out by a kind that has no such endpoint, which is every kind but files. */
	setMany?: SetVaultMany;
	/** Take a row off the screen immediately, before the list is re-read. */
	forget?: (id: string) => void;
	/**
	 * Whether what was hidden is still on screen afterwards.
	 *
	 * True with the vault open, where hiding a file marks it rather than removing it. It changes
	 * what there is to say: "unlock Hidden to see it" is wrong advice about something the person is
	 * looking at.
	 */
	stays?: boolean;
}

/**
 * A PIN has to exist before anything can go in, and this is what a refusal looks like.
 *
 * Concealing something with no PIN would lose it, so the server refuses, and this one failure says
 * what to do: the link opens the PIN form.
 */
const NO_PIN = [
	'Set a PIN in ',
	place('Profile', '/settings/profile#profile.pin'),
	" first. It's what unhides these again."
];

/* What the PIN prompt says it is for when an Undo raised it: the act waiting on the PIN, rather
   than "Show hidden items", which is the prompt's own reason and reads wrong here. */
const PUT_IT_BACK = 'Put it back';

/**
 * Hide, or unhide: the same action pointed the other way.
 *
 * Returns what actually moved, so a caller can clear a selection only when something did.
 *
 * Two ways of sending it and one sentence afterwards. A kind whose endpoint takes a LIST sends the
 * whole selection once (see `inOneRequest`), and a kind that has no such endpoint still goes one
 * at a time, stopping at the first failure rather than carrying on: the second call would fail the
 * same way, and a person does not need to be told six times.
 */
export async function setHidden(
	ids: string[],
	hide: boolean,
	options: HideOptions
): Promise<string[]> {
	const setMany = options.setMany;
	if (setMany) {
		const sent = await inOneRequest(ids, hide, setMany, options);
		return sent.length === 0 ? sent : announce(sent, hide, options);
	}
	const moved: string[] = [];
	for (const id of ids) {
		try {
			await options.set(id, hide);
			options.forget?.(id);
			moved.push(id);
		} catch (error) {
			refused(error, hide);
			break;
		}
	}
	if (moved.length === 0) return moved;
	return announce(moved, hide, options);
}

/**
 * The whole selection in ONE request, for a kind whose endpoint takes a list.
 *
 * Answers the ids that actually moved, which is either all of them or none, and that is the one
 * judgement here. A bulk reply says HOW MANY were left out and never WHICH, by design: naming the
 * rows a vault is concealing would undo the request in the act of confirming it. So a partial write
 * cannot take rows off the screen by id, and one re-read is the only thing that can be right; the
 * message beside it says how many were left out and offers the Unlock where that is the reason.
 */
async function inOneRequest(
	ids: string[],
	hide: boolean,
	setMany: SetVaultMany,
	options: HideOptions
): Promise<string[]> {
	let done: BulkWriteDone;
	try {
		done = await setMany(ids, hide);
	} catch (error) {
		refused(error, hide);
		return [];
	}
	if (done.skipped === 0) {
		for (const id of ids) options.forget?.(id);
		return [...ids];
	}
	announceSkipped(done, options.noun);
	// Some of it landed and this cannot say which, so nothing is edited in place and the wall reads
	// itself again. Silent when NOTHING landed: the message above has already said why.
	if (done.changed > 0) libraryChanges.changed();
	return [];
}

/**
 * Undoing a hide made with the vault shut, when the PIN was asked for and not given.
 *
 * What went in cannot be reached again until the vault is opened, so the server wrote nothing.
 * The prompt was the way through and the person closed it, so this says what stayed as it was
 * and offers nothing more: they have just said no to the one thing that would help.
 */
function stillHidden(count: number): void {
	toasts.show(count === 1 ? 'It stayed hidden' : `${counted(count)} stayed hidden`, {
		icon: 'lock'
	});
}

/** The one failure worth its own sentence, and the flat one for everything else. */
function refused(error: unknown, hide: boolean): void {
	// 401 is "no PIN yet", 409 is "the vault is shut". Both mean the same thing to somebody
	// standing in front of it: deal with the PIN.
	const status = error instanceof ApiError ? error.status : 0;
	toasts.show(
		status === 401 || status === 409
			? NO_PIN
			: hide
				? "That couldn't be hidden"
				: "That couldn't be unhidden",
		{ tone: 'error' }
	);
}

/** What went, and the one press back. The same sentence whichever way the write was sent. */
function announce(moved: string[], hide: boolean, options: HideOptions): string[] {
	const plural = options.plural ?? `${options.noun}s`;
	const what = moved.length === 1 ? 'it' : `${counted(moved.length)} ${plural}`;
	const them = moved.length === 1 ? 'it' : 'them';
	const done = !hide
		? `Unhid ${what}`
		: options.stays
			? `Hid ${what}. Still here because the vault is open.`
			: `Hid ${what}. Unlock Hidden to see ${them}.`;
	toasts.show(done, {
		tone: 'success',
		/*
		 * One press back.
		 *
		 * Hiding takes the row off the screen, so there is nothing left to right-click and nothing
		 * saying what went. On a whole selection it is worse: putting them back by hand means
		 * finding each of them again inside something that is, by design, hard to look through.
		 */
		action: { label: 'Undo', run: () => void undoHidden(moved, hide, options) }
	});
	return moved;
}

/** Put back exactly what the last one moved. Quiet: this is already the way back.
 *
 * Sent the same way the write was, so undoing a selection of four hundred is one request where the
 * write was one request. The ids are exactly the ones that moved, so there is nothing here for a
 * partial answer to be ambiguous about.
 *
 * AN UNDO OF A HIDE MADE WITH HIDDEN LOCKED ASKS FOR THE PIN, then tries once more. The server
 * resolves a hidden thing only for a session that has proved the PIN, and that rule is kept whole:
 * the Undo does not get a window of its own in which it may reach what it just hid. What changes is
 * that the person is asked in place, in the one shared prompt, instead of being told to go and
 * unlock and then find what they hid by hand. Once, because a second refusal after the vault opened
 * is not the vault, and asking again would be a loop.
 */
async function undoHidden(ids: string[], wasHidden: boolean, options: HideOptions): Promise<void> {
	let outcome = await putBack(ids, wasHidden, options);
	if (outcome === 'locked') {
		if (!(await vaultPrompt.opened(PUT_IT_BACK))) {
			stillHidden(ids.length);
			return;
		}
		outcome = await putBack(ids, wasHidden, options);
	}
	if (outcome === 'failed' || outcome === 'locked') {
		toasts.show("That couldn't be undone", { tone: 'error' });
		return;
	}
	// However they left, the way back is one re-read of everything: what came back belongs on
	// whatever wall is being looked at, and only the wall can say where.
	if (outcome === 'done') libraryChanges.changed();
}

/**
 * One attempt at the way back, and what came of it.
 *
 * `locked` is the server's refusal because Hidden is shut, told apart from every other failure the
 * way the server says it: a bulk reply sets `vault_locked` beside what it skipped, and a single
 * write answers the 404 an unknown id gets (a hidden thing resolves only with the vault open, and
 * answering anything else would confirm it exists). `quiet` is a partial answer already announced.
 */
async function putBack(
	ids: string[],
	wasHidden: boolean,
	options: HideOptions
): Promise<'done' | 'quiet' | 'locked' | 'failed'> {
	try {
		if (options.setMany) {
			const done = await options.setMany(ids, !wasHidden);
			if (done.skipped > 0) {
				if (wasHidden && done.vault_locked && done.changed === 0) return 'locked';
				announceSkipped(done, options.noun);
				return done.changed === 0 ? 'quiet' : 'done';
			}
		} else {
			for (const id of ids) await options.set(id, !wasHidden);
		}
	} catch (error) {
		const status = error instanceof ApiError ? error.status : 0;
		return wasHidden && status === 404 ? 'locked' : 'failed';
	}
	return 'done';
}
