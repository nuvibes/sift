/* Reading and writing preferences, in the one shape the server actually answers in.
 *
 * `GET /settings` returns `{sections: [{name, settings: [{key, value, ...}]}]}`: a *list* of
 * sections, each holding its own list of entries. That shape is easy to read wrongly in a way that
 * cannot be noticed: flattening it as though `sections` were a map of name to entries yields the
 * section objects themselves, so every lookup by key finds nothing, and every caller falls back to
 * its default forever. There is no error and nothing in the console. The preference simply never
 * takes effect, which is indistinguishable from one that was never set.
 *
 * So the flattening happens once, here, and the screens ask this instead of each doing it again.
 */

import { ApiError, api } from '$lib/api/client';
import { settingChanges } from '$lib/library/changes.svelte';
import { session } from '$lib/shell/session.svelte';

export interface SettingEntry {
	key: string;
	value: unknown;
	/* What the setting holds when nothing has been stored. Also what says which control it gets:
	   see `settings-ui/control`, where a value's type is never trusted for that and the default
	   always is. */
	default?: unknown;
	label?: string;
	help?: string;
	/** Reference material, shown behind a "more" affordance rather than between two controls. */
	disclosure?: string;
	choices?: unknown[];
	/** What each of those choices is called on screen, in the same order. */
	choice_labels?: string[];
	minimum?: number;
	maximum?: number;
	/** The suffix a number is shown with. A unit of `%` is also what makes it a slider. */
	unit?: string;
	/**
	 * What zero is called, where zero means "work it out yourself" rather than none.
	 *
	 * Declared by the setting, never listed here. The screen shows this word in place of the zero
	 * and an emptied box means it (see `NumberInput`).
	 */
	automatic_label?: string;
	scope?: string;
}

export interface SettingSection {
	name: string;
	settings: SettingEntry[];
}

interface SettingsResponse {
	sections: SettingSection[];
}

/**
 * What to tell somebody when a setting would not save: THE SERVER'S OWN SENTENCE, when it sent one.
 *
 * A refused setting is not a failure, it is an answer: "a graphics card cannot be used here, and
 * this is why". "That setting could not be saved" is the one part of the exchange that carries no
 * information: the reason has already crossed the wire.
 *
 * One function rather than a copy of the same conditional on every settings screen, because they
 * would drift and only one of them would ever be looked at.
 */
export function refusalOf(error: unknown): string {
	if (error instanceof ApiError && error.detail) return error.detail;
	return "That setting couldn't be saved.";
}

/*
 * One read in the air at a time, shared by everyone who asks while it is.
 *
 * A page load starts several stores that each want a preference (the theme, the tile marks, the
 * clock, the rating scale, the vault), and each asking for itself would fetch the same answer
 * that many times. Only a read still in the air is shared, never a finished one, so nothing here can go
 * stale; and never across a save, because a read begun before a save describes the state before it.
 */
let inFlight: { read: Promise<SettingSection[]>; written: number } | null = null;

/** Every setting this account may see, grouped as the settings screen draws them. */
export async function fetchSettings(): Promise<SettingSection[]> {
	let asking = inFlight;
	if (asking === null || asking.written !== written) {
		const read = api.get<SettingsResponse>('/settings').then((answer) => answer.sections ?? []);
		const mine = { read, written };
		asking = inFlight = mine;
		const done = () => {
			if (inFlight === mine) inFlight = null;
		};
		read.then(done, done);
	}
	// A copy each: a screen that changes what it was handed must not change another's.
	return structuredClone(await asking.read);
}

/*
 * The session's one copy of the values: read once, and kept until a setting moves.
 *
 * A dozen stores and screens each want a preference (the theme, the tile marks, the clock, the
 * rating scale, the records, the vault, the player's dwell, the Theater wall), and each would
 * otherwise read the whole set for itself whenever it was built, long after the page load the in-flight
 * sharing above covers. They read this instead. It is dropped by the only three things that can
 * make it wrong: a save from this browser (counted by `written`), a setting moved somewhere else
 * (`settingChanges`, below, which drops it and reads again), and a different account (the copy
 * names whose it is; with nobody known, nothing is kept).
 */
