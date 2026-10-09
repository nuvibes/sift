/* How each entity verb is carried out, for the five kinds of named thing, on any wall: one registry
 * keyed by kind, `KIND_FACTS` holding what differs. `person` is the subject and `people` the wall,
 * kept as two fields. Every flow (share, hide, delete, merge, tag, rename, pin) lives here once;
 * `wall-verbs.walls.test.ts` refuses a wall importing one of those sheets again. */

import { counted, filesSaid } from '$lib/entity/entity-counts';
import { goto } from '$app/navigation';
import { api, ApiError, type ApiPath } from '$lib/api/client';
import type { ChoicePicture, VerbPick } from '$lib/components/common/verbs';
import { enrichMany, type EnrichSubject } from '$lib/entity/enrich-many.svelte';
import { EntityEnrichment, sayKeptLocal, setKeptLocal } from '$lib/entity/enrichment.svelte';
import { tagPickerFor, type EntityKind as TagWall } from '$lib/entity/entity-tags.svelte';
import { setHidden } from '$lib/library/hiding';
import { allPinned, pinAll } from '$lib/library/pinning.svelte';
import { libraryChanges } from '$lib/library/changes.svelte';
import type { MergeableKind } from '$lib/entity/merge.svelte';
import { pageOf, type EntityKind } from '$lib/entity/related.svelte';
import type { ShareTarget, ShareableType } from '$lib/library/sharing';
import { tags as tagStore } from '$lib/entity/tags.svelte';
import { collections } from '$lib/library/collections.svelte';
import { people, sites } from '$lib/people/people.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { vault } from '$lib/shell/vault.svelte';
import type { EntityVerbHandlers } from './verbs';

/** The word one of these kinds is spelled with in an address. */
type WallWord = 'people' | 'sites' | 'tags' | 'collections' | 'photo-sets' | 'songs';

/** The least a row has to be for these verbs: a name, and the count a delete names. */
export interface WallRow {
	id: string;
	name: string;
	/**
	 * Its whole-library count, for a delete's sentence; a tab wall counts in context, so absent.
	 */
	count?: number;
	/** Whether this account has hearted it, where the wall carries that fact. See `favoriteAll`. */
	favorite?: boolean;
	/** What it is drawn by, for the merge sheet; absent falls back to the letter. */
	picture?: ChoicePicture;
	/** In Hidden for this account (`vault` or `hidden` on the wire); absent is not hidden. */
	hidden?: boolean;
	/** The stars this account gave it, where the wall carries them. See `sharedRating`. */
	rating?: number | null;
	/** Whether this account pinned it to the top of its wall, where the wall carries that. */
	pinned?: boolean;
}

interface KindFacts {
	wall: WallWord;
	/** One of them, and several, for a sentence somebody reads. */
	one: string;
	many: string;
	/** What the tag endpoints call this kind, or null where it cannot carry a tag. */
	tagWall: TagWall | null;
	/** Whether it has a heart and stars; all five do. */
	opinions: boolean;
	/** What a stash-box calls this kind, or null where none has one. */
	enrichAs: EnrichSubject | null;
	/** What a merge calls it, or null where two of them can never turn out to be one. */
	mergeable: MergeableKind | null;
	/** What a delete takes with it, in the words each wall's own confirmation already used. */
	consequence: (rows: WallRow[]) => string;
	/**
	 * The word the share and hide routes call it, or null; Share, Visibility and Hide follow it.
	 */
	reach: ShareableType | null;
	/** The longest name the server takes for this kind (a tag's is 64, the others 120). */
	nameLimit: number;
}

/** How many things these rows hold or are on, added up. One number is the size of the decision. */
function held(rows: WallRow[]): number {
	return rows.reduce((total, row) => total + (row.count ?? 0), 0);
}

/** How much comes off, as a phrase: a real sum only when every row has a count. */
function amount(rows: WallRow[], whole: string): string {
	return rows.some((row) => row.count === undefined) ? whole : filesSaid(held(rows));
}

