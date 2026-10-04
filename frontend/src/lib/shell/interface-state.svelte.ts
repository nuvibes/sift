/*
 * The account's own interface state: small answers the client writes and the client reads back.
 *
 * ## Why this is not `remembered.svelte`
 *
 * That module is the BROWSER's memory, and it is the right home for an arrangement of one screen on
 * one machine: whether a band is open, which tab was last looked at. This is the other kind: an
 * answer a person gave once that has to follow them to the other machine. "Don't ask me again
 * before taking a name off a file" is that. Somebody who has agreed to stop being asked has
 * agreed to it, not agreed to it on this laptop.
 *
 * ## One document, one read
 *
 * Every key lives in a single per-account document behind `/settings/interface`, so a screen with
 * three of these on it is one GET rather than three. The read is held as the PROMISE rather than as
 * a flag, so two callers in the same tick wait on the same answer instead of racing two requests.
 * `frequent.svelte.ts` reads through `interfaceState()` below, so there is one GET per session
 * however many modules hold a key.
 *
 * ## What a failure means
 *
 * Nothing on screen. A key that could not be read is a key nobody has answered, which puts every
 * guard back to asking, the safe direction. A key that could not be WRITTEN is a preference that
 * did not stick, and the next press asks again; there is nothing here worth a message about.
 */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { settingChanges } from '$lib/library/changes.svelte';

/** What came back last, by key. Empty until the one read below has landed. */
const held = $state<Record<string, string>>({});

/** The one request, shared. Held as the promise for the reason in the header. */
let reading: Promise<void> | null = null;
/** Whether that one request failed. A flag reads as its default; a list must not be rebuilt from
 *  nothing and written back over the account's copy, so `interfaceState()` says so instead. */
let unread = false;

/**
 * Read the account's interface state, once.
 *
 * Idempotent, and awaited where an answer is about to be needed rather than at boot: most screens
 * ask nothing of it, and a request made on every page load for a document most screens never read
 * is a request made for nothing.
 */
export function recallInterfaceState(): Promise<void> {
	if (reading) return reading;
	reading = (async () => {
		try {
			const answer = await api.get<components['schemas']['InterfaceState']>('/settings/interface');
			for (const [key, value] of Object.entries(answer.state)) held[key] = value;
		} catch {
			// Nothing remembered this sitting. Every flag falls back to its default, which is the
			// answer that keeps a guard in place rather than the one that takes it away.
			unread = true;
		}
	})();
	return reading;
}

/** What the account holds under one key, as far as this session knows. */
function valueOf(key: string): string {
	return held[key] ?? '';
}

/**
 * Everything the account holds, after the one read, as a copy.
 *
 * For a module that keeps its own shape of some keys (`frequent.svelte.ts` keeps a list per kind)
 * and would otherwise read the document a second time to build it. One request per session is
 * the whole point of this module; a copy rather than the live object, so a reader cannot write.
 */
/**
 * Forget what was read, so the next reader asks the account again.
 *
 * For a module that has just written a key of its own and wants the next read to be the
 * account's answer rather than this session's memory of it: `frequent.svelte.ts` after forgetting
 * a kind. Not a second request on its own: nothing is asked until somebody reads.
 */
export function rereadInterfaceState(): void {
	reading = null;
	unread = false;
	// Forgotten, not merely re-asked: a key held from the last read would otherwise outlive the
	// account's answer to the next one, and every flag reads as its default until that lands.
	for (const key of Object.keys(held)) delete held[key];
}

/*
 * An answer given in another window follows this one: the server says an arrangement moved on
 * the settings bell, to this account only, and a session that has read the document reads it
 * again. Keys the account no longer holds are dropped, so a guard switched back on elsewhere asks
 * here too. A session that never read it asks nothing: its first reader reads the answer as it
 * stands. One lasting listener, because this document is the session's, not a screen's.
 */
