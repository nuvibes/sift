/**
 * WHERE ONE FILE OR RECORD STANDS with enrichment from outside this device.
 *
 * Two facts asked together because a menu asks them together: may this be sent to a stash-box at
 * all, and when was it last sent. One route answers both (see `EnrichmentState`), so a menu
 * opening on a file waits once rather than twice.
 *
 * ## Why the "Last:" line is built here and not in the row that draws it
 *
 * Three surfaces draw it: a file's menu, a person's, and the Auto-enrich control. A sentence
 * written three times is a sentence that comes to read three ways, and the one that drifts is the
 * surface nobody looks at. The row is handed a string or nothing.
 *
 * ## Why nothing is cached
 *
 * A menu is opened, read and closed. Holding an answer past that would mean holding a decision
 * somebody may have just changed from the row underneath, and the whole point of the "Last:" line
 * is that it is true at the moment it is read.
 */
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

/**
 * WHAT SOMEBODY IS TOLD when they ask for something kept local to be enriched.
 *
 * The server's own sentence, word for word (`service.KEPT_LOCAL` in the stash-box slice), and a
 * second copy of it rather than a second wording. It has to be here because the refusal
 * happens BEFORE any request: the sheet does not open and nothing is sent, so there is no reply to
 * read a sentence off. `enrichment.test.ts` pins the text, and the server's is what it was copied
 * from; if the two ever have to differ, the one that changes is this file and it is a mistake.
 */
export const KEPT_LOCAL_SAID =
	'Kept local \u2014 not sent outside this device. Allow enrichment on it first.';

/**
 * Say the refusal, in the one tone it is ever said in. True, always, so a caller can `return`
 * through it: `if (refusedHere(state)) return;` reads as the refusal it is.
 *
 * Its own function rather than a `toasts.show` at each of the six surfaces that refuse, because
 * six copies of one sentence is how one of them comes to say something else.
 */
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

/**
 * Keep this thing local, or let it be enriched again. Says what happened.
 *
 * The toast says what was decided rather than that a request succeeded, and it says the one thing
 * somebody might otherwise assume: this is about everything from now on. A fingerprint that went to
 * a public service last month cannot be recalled, and a message implying otherwise would be the
 * only promise here that nobody can keep.
 */
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

/**
 * WHETHER THE ENRICH ROWS ARE DRAWN REFUSED, and what to say under them.
 *
 * Read off the server's answer rather than worked out here, because the rule is the server's: a
 * FILE is refused by its own switch OR by the Site, the person or the tag it is filed under, and a
 * screen that decided it for itself would be a second opinion about what leaves the machine. Null
 * asks nothing and refuses nothing: a menu whose answer has not arrived draws the rows it has
 * always drawn.
 */
export function refusedOutside(state: EnrichmentState | null): {
	refused: boolean;
	why: string | undefined;
} {
	return { refused: state?.refused ?? false, why: state?.why || undefined };
}

/**
 * The line under a menu row: when this was last enriched, and against what.
 *
 * Null where it never has been, which is what makes the row draw nothing at all. A line reading
 * "Last: never" under every verb on a library nobody has swept is noise, and the absence already
 * says it.
 *
 * The NEWEST run, of however many boxes have enriched this. The server answers newest first, so
 * this reads the head rather than sorting a list it was handed in order.
 */
export function lastEnriched(state: EnrichmentState | null): string | null {
	const run = state?.runs?.[0];
	if (!run) return null;
	const when = onRecord(run.at);
	/* The box is named where it can be. A box that has since been removed leaves a row saying a box
	   did this and nothing to call it, and "Last: <date>" is the honest reading of that, better
	   than an id nobody recognises. */
	return run.box_name ? `Last: ${when} \u2014 ${run.box_name}` : `Last: ${when}`;
}

/**
 * The stash-boxes this install has configured, for the Enrich flyout's rows.
 *
 * ## Why it is held rather than asked per menu
 *
 * A menu opens on every right-click and the answer changes when somebody adds a box, which is a
 * visit to Settings. Asking per menu would be a request for a list of three on every press; asking
 * once and never again would go stale the moment a box is added. So it is loaded on the first menu
 * that wants it and reloaded by whoever adds a box. The settings pane calls `forgetBoxes` when it
 * writes, which is the one place a box is ever added or removed.
 *
 * ## Why a box with no WORD is left out
 *
 * A row here writes a box's word into a request, and a self-hosted box has none. See
 * `known_boxes` on the server, where the same limit is written down and accepted. Such a box is
 * still switched on, still asked by "All stash-boxes", and still draws its marks; what it cannot be
 * is singled out. Leaving it out of the menu is the honest drawing of that: a row that asked
 * nobody would be worse.
 */
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