let held: { values: Map<string, unknown>; who: string; written: number } | null = null;

/** The same, flattened to a map of key to value. What a screen wanting one preference asks for. */
export async function fetchSettingValues(): Promise<Map<string, unknown>> {
	const who = session.viewer?.id ?? null;
	if (held !== null && held.who === who && held.written === written) return new Map(held.values);
	const before = written;
	const sections = await fetchSettings();
	const values = new Map(
		sections.flatMap((section) => section.settings ?? []).map((one) => [one.key, one.value])
	);
	// Kept only where nothing moved while it was being read: a save that began meanwhile makes it
	// the answer from before the save, which is fine to hand back once and wrong to keep.
	if (who !== null && written === before && (session.viewer?.id ?? null) === who) {
		held = { values: new Map(values), who, written };
	}
	return values;
}

/* Save a batch. All or none, decided by the server: an unknown key, a value out of range, or a
 * global setting written by a guest fails the whole request and stores nothing. The wrapper object
 * is required: the endpoint forbids extra fields, so a bare map is refused as malformed.
 */
export async function saveSettings(values: Record<string, unknown>): Promise<void> {
	// Counted BEFORE the request rather than after it, and that is the whole of what makes the
	// staleness check below correct: the read this is racing may already be in the air, and what
	// has to be caught is a save that began at any point during it, not only one that finished.
	written += 1;
	await api.put<void>('/settings', { body: { values } });
	// Everything that saves a setting comes through here, so this is the one place that can tell
	// the rest of the app a preference has moved. Without it a switch takes effect at the next
	// page load, which reads as a switch that did nothing: the settings sheet sits OVER the
	// screen the preference changes, so the proof is two inches away and stale.
	for (const watch of watchers) watch(values);
}

type SettingsWatcher = (saved: Record<string, unknown>) => void;
const watchers = new Set<SettingsWatcher>();

/* How many settings this browser has written. Only ever compared with itself.
 *
 * A read that was in the air when a save began describes the state BEFORE that save, so handing it
 * to the watchers would put the old value back a beat after the new one appeared, and the stores
 * below apply on the screen first, so what it would undo is something somebody is looking at.
 */
let written = 0;

/* And the same, for a preference somebody changed somewhere else.
 *
 * A setting is saved on one computer and read on another: this account's own on a second browser,
 * or one the whole installation shares, changed by another admin. The watchers below are how a
 * preference reaches the screens that act on it. Fired only for the tab that did the saving,
 * they would leave the switch to take effect elsewhere at the next page load, which reads as a
 * switch that did nothing.
 *
 * Read back rather than carried: the message says a setting moved and never which, so the values
 * come from the server through the ordinary endpoint like every other read. Handed to the same
 * watchers, so there is one way a preference takes effect rather than a local one and a remote one
 * that can disagree.
 */
settingChanges.subscribe(() => {
	const writtenBefore = written;
	held = null;
	void fetchSettingValues()
		.then((values) => {
			/* Abandoned rather than retried, and that is not laziness: the save that overtook this
			 * read announces itself, and the announcement brings this straight back with a read that
			 * cannot be stale. A retry here would be a second way to do the same thing, and the one
			 * that only runs in a race is the one nobody would notice breaking. */
			if (written !== writtenBefore) return;
			const saved = Object.fromEntries(values);
			for (const watch of watchers) watch(saved);
		})
		.catch(() => {
			// Unreachable for a moment. The next announcement brings it back, and nothing on screen
			// is worse off than it was.
		});
});

/**
 * Be told when settings are saved, with the batch that was saved.
 *
 * For a preference something outside the settings screen acts on. The watcher is handed only what
 * changed, so a store that cares about one key checks for it and ignores everything else rather
 * than re-reading the whole set on every save.
 *
 * Deliberately not a general event bus. There is one writer, the callbacks are registered at module
 * scope by long-lived stores, and nothing here ever unsubscribes. If that stops being true this
 * needs to grow a way to.
 */
export function onSettingsSaved(watch: SettingsWatcher): void {
	watchers.add(watch);
}