async function readAgain(): Promise<void> {
	if (reading === null || unread) return;
	try {
		const answer = await api.get<components['schemas']['InterfaceState']>('/settings/interface');
		for (const key of Object.keys(held)) if (!(key in answer.state)) delete held[key];
		for (const [key, value] of Object.entries(answer.state)) held[key] = value;
	} catch {
		// What was read stands; the next move says so again.
	}
}

settingChanges.subscribe(() => void readAgain());

export async function interfaceState(): Promise<Record<string, string>> {
	await recallInterfaceState();
	if (unread) throw new Error("the interface state couldn't be read");
	return { ...held };
}

/** Write one key through, at once. Held locally as well, so the screen that set it does not have to
 *  wait for the round trip to read its own answer back. */
function remember(key: string, value: string): void {
	held[key] = value;
	void api.put('/settings/interface', { body: { state: { [key]: value } } }).catch(() => {
		// Written here, not there. The next press asks again, which is the safe direction.
	});
}

/*
 * --- Whether a chip's cross asks before it removes -------------------------------------------
 *
 * `skip` or nothing, rather than a yes/no pair, because "never answered" and "answered no" are the
 * same state here and a word for both of them would be a third value to keep in step with the
 * server's own validator.
 */
const CHIP_REMOVE = 'confirm.chip_remove';
const SKIP = 'skip';

/** Whether taking something off a file should happen without asking. False until told otherwise,
 *  including while the one read is still in flight, which is why the guard is the default. */
export function chipRemoveSkipped(): boolean {
	return valueOf(CHIP_REMOVE) === SKIP;
}

/** Stop asking, from now on and on every machine this account signs in from. Written only once
 *  somebody has ticked the box AND pressed the button: a box ticked on a dialog that was then
 *  cancelled is not an agreement to anything. */
export function skipChipRemoveConfirm(): void {
	remember(CHIP_REMOVE, SKIP);
}

/**
 * Ask again from now on.
 *
 * The way back, and it has to exist: a guard somebody can switch off and cannot switch on is a
 * guard they lose by accident. Nothing calls it yet (the row that would belongs in Settings), so
 * it is exported and named for whoever adds it.
 */
export function askBeforeChipRemove(): void {
	remember(CHIP_REMOVE, '');
}

/*
 * --- Whether taking a face off a file asks first ----------------------------------------------
 *
 * ONE KEY FOR BOTH VERBS, and that is the decision rather than a saving. Ignoring a face and
 * removing it are two questions on the same strip, asked in the same words about the same picture,
 * and somebody who ticks the box is saying "stop asking me about faces" rather than "stop asking me
 * about this one of the two". Two keys would mean being asked a second time to be rid of a question
 * that reads as one.
 *
 * What it costs is stated rather than hidden: the box is ticked on whichever dialog is open, and it
 * then covers the other one too. The wording on it therefore has to be the STRONGER claim (the
 * one that is true of removing a face for good), so nobody agrees to less than what they get.
 *
 * `skip` or nothing, and it follows the ACCOUNT, for the reasons written on `confirm.chip_remove`
 * above: a guard is on until somebody turns it off, and they turned it off, not turned it off on
 * this laptop.
 */
const FACE_REMOVAL = 'confirm.face_removal';

/** Whether ignoring or removing a face should happen without asking. False until told otherwise,
 *  including while the one read is still in flight: the guard is the default. */
export function faceRemovalConfirmSkipped(): boolean {
	return valueOf(FACE_REMOVAL) === SKIP;
}

/** Stop asking, for both verbs, on every machine this account signs in from. Written only once
 *  somebody has ticked the box AND pressed the button. */
export function skipFaceRemovalConfirm(): void {
	remember(FACE_REMOVAL, SKIP);
}

/**
 * Ask again from now on.
 *
 * The way back, and it has to exist: a guard somebody can switch off and cannot switch on is a
 * guard they lose by accident. Nothing calls it yet. The row that would belongs in Settings,
 * beside the one `askBeforeChipRemove` is waiting for.
 */
