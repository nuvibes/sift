/** WHERE ONE FILE OR RECORD STANDS with enrichment from outside this device. */
import { ApiError, api } from '$lib/api/client';
import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
import { onRecord } from '$lib/shell/when';
import { toasts } from '$lib/shell/toasts.svelte';
import type { components } from '$lib/api/schema';
import type { Verb } from '$lib/components/common/verbs';

export type EnrichmentState = components['schemas']['EnrichmentState'];
type EnrichmentRun = components['schemas']['EnrichmentRunView'];

/** The kinds that carry "Don't enrich". The server names the same five; a folder's reaches its files. */
type EnrichmentSubject = 'asset' | 'person' | 'site' | 'tag' | 'folder';

/** WHAT SOMEBODY IS TOLD when they ask for something kept local to be enriched. */
export const KEPT_LOCAL_SAID =
	'Kept local \u2014 not sent outside this device. Allow enrichment on it first.';

/** Say the refusal, in the one tone it is ever said in. */
export function sayKeptLocal(): true {
	toasts.show(KEPT_LOCAL_SAID, { tone: 'error', icon: 'shield' });
	return true;
}

/** Where one thing stands, or null when it could not be asked. Null is drawn as nothing. */
export async function enrichmentOf(
	subject: EnrichmentSubject,
	id: string
): Promise<EnrichmentState | null> {
	try {
		return await api.get<EnrichmentState>(`/stash-boxes/enrichment/${subject}/${id}`);
	} catch {
		return null;
	}
}

/** Keep this thing local, or let it be enriched again. */
export async function setKeptLocal(
	subject: EnrichmentSubject,
	id: string,
	keptLocal: boolean
): Promise<EnrichmentState | null> {
	try {
		const state = await api.put<EnrichmentState>(
			`/stash-boxes/enrichment/${subject}/${id}/keep-local`,
			{ body: { kept_local: keptLocal } }
		);
		toasts.show(
			keptLocal
				? "Kept local \u2014 it won't be sent outside this device from now on"
				: 'It can be enriched from a stash-box again',
			{ tone: 'success' }
		);
		return state;
	} catch (error) {
		toasts.show(
			error instanceof ApiError && error.detail ? error.detail : "That couldn't be changed",
			{ tone: 'error' }
		);
		return null;
	}
}

/** WHETHER THE ENRICH ROWS ARE DRAWN REFUSED, and what to say under them. */
export function refusedOutside(state: EnrichmentState | null): {
	refused: boolean;
	why: string | undefined;
} {
	return { refused: state?.refused ?? false, why: state?.why || undefined };
}

/** The line under a menu row: when this was last enriched, and against what. */
export function lastEnriched(state: EnrichmentState | null): string | null {
	const run = state?.runs?.[0];
	if (!run) return null;
	const when = onRecord(run.at);
	/* The box is named where it can be. */
	return run.box_name ? `Last: ${when} \u2014 ${run.box_name}` : `Last: ${when}`;
}

/** The stash-boxes this install has configured, for the Enrich flyout's rows. */
let known = $state<{ word: string; name: string }[] | null>(null);
let asking: Promise<void> | null = null;

/** The list as it stands. Empty until it has been loaded, which draws no flyout at all. */
export function enrichBoxes(): readonly { word: string; name: string }[] {
	return known ?? [];
}

/** Load it once. Safe to call from every menu that opens: the second call joins the first. */
export function loadEnrichBoxes(): void {
	if (known !== null || asking !== null) return;
	asking = (async () => {
		try {
			const answer = await api.get<components['schemas']['BoxList']>('/stash-boxes');
			known = answer.boxes
				.filter((box) => box.enabled && box.slug)
				.map((box) => ({ word: String(box.slug), name: box.name }));
		} catch {
			// A guest, or a server that cannot be reached. No flyout, and the verb stays the plain
			// press it has always been, which is the right failure: it still works.
			known = [];
		} finally {
			asking = null;
		}
	})();
}

/** Forget the list, so the next menu asks again. Called where a box is added, removed or renamed. */
export function forgetEnrichBoxes(): void {
	known = null;
}

/** The song lookup, as the Auto-enrich flyout offers it beside the stash-boxes: its word, its
 * name and whether a press would send anything, all the server's (`GET /music/lookup`). */
let lookup = $state<{ word: string; name: string; ready: boolean } | null>(null);
let lookupAsked = false;

/** The song lookup's row, or null where there is none to offer. */
export function songLookup(): { word: string; name: string; ready: boolean } | null {
	return lookup;
}

