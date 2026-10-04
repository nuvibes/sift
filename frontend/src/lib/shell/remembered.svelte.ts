/* A small thing this browser remembers between visits.
 *
 * For arrangements rather than preferences. A preference is chosen on the settings screen, is the
 * same on every machine somebody signs in from, and is stored on the server. Whether a band on one
 * page is open is neither of those: it belongs to the screen it was set on, and putting it in
 * Settings would be a nonsense row in a list of real choices.
 *
 * Anything unreadable reads as "never set", so the caller's default wins. The try/catch is not
 * defensive noise: a browser with storage switched off, or a full quota, throws on both calls, and
 * the honest fallback is a screen that works and forgets rather than one that fails to draw.
 *
 * Server rendering is off for this application, so `localStorage` exists by the time any of this
 * runs.
 */

/**
 * What this browser has stored under a key, or null if it has nothing or cannot say.
 *
 * The try/catch also covers a context with no `localStorage` at all, where the reference itself
 * throws. That is one guard rather than a `typeof` check at each call site that only some of them had.
 */
export function readStored(key: string): string | null {
	try {
		return localStorage.getItem(key);
	} catch {
		return null;
	}
}

/** Remember a value. Silently does nothing where storage is refused, which is the honest fallback. */
export function writeStored(key: string, value: string): void {
	try {
		localStorage.setItem(key, value);
	} catch {
		// Storage refused. Everything still works; it just will not be remembered.
	}
}

/** Forget one. Used where "no opinion" is a real state and an empty string is not the same thing. */
export function clearStored(key: string): void {
	try {
		localStorage.removeItem(key);
	} catch {
		// As above.
	}
}

/**
 * A yes/no this browser remembers.
 *
 * Read `.on` and assign to it. The default is used until somebody has actually chosen, so changing
 * what Sift ships with reaches everybody who never expressed an opinion, which is the behaviour a
 * default is for.
 */
export function rememberedFlag(key: string, fallback: boolean) {
	const stored = readStored(key);
	let value = $state(stored === null ? fallback : stored === 'yes');

	return {
		get on(): boolean {
			return value;
		},
		set on(next: boolean) {
			value = next;
			writeStored(key, next ? 'yes' : 'no');
		}
	};
}

/*
 * Whether this browser has been told to stop asking twice before a disk delete.
 *
 * ## Why this is remembered HERE and not on the account
 *
 * It is an arrangement of one screen on one machine, which is exactly what this module is for.
 * "Ask me again before deleting" on the server would be a preference that travels to a borrowed
 * laptop and a phone, and the second question is about the hand on the mouse rather than about the
 * person. Somebody who deletes from disk all day at their desk has not agreed to skip the guard
 * on a machine they are signed in to once.
 *
 * ## What it does NOT turn off
 *
 * The first dialog, the one with the two cards, still opens every time. That one is where the
 * choice between "out of Sift" and "off the disk" is made, and it is the dialog that stops somebody
 * deleting files when they meant to tidy a wall. What this skips is the SECOND question (the one
 * that only restates a decision already made), and only once somebody has ticked a box saying so.
 */
const DELETE_NO_CONFIRM = 'sift.delete.noConfirm';

const deleteSecondQuestion = rememberedFlag(DELETE_NO_CONFIRM, false);

/** Whether the second, permanent-delete question should be skipped on this browser. */
export function deleteConfirmationSkipped(): boolean {
	return deleteSecondQuestion.on;
}

/** Remember that it may be skipped. Written only when somebody ticked the box and then confirmed:
 *  a box ticked on a dialog that was cancelled is not an answer to anything. */
export function rememberDeleteConfirmation(): void {
	deleteSecondQuestion.on = true;
}

/**
 * Ask again from now on.
 *
 * The way back, and it has to exist: a guard somebody can switch off and cannot switch on is a
 * guard they lose by accident. Nothing in the app calls this yet (the row that would belongs in
 * Settings), so it is exported and named for whoever adds it.
 */
export function forgetDeleteConfirmation(): void {
	deleteSecondQuestion.on = false;
	// Both, and in this order. The assignment moves the value this session is reading; clearing the
	// key puts the browser back to "never said", so a later change to what Sift ships with reaches
	// somebody who had turned it off and then turned it back on.
	clearStored(DELETE_NO_CONFIRM);
}
