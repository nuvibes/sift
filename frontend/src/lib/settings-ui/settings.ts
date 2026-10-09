/* Reading and writing preferences, in the one shape the server actually answers in. */

import { ApiError, api } from '$lib/api/client';
import { settingChanges } from '$lib/library/changes.svelte';
import { session } from '$lib/shell/session.svelte';

export interface SettingEntry {
	key: string;
	value: unknown;
	/* What the setting holds when nothing has been stored. */
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
	/** What zero is called, where zero means "work it out yourself" rather than none. */
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

/** What to tell somebody when a setting would not save: THE SERVER'S OWN SENTENCE, when sent. */
export function refusalOf(error: unknown): string {
	if (error instanceof ApiError && error.detail) return error.detail;
	return "That setting couldn't be saved.";
}

/* One read in the air at a time, shared by everyone who asks while it is. */
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

/* The session's one copy of the values: read once, and kept until a setting moves. */
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
	// Kept only where nothing moved while it was being read: a save meanwhile makes it stale.
	if (who !== null && written === before && (session.viewer?.id ?? null) === who) {
		held = { values: new Map(values), who, written };
	}
	return values;
}

/* Save a batch. All or none, decided by the server. */
export async function saveSettings(values: Record<string, unknown>): Promise<void> {
	// Counted BEFORE the request: a save that began at any point during the read makes it stale.
	written += 1;
	await api.put<void>('/settings', { body: { values } });
	// Everything that saves a setting comes through here, so this tells the rest of the app.
	for (const watch of watchers) watch(values);
}

type SettingsWatcher = (saved: Record<string, unknown>) => void;
const watchers = new Set<SettingsWatcher>();

/* How many settings this browser has written. Only ever compared with itself. */
let written = 0;

settingChanges.subscribe(() => {
	const writtenBefore = written;
	held = null;
	void fetchSettingValues()
		.then((values) => {
			/* Abandoned, not retried: the save that overtook this read announces itself. */
			if (written !== writtenBefore) return;
			const saved = Object.fromEntries(values);
			for (const watch of watchers) watch(saved);
		})
		.catch(() => {
			// Unreachable for a moment. The next announcement brings it back.
		});
});

/** Be told when settings are saved, with the batch that was saved. */
export function onSettingsSaved(watch: SettingsWatcher): void {
	watchers.add(watch);
}