/** What differs between the kinds; the fuller sentence wins, since a confirmation is read once. */
export const KIND_FACTS: Record<EntityKind, KindFacts> = {
	person: {
		wall: 'people',
		one: 'person',
		many: 'people',
		tagWall: 'people',
		opinions: true,
		enrichAs: 'person',
		mergeable: 'person',
		reach: 'person',
		nameLimit: 120,
		consequence: (rows) =>
			`${rows.length === 1 ? 'They come' : 'They all come'} off ${amount(rows, 'every file they are on')}, their other names are forgotten, and any sharing set on them is forgotten. The files themselves aren't touched.`
	},
	site: {
		wall: 'sites',
		one: 'Site',
		many: 'Sites',
		tagWall: 'sites',
		opinions: true,
		enrichAs: 'site',
		mergeable: 'site',
		reach: 'site',
		nameLimit: 120,
		consequence: (rows) =>
			`What Sift recorded about where ${amount(rows, 'the files filed under it')} came from is forgotten too, along with any sharing set on ${rows.length === 1 ? 'it' : 'them'}. The people stay, and so do the files \u2014 only the Site they were attributed to goes.`
	},
	tag: {
		wall: 'tags',
		one: 'tag',
		many: 'tags',
		tagWall: null,
		opinions: true,
		enrichAs: 'tag',
		mergeable: null,
		reach: 'tag',
		nameLimit: 64,
		consequence: (rows) =>
			`${rows.length === 1 ? 'It comes' : 'They come'} off ${amount(rows, 'every file carrying it')}, and any sharing set on ${rows.length === 1 ? 'it' : 'them'} is forgotten. The files themselves aren't touched.`
	},
	collection: {
		wall: 'collections',
		one: 'collection',
		many: 'collections',
		tagWall: 'collections',
		opinions: true,
		enrichAs: null,
		mergeable: null,
		reach: 'collection',
		nameLimit: 120,
		consequence: (rows) =>
			`${rows.length === 1 ? 'It stops' : 'They stop'} holding ${amount(rows, 'everything in it')}, and any sharing set on ${rows.length === 1 ? 'it' : 'them'} is forgotten. The files themselves aren't touched and stay exactly where they are.`
	},
	photo_set: {
		wall: 'photo-sets',
		one: 'Photo Set',
		many: 'Photo Sets',
		tagWall: 'photo-sets',
		opinions: true,
		enrichAs: null,
		mergeable: null,
		reach: 'photo_set',
		nameLimit: 120,
		consequence: (rows) => {
			const count = held(rows);
			const many = rows.some((row) => row.count === undefined)
				? 'pictures in it'
				: count === 1
					? '1 picture'
					: `${counted(count)} pictures`;
			return `The ${many} stay exactly where they are on disk. Only the grouping goes.`;
		}
	},
	/* A song: shared, restricted and hidden like the rest; no tags or stash-box; it merges. */
	song: {
		wall: 'songs',
		one: 'song',
		many: 'songs',
		tagWall: null,
		opinions: true,
		enrichAs: null,
		mergeable: 'song',
		reach: 'song',
		nameLimit: 200,
		consequence: (rows) =>
			`${rows.length === 1 ? 'It comes' : 'They come'} off ${amount(rows, 'every file carrying it')}, and those files' Music field is left empty. The files themselves aren't touched.`
	}
};

/** Renaming sends the name alone; a tag's goes through the tag store. */
async function writeName(
	kind: EntityKind,
	wall: WallWord,
	id: string,
	name: string
): Promise<void> {
	if (kind === 'tag') {
		await tagStore.rename(id, name);
		return;
	}
	await api.put(`/${wall}/${id}` as ApiPath, { body: { name } });
}

/* The walls whose rows a store holds, so a rename draws there on the press. */
function heldBy(kind: EntityKind): { items: { id: string; name: string }[] } | null {
	if (kind === 'person') return people;
	if (kind === 'site') return sites;
	if (kind === 'collection') return collections;
	return null;
}

