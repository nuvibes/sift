/* Everything the client is holding that belongs to whoever is signed in.
 *
 * `vault-scoped.ts` for the other axis, and deliberately the same shape: one named list, emptied
 * from one effect, with the stores' existing "load it if it is not loaded" logic doing the rest.
 * Two mechanisms for one idea would be two lists to keep in step.
 *
 * ## What goes wrong without it
 *
 * Every store below reads its answer once and remembers that it did. "Once" means once per PAGE,
 * and the desktop shell's page never reloads: signing out and back in is a client-side
 * navigation. So a second account signing in on the same window would keep the first one's theme,
 * star scale, tile marks and move confirmation, for as long as the window stayed open, with
 * nothing anywhere saying why. On the browser it would take a refresh to notice.
 *
 * The theme is the visible half of it. `Theme.forget()` alone, called by both sign-out buttons,
 * makes it worse: it puts the theme back to the near-black default and clears the browser's
 * mirror, and then nothing asks the server again, because the memo is already satisfied for the
 * page.
 *
 * ## Why a list and not a registry
 *
 * A store could register itself and this file could hold nothing. It would also be silently wrong
 * the first time somebody wrote a store and did not: the failure is invisible, and it is the same
 * failure this file exists to prevent. A list that a reader can see all of, in one screen, next
 * to a test that walks it, is the version that stays true. Anything that caches a read belonging
 * to one account belongs here on the same day it is written.
 *
 * ## The one that is not here
 *
 * `savedSearches` follows the `mine` bell and reloads itself only once it has been opened, so it
 * has no memo to invalidate. `session` is not scoped to the account; it IS the account.
 */

import { appearance } from '$lib/theme/appearance.svelte';
import { imports } from '$lib/library/imports.svelte';
import { ratingScale } from '$lib/library/rating.svelte';
import { theme } from '$lib/theme/theme.svelte';
import { tileMarks } from '$lib/grid/tile-marks.svelte';

/**
 * One store each, and both functions below walk this.
 *
 * One list rather than two runs of calls, so what is FORGOTTEN when the account changes and what is
 * READ on the way in can never name different stores. A store that has to be emptied when the
 * account changes is a store something has to fill again, and a store left to whichever screen
 * remembers to ask is missing on the screens that do not (a wall of people drawing heights, for
 * one).
 */
const PREFERENCES = [theme, ratingScale, tileMarks, appearance];

/**
 * Read every preference the signed-in account is drawn by, once.
 *
 * Called from the root layout, on the one effect that knows which account is signed in. So a
 * preference is asked for on the way in to the application rather than by whichever screen happens
 * to need it. A preference every screen reads is not a thing each screen can be trusted to remember
 * to load.
 *
 * Nothing is awaited. Every store draws its default until its answer lands, and a layout that
 * waited would hold the whole application behind a preference.
 */
export function loadAccountScopedPreferences(): void {
	for (const store of PREFERENCES) void store.load();
}

/**
 * Throw away every preference read on behalf of the account that was signed in.
 *
 * Called when the signed-in account CHANGES, and at no other time: never on the first load of a
 * page, where there is no previous account and where `theme.forget()` would clear the browser's
 * mirror of the look and reintroduce the flash of the default theme that the mirror exists to
 * prevent.
 */
export function forgetAccountScopedPreferences(): void {
	stopAccountScopedReaders();
	for (const store of PREFERENCES) store.forget();
	// There is no first-run flow to forget here: its questions are settings in the registry.
}

/* The readers that ask on their own (a timer, a watch), stopped when a session ends so nothing
   is asked for it afterwards. A watch made per screen checks `accountTurn` on each tick. */
const READERS = [imports];
let turn = 0;

/** Which session the client is on: it moves the moment one ends. */
export function accountTurn(): number {
	return turn;
}

/** Stop every reader of the session that is ending. Called before sign-out is sent, too. */
export function stopAccountScopedReaders(): void {
	turn += 1;
	for (const reader of READERS) reader.stop();
}
