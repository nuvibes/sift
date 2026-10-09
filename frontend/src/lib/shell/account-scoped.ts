/*
 * Everything the client holds that belongs to whoever is signed in. The desktop page never reloads,
 * so without this a second account would keep the first one's preferences. A plain list rather than
 * a registry: a store that forgot to register would fail silently.
 */

import { appearance } from '$lib/theme/appearance.svelte';
import { imports } from '$lib/library/imports.svelte';
import { ratingScale } from '$lib/library/rating.svelte';
import { theme } from '$lib/theme/theme.svelte';
import { tileMarks } from '$lib/grid/tile-marks.svelte';

/** One store each, so what is forgotten and what is read can never name different stores. */
const PREFERENCES = [theme, ratingScale, tileMarks, appearance];

/** Read every preference of the signed-in account, once; nothing is awaited. */
export function loadAccountScopedPreferences(): void {
	for (const store of PREFERENCES) void store.load();
}

/** Forget them when the account CHANGES; on first load it would flash the default theme. */
export function forgetAccountScopedPreferences(): void {
	stopAccountScopedReaders();
	for (const store of PREFERENCES) store.forget();
}

/* Readers that ask on their own, stopped when a session ends. */
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