/** Draw the new name now; the function returned puts the old one back. */
function drawName(kind: EntityKind, id: string, name: string): () => void {
	const store = heldBy(kind);
	const before = store?.items.find((one) => one.id === id)?.name;
	if (!store || before === undefined) return () => {};
	const swap = (from: string, to: string) => {
		store.items = store.items.map((one) =>
			one.id === id && one.name === from ? { ...one, name: to } : one
		);
	};
	swap(before, name);
	return () => swap(name, before);
}

/** What a wall tells this file, as functions so the answers stay live. */
interface WallAround {
	kind: () => EntityKind;
	rows: () => WallRow[];
	/** Read the wall again, because something changed that it cannot work out for itself. */
	changed: () => void;
	/** Let go of whatever is picked, once a verb over a selection has landed. */
	clear: () => void;
	/** Whether this wall offers Pin. */
	pins?: boolean;
}

/** The verbs and their flows for one wall; a class, so the handlers exist during setup. */
export class WallVerbs {
	#around: WallAround;

	/** The row being renamed, and the name being typed over it. */
	renaming = $state<WallRow | null>(null);
	renameOpen = $state(false);
	renameTo = $state('');

	/** What the sharing panel is open on. A list, because the bar shares a whole selection. */
	sharing = $state<ShareTarget[]>([]);
	shareOpen = $state(false);

	/** What the reach report is open on. One thing, never a list. See the verb. */
	reaching = $state<ShareTarget | null>(null);
	reachOpen = $state(false);

	/** What is about to be deleted, held so the sentence can name it after the menu has gone. */
	confirming = $state<WallRow[]>([]);
	confirmOpen = $state(false);

	/** The rows being folded into one. */
	merging = $state<WallRow[]>([]);
	mergeOpen = $state(false);

	/** What the "why is this hidden" panel is open on, from a card's Hidden mark. */
	hiddenAbout = $state<ShareTarget | null>(null);
	hiddenOpen = $state(false);

	constructor(around: WallAround) {
		this.#around = around;
	}

	/** Read the wall again; public, for the sheets drawn beside the wall. */
	changed(): void {
		this.#around.changed();
	}