/**
 * The song lookup, as the Auto-enrich flyout offers it beside the stash-boxes: its word, its name
 * and whether a press would send anything, all the server's (`GET /music/lookup`).
 *
 * Held for the reason the boxes above are: a menu opens on every right-click, and the answer
 * changes only when somebody visits `Settings > Music`. Loaded with the boxes, by the first menu
 * that wants them.
 *
 * The row is OFFERED while the lookup is off or has no key, and refused on the press in the
 * server's own words: a row that vanished would say nothing about where the lookup is switched on,
 * and the refusal names it. Null for a guest, and for a server that could not be asked: no row.
 */
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

/**
 * THE WORD A PRESS SENDS FOR "EVERY SWITCHED-ON BOX": the server's `settings.EVERY_BOX`.
 *
 * Not the same answer as sending no box: nothing means the Settings choice ("Auto-enrich using");
 * this means every box. One spelling for both would leave the server unable to tell "ask them all"
 * from "ask whatever Settings says".
 */
export const EVERY_BOX = 'all';

/**
 * THE ROWS UNDER AUTO-ENRICH: every box, then each configured box by name.
 *
 * One declaration for every surface that offers Auto-enrich (a file's menu and bar, the walls'
 * menus and bars, the related tabs and the Options menu on a person's, a Site's and a tag's own
 * page), so the rows cannot come apart (a copy that dropped the pressed box on the way to the
 * server would make every per-box row ask every box). A row here hands the box to `run`; what `run`
 * does with it is the surface's, and the surface's only job is to pass it on.
 *
 * NO "SIFT'S OWN" ROW, and that is a finding rather than an omission: none of Sift's own passes can
 * be queued for exactly the things chosen. Faces, watermarks and the file-name suggester each sweep
 * the WHOLE library from their own Settings section (`/faces/scan`, the watermarks task's Run now,
 * the suggestions scan), and fingerprinting runs as files arrive. A row that started a
 * whole-library pass from a menu opened on one file would do something other than what it says. The
 * row belongs here the day one of them takes a list of files or subjects.
 */
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

/**
 * The song lookup's row under Auto-enrich, beside the stash-boxes' rows: the server's name for it
 * and the Music page's glyph, since what it writes onto a file is the song.
 *
 * A file's row only: AcoustID knows recordings by their sound, so there is nothing to ask it about
 * a person, a Site or a tag, and the walls of those never draw it.
 */
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

/**
 * Ask AcoustID again, under the lookup's own row: for a file AcoustID was asked about and did not
 * know. At any age, because a person chose the file; `Settings > Music` asks again about every such
 * file, but only those last asked long enough ago.
 */
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

/**
 * WHERE EACH ROW OF AN ENTITY WALL STANDS, asked as its menu opens and remembered by id.
 *
 * ## Why a holder and not a field on the row
 *
 * A wall's rows come off five different statements (the listing, the by-id read, two write
 * replies and the suggester), and a field added to the wire would have to be filled in all five.
 * Four of them being right and one being wrong is invisible: the menu would say "Do not enrich" on
 * something already kept local. This asks the ONE route that already answers the question, at the
 * moment a menu wants it, which is what the file menu next door does. See
 * `FileVerbs.askAboutEnrichment`.
 *
 * ## How long it is kept
 *
 * By id, for the life of the page. A menu is opened, read and closed, and the answer it needs must
 * not change under it. But a press of the row underneath does change it, so `mark` writes the new
 * answer straight in rather than waiting for a re-ask. Nothing is held across a page, because the
 * holder is made by the page.
 */
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
	/**
	 * Ask again about everything already asked about when the library moves: a record enriched,
	 * kept local or refused in another window is a library change. Called by the page that holds
	 * this, while it sets up, so the listening ends with the page.
	 */
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

	/**
	 * THE THREE READERS ASK AS THEY READ, which is what makes this need no wiring on a wall.
	 *
	 * They are called where a menu's verbs are built, and a menu's verbs are built when the menu
	 * opens, so a page of sixty cards asks nothing until somebody right-clicks one, and then asks
	 * once. Asking is safe from a template because nothing here writes reactive state on the way
	 * through: the set of ids already asked is a plain Set, and the answer lands in a `.then`.
	 * Svelte refuses a state write in a template expression outright (see `FileVerbs`).
	 */
	private reading(ids: readonly string[]): void {
		for (const id of ids) this.ask(id);
	}

	/** Whether every one of these is kept local. Mixed reads as NOT (see `entityVerbs`). */
	allKeptLocal(ids: readonly string[]): boolean {
		this.reading(ids);
		return ids.length > 0 && ids.every((id) => this.held[id]?.kept_local === true);
	}

	/**
	 * Whether ANY of these is refused, which is what greys the row over a selection.
	 *
	 * ANY and not every, which is the opposite reading from the one above it and is deliberate:
	 * this is the direction that cannot send anything anywhere by accident. A selection of forty
	 * with one kept-local row in it is a press that would send thirty-nine and refuse one, and the
	 * honest drawing of that is a row that does not run until the one is dealt with.
	 */
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
