import { ApiError } from '$lib/api/client';
import { announceSkipped, type BulkWriteDone } from '$lib/library/bulk';
import { libraryChanges } from '$lib/library/changes.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { place } from '$lib/components/common/toast-pieces';
import { counted } from '$lib/entity/entity-counts';
import { vaultPrompt } from '$lib/shell/vault.svelte';

/* Putting a thing in the vault from a menu, wherever the menu is. */

/** How the caller performs the write for one id. Each kind of thing has its own endpoint. */
type SetVault = (id: string, vault: boolean) => Promise<void>;

/** How the caller performs the write for a WHOLE selection, where its endpoint takes a list. */
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
	/** Whether what was hidden is still on screen afterwards. */
	stays?: boolean;
}

/** A PIN has to exist before anything can go in, and this is what a refusal looks like. */
const NO_PIN = [
	'Set a PIN in ',
	place('Profile', '/settings/profile#profile.pin'),
	" first. It's what unhides these again."
];

/* What the PIN prompt says it is for when an Undo raised it: the act waiting on the PIN, rather
   than "Show hidden items", which is the prompt's own reason and reads wrong here. */
const PUT_IT_BACK = 'Put it back';

/** Hide, or unhide: the same action pointed the other way. */
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

/** The whole selection in ONE request, for a kind whose endpoint takes a list. */
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
	// itself again.
	if (done.changed > 0) libraryChanges.changed();
	return [];
}

/** Undoing a hide made with the vault shut, when the PIN was asked for and not given. */
function stillHidden(count: number): void {
	toasts.show(count === 1 ? 'It stayed hidden' : `${counted(count)} stayed hidden`, {
		icon: 'lock'
	});
}

/** The one failure worth its own sentence, and the flat one for everything else. */
function refused(error: unknown, hide: boolean): void {
	// 401 is "no PIN yet", 409 is "the vault is shut".
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
		/* One press back. Hiding takes the row off the screen, so there is nothing left to
		 * right-click and nothing saying what went. */
		action: { label: 'Undo', run: () => void undoHidden(moved, hide, options) }
	});
	return moved;
}

/** Put back exactly what the last one moved. Quiet: this is already the way back. */
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

/** One attempt at the way back, and what came of it. */
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