export function askBeforeFaceRemoval(): void {
	remember(FACE_REMOVAL, '');
}

/*
 * --- Which folders a download last landed in --------------------------------------------------
 *
 * A library has one folder per person and a hundred of them. Alphabetical, the one somebody has
 * been dropping clips into all afternoon is somewhere in the middle of that list, every time,
 * and it is the same handful of folders all week. So the chooser puts what this account actually
 * used in front of the alphabetical list, exactly as a picker puts what it remembers in front of
 * its page (see `$lib/search/frequent`, which is the same idea about a different list).
 *
 * IT FOLLOWS THE ACCOUNT and not the browser, and that is the same call `frequent` made and for
 * the same reason: somebody who downloads into the same three folders does it from the desktop app
 * and from a browser tab, and a memory held per browser is one they lose by opening Sift somewhere
 * else.
 *
 * Recency and not a count, which is the one place this differs from `frequent`. A folder is a
 * place a download went, and the answer somebody wants first is the one they just used. A count
 * would keep last month's project at the top for weeks after it finished.
 *
 * The ORDER is what is stored and nothing else. What a chooser does with it (how many it shows
 * in front, what it draws beside them) is the chooser's, and the server holds no opinion at all:
 * `recent.folders` is validated as a list of short tokens and never looked at again.
 */
const RECENT_FOLDERS = 'recent.folders';

/**
 * How many go in front of the alphabetical list.
 *
 * Five, because the list underneath is the whole answer and this is a shortcut past it. A longer
 * run of them starts to BE the list, in an order nobody can predict, above an alphabetical one
 * they could, and the row somebody wants is then in the middle of a different middle.
 */
export const RECENT_FOLDERS_KEPT = 5;

/**
 * The folders this account downloaded into, most recent first. Empty until the read has landed,
 * which draws the plain alphabetical list: the right fallback.
 */
export function recentFolders(): string[] {
	return valueOf(RECENT_FOLDERS)
		.split(',')
		.filter((id) => id !== '');
}

/**
 * Remember that a download named this folder. Written through at once: the next visit is the
 * point, and it may be on another machine.
 *
 * The id moves to the FRONT whether or not it was already held, which is what makes this recency
 * rather than a set. Trimmed here as well as on the server, so the value cannot grow.
 */
export function noteFolderUse(folderId: string): void {
	if (folderId === '') return;
	const kept = [folderId, ...recentFolders().filter((id) => id !== folderId)].slice(
		0,
		RECENT_FOLDERS_KEPT
	);
	remember(RECENT_FOLDERS, kept.join(','));
}

/*
 * --- Whether the popout opens with everything under the picture SHOWING ------------------------
 *
 * The popout draws the file, a row with its name on it, and then everything ABOUT the file: the
 * lookalikes, the faces, the record. An Expand control on that row folds all of it away, and which
 * way it was left is remembered.
 *
 * IT FOLLOWS THE ACCOUNT, and that is the deliberate half. It is the opposite call from which pane
 * of a file's record opens, which is always About. The difference is what each one is an answer to.
 * A TAB is about the visit: somebody opens a file to look at it, so a pane held over from yesterday
 * would be the panel deciding what this visit is for. This is about the SCREEN (how much of it
 * somebody wants), and that answer does not change between one file and the next or between one
 * machine and another.
 *
 * `open` or `shut`, rather than a flag, because the DEFAULT IS OPEN and an absent key has to read
 * as the default. Storing "yes"/absent would make "never answered" and "shut" the same state, which
 * is the trap `confirm.chip_remove` above avoids by defaulting the other way.
 */
const POPOUT_EXPANDED = 'popout.expanded';
const OPEN = 'open';
const SHUT = 'shut';