	get facts(): KindFacts {
		return KIND_FACTS[this.#around.kind()];
	}

	get kind(): EntityKind {
		return this.#around.kind();
	}

	/** The rows these ids name, in the order they were given, skipping any that have gone. */
	rowsFor(ids: string[]): WallRow[] {
		const held = this.#around.rows();
		return ids
			.map((id) => held.find((row) => row.id === id))
			.filter((row): row is WallRow => row !== undefined);
	}

	/** What the Sharing and Visibility panels are open on, named once for both. */
	targetsOf(ids: string[]): ShareTarget[] {
		const type = this.facts.reach;
		// A kind with no sharing of its own has nothing for either panel to be open on.
		if (!type) return [];
		return this.rowsFor(ids).map((row) => ({ type, id: row.id, label: row.name }));
	}

	/** Whether all are hidden (Hide reads Unhide); the menu and the bar both read it. */
	allHidden(ids: string[]): boolean {
		const rows = this.rowsFor(ids);
		return rows.length > 0 && rows.length === ids.length && rows.every((row) => row.hidden);
	}

	/** The stars all of these share, or null; never just the pressed row's. */
	sharedRating(ids: string[]): number | null {
		const rows = this.rowsFor(ids);
		if (rows.length === 0) return null;
		const first = rows[0].rating ?? null;
		return rows.every((row) => (row.rating ?? null) === first) ? first : null;
	}

	/** Whether all are pinned; mixed reads as not, so the verb pins the lot. */
	allPinned(ids: string[]): boolean {
		const rows = this.rowsFor(ids);
		return rows.length === ids.length && allPinned(rows.map((row) => row.pinned));
	}

	/** Whether all are favorites; mixed reads as not, as `favoriteAll` acts. */
	allFavorite(ids: string[]): boolean {
		const rows = this.rowsFor(ids);
		return rows.length > 0 && rows.length === ids.length && rows.every((row) => row.favorite);
	}

	/** Open the "why is this hidden" panel on one row: the card's Hidden mark. */
	askAboutHidden(id: string): void {
		this.hiddenAbout = this.targetsOf([id])[0] ?? null;
		if (this.hiddenAbout) this.hiddenOpen = true;
	}

	/** A sheet over a selection landed: let the picks go and read the wall again. */
	applied(): void {
		this.#around.clear();
		this.#around.changed();
	}

	/** The list the Tag row opens into; null where this kind cannot carry a tag. */
	get tagPick(): VerbPick | null {
		const facts = this.facts;
		if (!facts.tagWall) return null;
		/* Not cleared after a pick: the flyout stays up for the next one. */
		return tagPickerFor(facts.tagWall, facts.many, () => this.#around.changed());
	}

	/** Every verb as handlers; a getter, since one tab strip draws five kinds. */
	get handlers(): EntityVerbHandlers {
		const facts = this.facts;
		const handlers: EntityVerbHandlers = {
			rename: (ids) => this.askToRename(ids),
			remove: (ids) => this.askToDelete(ids)
		};
		/* Sharing, the reach report and Hidden, where the kind has them. */
		if (facts.reach) {
			handlers.share = (ids) => this.askToShare(ids);
			handlers.visibility = (ids) => this.reachOf(ids);
			handlers.hide = (ids, wanted) => void this.hideAll(ids, wanted);
		}
		const tagPick = this.tagPick;
		if (tagPick) handlers.tag = tagPick;
		if (facts.opinions) {
			handlers.favorite = (ids) => void this.favoriteAll(ids);
			handlers.rate = (ids, stars) => void this.rateAll(ids, stars);
		}
		const asking = facts.enrichAs;
		if (asking) {
			const stands = this.enrichment(asking);
			// The flyout's box, so a per-box row asks only that box.
			handlers.enrich = (ids, box) => {
				if (stands.anyRefused(ids)) return void sayKeptLocal();
				void enrichMany(asking, ids, box ?? '');
			};
			/* To its own page with the chooser open, rather than a second copy of that screen. */
			handlers.lookUp = (ids) => {
				// Refused before navigating, so no page opens under a question nothing may ask.
				if (stands.anyRefused(ids)) return void sayKeptLocal();
				void goto(`${pageOf(this.#around.kind(), ids[0])}?enrich=1`);
			};
			/*
			 * Keeping local is offered where Enrich is; the reply marks the holder so the row
			 * reverses.
			 */
			handlers.keepLocal = (ids, kept) => {
				for (const id of ids) {
					void setKeptLocal(asking, id, kept).then((state) => stands.mark([id], state));
				}
			};
		}
		if (facts.mergeable) handlers.merge = (ids) => this.askToMerge(ids);
		if (this.#around.pins) handlers.pin = (ids, wanted) => void this.pinAll(ids, wanted);
		return handlers;
	}

	/** Enrichment standing, one holder per kind: one instance serves several tabs. */
	#standing = new Map<EnrichSubject, EntityEnrichment>();

	enrichment(subject: EnrichSubject): EntityEnrichment {
		const held = this.#standing.get(subject) ?? new EntityEnrichment(subject);
		this.#standing.set(subject, held);
		return held;
	}

	// --- the flows ----------------------------------------------------------------------------

	askToRename(ids: string[]): void {
		const row = this.rowsFor(ids)[0];
		if (!row) return;
		this.renaming = row;
		this.renameTo = row.name;
		this.renameOpen = true;
	}

	/** Write the typed name. Quiet on a name that has not changed: that is a press, not an edit. */
	async rename(): Promise<void> {
		const row = this.renaming;
		const wanted = this.renameTo.trim();
		this.renaming = null;
		if (!row || !wanted || wanted === row.name) return;
		const facts = this.facts;
		const putBack = drawName(this.#around.kind(), row.id, wanted);
		try {
			await writeName(this.#around.kind(), facts.wall, row.id, wanted);
			this.#around.changed();
		} catch (error) {
			putBack();
			// 409: the name is taken, perhaps in another capitalisation.
			toasts.show(
				error instanceof ApiError && error.status === 409
					? `There's already a ${facts.one} called "${wanted}"`
					: "That name couldn't be saved",
				{ tone: 'error' }
			);
		}
	}

	askToShare(ids: string[]): void {
		this.sharing = this.targetsOf(ids);
		if (this.sharing.length > 0) this.shareOpen = true;
	}

	reachOf(ids: string[]): void {
		this.reaching = this.targetsOf(ids)[0] ?? null;
		if (this.reaching) this.reachOpen = true;
	}

	/** Every pick reaches the merge sheet by id, held here or not, so the merge is not reversed. */
	askToMerge(ids: string[]): void {
		if (ids.length === 0) return;
		const held = this.rowsFor(ids);
		this.merging = ids.map((id) => held.find((row) => row.id === id) ?? { id, name: '' });
		this.mergeOpen = true;
	}

	askToDelete(ids: string[]): void {
		const rows = this.rowsFor(ids);
		if (rows.length === 0) return;
		this.confirming = rows;
		this.confirmOpen = true;
	}

	/** What the confirmation says, in the words that kind's own wall already used. */
	get deleteTitle(): string {
		const facts = this.facts;
		const rows = this.confirming;
		if (rows.length === 1) return `Delete "${rows[0].name}"?`;
		return `Delete ${counted(rows.length)} ${facts.many}?`;
	}

	get deleteConsequence(): string {
		return this.confirming.length === 0 ? '' : this.facts.consequence(this.confirming);
	}

	get deleteLabel(): string {
		const rows = this.confirming;
		return rows.length === 1 ? `Delete ${this.facts.one}` : `Delete ${rows.length}`;
	}

	/** Delete them one at a time, so a refusal partway leaves a readable result. */
	async remove(): Promise<void> {
		const rows = this.confirming;
		const facts = this.facts;
		if (rows.length === 0) return;
		try {
			for (const row of rows) await api.del(`/${facts.wall}/${row.id}` as ApiPath);
			this.#around.clear();
		} catch {
			toasts.show(
				rows.length === 1
					? `That ${facts.one} couldn't be deleted`
					: "Some of those couldn't be deleted",
				{ tone: 'error' }
			);
		} finally {
			this.confirming = [];
			this.#around.changed();
		}
	}

	/** Into the vault or out; the sentence, Undo and refusals are `setHidden`'s. */
	async hideAll(ids: string[], wanted: boolean): Promise<void> {
		if (ids.length === 0) return;
		const facts = this.facts;
		const moved = await setHidden(ids, wanted, {
			noun: facts.one,
			plural: facts.many,
			stays: vault.unlocked,
			set: (id, flag) => api.put(`/${facts.wall}/${id}/vault` as ApiPath, { body: { vault: flag } })
		});
		/* A library change, so other screens hear it; this wall reads again below either way. */
		if (moved.length > 0) libraryChanges.changed();
		this.#around.clear();
		this.#around.changed();
	}

	/** One target state for the set: any not yet a favorite makes them all favorites. */
	async favoriteAll(ids: string[]): Promise<void> {
		const rows = this.#around.rows();
		/* A wall without hearts answers "not yet", so the press turns them all on. */
		const wanted = ids.some((id) => !rows.find((row) => row.id === id)?.favorite);
		await this.#opinion(ids, 'favorite', { favorite: wanted });
	}

	async rateAll(ids: string[], rating: number | null): Promise<void> {
		await this.#opinion(ids, 'rating', { rating });
	}

	/** Pin or unpin the whole pick; the wall is ordered by it, so it is read again. */
	async pinAll(ids: string[], wanted: boolean): Promise<void> {
		if (ids.length === 0) return;
		const landed = await pinAll(this.facts.wall, ids, wanted, () => {});
		if (!landed) return;
		this.#around.clear();
		this.#around.changed();
	}

	async #opinion(ids: string[], what: string, body: object): Promise<void> {
		const facts = this.facts;
		for (const id of ids) {
			try {
				await api.put(`/${facts.wall}/${id}/${what}` as ApiPath, { body });
			} catch {
				toasts.show("That couldn't be saved", { tone: 'error' });
				return;
			}
		}
		this.#around.clear();
		this.#around.changed();
	}
}