/** Ask once what the song lookup is called and whether it is ready. A second call does nothing. */
export function loadSongLookup(): void {
	if (lookupAsked) return;
	lookupAsked = true;
	void api.get<components['schemas']['LookupState']>('/music/lookup').then(
		(state) => (lookup = { word: state.source, name: state.label, ready: state.ready }),
		() => {
			// A guest, or a server that could not be reached: no row, and nothing else changes.
			lookup = null;
		}
	);
}

/** THE WORD A PRESS SENDS FOR "EVERY SWITCHED-ON BOX": the server's `settings.EVERY_BOX`. */
export const EVERY_BOX = 'all';

/** THE ROWS UNDER AUTO-ENRICH: every box, then each configured box by name. */
export function autoEnrichRows(
	boxes: readonly { word: string; name: string }[],
	run: (ids: string[], box: string) => void
): Verb[] {
	return [
		{
			id: 'enrich-all',
			label: 'All stash-boxes',
			icon: 'backlight_low',
			alone: true,
			run: (ids: string[]) => run(ids, EVERY_BOX)
		},
		...boxes.map((box) => ({
			id: `enrich-${box.word}`,
			label: box.name,
			icon: 'inventory_2' as const,
			alone: true,
			run: (ids: string[]) => run(ids, box.word)
		}))
	];
}

/** The song lookup's row under Auto-enrich, beside the stash-boxes' rows: the server's name for
 * it and the Music page's glyph, since what it writes onto a file is the song. */
export function songLookupRow(
	found: { word: string; name: string },
	run: (ids: string[]) => void
): Verb {
	return {
		id: `enrich-${found.word}`,
		label: found.name,
		icon: 'music_note_2',
		alone: true,
		run
	};
}

/** Ask AcoustID again, under the lookup's own row: for a file AcoustID was asked about and did
 * not know. */
export function songAgainRow(
	found: { word: string; name: string },
	run: (ids: string[]) => void
): Verb {
	return {
		id: `enrich-${found.word}-again`,
		label: `Ask ${found.name} again`,
		icon: 'music_note_2',
		alone: true,
		run
	};
}

/** WHERE EACH ROW OF AN ENTITY WALL STANDS, asked as its menu opens and remembered by id. */
export class EntityEnrichment {
	private readonly subject: EnrichmentSubject;
	private readonly asked = new Set<string>();
	private held = $state<Record<string, EnrichmentState>>({});

	constructor(subject: EnrichmentSubject) {
		this.subject = subject;
	}

	/** Ask about one row, once. Safe to call from a template: nothing is written synchronously. */
	ask(id: string | undefined): void {
		if (id === undefined || this.asked.has(id)) return;
		this.asked.add(id);
		void enrichmentOf(this.subject, id).then((state) => {
			if (state) this.held[id] = state;
		});
	}

	/** What is known about one row, or null while nothing is. */
	/** Ask again about everything already asked about when the library moves: a record enriched,
	 * kept local or refused in another window is a library change. */
	follow(): void {
		whenChanged(libraryChanges, () => {
			const again = [...this.asked];
			this.asked.clear();
			for (const id of again) this.ask(id);
		});
	}

	of(id: string | undefined): EnrichmentState | null {
		return id === undefined ? null : (this.held[id] ?? null);
	}

	/** THE THREE READERS ASK AS THEY READ, which is what makes this need no wiring on a wall. */
	private reading(ids: readonly string[]): void {
		for (const id of ids) this.ask(id);
	}

	/** Whether every one of these is kept local. Mixed reads as NOT (see `entityVerbs`). */
	allKeptLocal(ids: readonly string[]): boolean {
		this.reading(ids);
		return ids.length > 0 && ids.every((id) => this.held[id]?.kept_local === true);
	}

	/** Whether ANY of these is refused, which is what greys the row over a selection. */
	anyRefused(ids: readonly string[]): boolean {
		this.reading(ids);
		return ids.some((id) => this.held[id]?.refused === true);
	}

	/** The reason to draw under a refused row, or nothing. */
	why(ids: readonly string[]): string | undefined {
		this.reading(ids);
		for (const id of ids) {
			const said = this.held[id];
			if (said?.refused) return said.why || undefined;
		}
		return undefined;
	}

	/** What a press of the row underneath just decided, put where the menu reads it. */
	mark(ids: readonly string[], state: EnrichmentState | null): void {
		if (!state) return;
		for (const id of ids) if (id === state.id) this.held[id] = state;
	}
}