/** Whether the popout shows what is under the picture. Open until somebody says otherwise, which
 *  includes while the one read is still in flight. A screen that starts folded and unfolds a
 *  moment later reads as broken, where one that starts open and folds reads as the answer landing. */
export function popoutExpanded(): boolean {
	return valueOf(POPOUT_EXPANDED) !== SHUT;
}

/** Remember which way it was left, on every machine this account signs in from. */
export function rememberPopoutExpanded(expanded: boolean): void {
	remember(POPOUT_EXPANDED, expanded ? OPEN : SHUT);
}

/*
 * --- No key for a queue screen's own thread ---------------------------------------------------
 *
 * A queue's history lives in Settings > Tasks and Activity > App History, where every act in the library is one list with
 * the same Undo on each line, so there is no band on a queue screen to fold and nothing for a key
 * to remember. The server does not accept one.
 */

/*
 * --- Whether LEAVING the popout keeps the clip going, in the corner ---------------------------
 *
 * Pressing a chip inside the popout, or a face's "Show the face group", goes to another page, and
 * the popout is that page's panel, so it goes with it and whatever was playing stops. That is the
 * right answer for Escape, which means "I have finished with this", and the wrong one for a press
 * that means "take me to that": somebody who follows a person's chip halfway through a clip has not
 * finished with the clip.
 *
 * ON BY DEFAULT, which is why the stored value has a word for each state rather than one word and
 * an absence: an absent key has to read as the default, so "off" must be storable and there has to
 * be a way back. The same reasoning as `popout.expanded` above, in the other direction from
 * `confirm.chip_remove`.
 *
 * It follows the ACCOUNT rather than the browser for the same reason the disclosure above does: it
 * is an answer about how somebody moves around a library, and it does not change between machines.
 */
const POPOUT_TO_MINI = 'popout.leave_to_mini';
const ON = 'on';
const OFF = 'off';

/** Whether a clip carries on in the mini player when the popout is left for another page. On until
 *  somebody says otherwise, which includes while the one read is still in flight. The default is
 *  what was asked for, and a first departure that stopped the clip would be the setting appearing
 *  not to work. */
export function popoutLeavesToMini(): boolean {
	return valueOf(POPOUT_TO_MINI) !== OFF;
}

/** Remember the answer, on every machine this account signs in from. */
export function rememberPopoutLeavesToMini(on: boolean): void {
	remember(POPOUT_TO_MINI, on ? ON : OFF);
}

/*
 * --- How the facial fingerprints export's chooser opens -----------------------------------------
 *
 * Everyone ticked with the picks left out (`except`, the default), or
 * nobody ticked with only the picks sent (`only`). Two words because the default is a state
 * somebody comes back to, as `popout.leave_to_mini` above. It follows the ACCOUNT: it is how
 * somebody shares what Sift learned, the same on every machine they sign in from.
 */
const EXPORT_WAY = 'faces.export_way';

/** Who the export's sheet starts with: everyone (`except`) or nobody (`only`). */
export type ExportWay = 'except' | 'only';

/** How the sheet opens for this account. `except` until somebody chooses otherwise, including
 *  while the one read is in flight, because that is the default. */
export function exportWay(): ExportWay {
	return valueOf(EXPORT_WAY) === 'only' ? 'only' : 'except';
}

/** Remember the choice, on every machine this account signs in from. */
export function rememberExportWay(way: ExportWay): void {
	remember(EXPORT_WAY, way);
}

/*
 * --- No key for which pane of a file's record was last read ------------------------------------
 *
 * A popout is opened to look at the file, so the pane it comes up on is About. A panel opening on
 * History because of something somebody did yesterday would be the screen deciding what this visit
 * is about. The habit is served where it is actually exercised: the pane HOLDS while somebody steps
 * from one file to the next inside one open popout. That is AssetView's own state, it costs no
 * request, and it resets when the popout is closed. The server does not accept a key for it.
 */
