/*
 * How each entity verb is carried out, for any of the five kinds of named thing, on any wall.
 *
 * `verbs.ts` declares which verbs exist and in what order both surfaces draw them. A verb is
 * offered where a wall hands over a handler, and the handlers live here, so a person's card offers
 * the same verbs on the People wall and on a tag's People tab.
 *
 * One registry, keyed by the kind. A wall says what kind of thing its rows are and asks for the
 * handlers. What differs between the five is `KIND_FACTS` below: the word in the address, the words
 * in a sentence, and which verbs that kind has at all. A tag cannot carry a tag and no stash-box
 * has a collection, so those walls get no handler and neither surface offers it.
 *
 * Two spellings of one word, deliberately kept apart: `person` is the subject (what a share, an
 * enrichment and a merge call it), `people` is the wall (what an address says). Carried as two
 * fields because `photo_set` and `photo-sets` differ by more than a letter, and a rule guessing one
 * from the other would guess wrong once.
 *
 * Six callers, one answer: the People, Sites, Tags, Collections and Photo Sets walls each build
 * one, and so does `RelatedWall` for the walls on an entity's tabs. Every flow below (share,
 * visibility, hidden, delete, merge, the tag sheet, rename) lives here once.
 * `wall-verbs.walls.test.ts` refuses a wall that imports one of those sheets again.
 *
 * The pin, for a wall that asks for it (`pins`). It is the one verb that changes where a row
 * belongs; the re-read is `changed`, which every wall hands over, and the rows are here through
 * `rows`. `pinning.svelte.ts` owns the write. `RelatedWall` asks for it on the tabs whose rows pin.
 */

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
import { toasts } from '$lib/shell/toasts.svelte';
import { vault } from '$lib/shell/vault.svelte';
import type { EntityVerbHandlers } from './verbs';

/** The word one of these kinds is spelled with in an address. */
type WallWord = 'people' | 'sites' | 'tags' | 'collections' | 'photo-sets' | 'songs';

/**
 * The least a row has to be for these verbs to act on it.
 *
 * A name, because every sentence these draw names what it is about, and a count because the one
 * question a delete has to answer is how much comes off with it. Nothing else: a wall of related
 * rows and a wall of the things themselves carry different shapes, and the whole point of this file
 * is that the verbs do not care which wall they were reached from.
 */
export interface WallRow {
	id: string;
	name: string;
	/**
	 * How much this thing holds or is on in the whole library, for the sentence a delete says.
	 *
	 * Absent is a real answer, and it is the one a tab wall gives: a wall reached through an
	 * entity's tabs counts in context (a person on a tag's People tab reads "25 items" meaning 25
	 * of that tag's files), while deleting her takes her off every file she is on. Handing that
	 * number over would make the confirmation understate what the button does. So a wall hands over
	 * a count only when it is the whole one, and the sentence says it in words where there is none.
	 * See `amount`.
	 */
	count?: number;
	/** Whether this account has hearted it, where the wall carries that fact. See `favoriteAll`. */
	favorite?: boolean;
	/**
	 * What this thing is drawn by, where the wall holding it knows.
	 *
	 * For the merge sheet, which reads a row by its picture before its name, as the People wall is
	 * read. A wall reached through a tab must hand this over too, or every candidate card falls
	 * back to a coloured letter.
	 *
	 * Optional, and absent is still a real answer: a wall whose rows carry no cover columns hands
	 * nothing over and the card falls back to the letter.
	 */
	picture?: ChoicePicture;
	/**
	 * Whether this account has it in Hidden, where the wall carries that fact.
	 *
	 * One spelling here for what the five kinds spell two ways on the wire (`vault` on a person,
	 * a collection and a Photo Set, `hidden` on a Site and a tag) so the question "are all of
	 * these already hidden" is asked in one place. Absent reads as not hidden.
	 */
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
	/**
	 * What this kind is called by the tag endpoints, or null where it cannot carry a tag.
	 *
	 * The same word as `wall` for the four that can, and it is written out rather than reused: the
	 * tag routes are their own list, and a kind added to one of the two lists and not the other is
	 * exactly the drift this table exists to make visible.
	 */
	tagWall: TagWall | null;
	/**
	 * Whether it has a heart and stars.
	 *
	 * All five do: the server has a `/favorite` and a `/rating` route for a tag and a collection
	 * too (`tags_ratings/router.py`, `collections/router.py`), and their cards draw the heart and
	 * the stars, so the menu offers them as well. Kept as a field so a sixth kind without them is
	 * one word here.
	 */
	opinions: boolean;
	/**
	 * What a stash-box calls this kind, or null where no stash-box has one at all.
	 *
	 * The same shape as the two fields around it rather than a yes-or-no, and the type checker
	 * is what made that call rather than a preference: a collection is somebody's own grouping
	 * and no stash-box has one, so there is no word to send, while a boolean would have let the
	 * request be built for it anyway.
	 */
	enrichAs: EnrichSubject | null;
	/** What a merge calls it, or null where two of them can never turn out to be one. */
	mergeable: MergeableKind | null;
	/** What a delete takes with it, in the words each wall's own confirmation already used. */
	consequence: (rows: WallRow[]) => string;
	/**
	 * Whether it has sharing and Hidden of its own, as the server's grant and hide tables know it,
	 * or null where it is only ever seen through its files.
	 *
	 * The word the share and hide routes call it, rather than a yes-or-no, for the reason
	 * `enrichAs` gives: a kind with none would have nothing to share, restrict or hide, and the
	 * type checker then refuses to build a share panel for one. The three verbs (Share,
	 * Visibility, Hide) are offered exactly where this is set.
	 */
	reach: ShareableType | null;
	/**
	 * The longest name the server takes for this kind, so the rename box stops where the route does.
	 *
	 * A tag's is 64 (`MAX_TAG_NAME`) and the other four are 120. One limit of 120 for all five
	 * would let a tag name between the two be typed in full and then refused with a sentence that
	 * could not say why.
	 */
	nameLimit: number;
}

/** How many things these rows hold or are on, added up. One number is the size of the decision. */
function held(rows: WallRow[]): number {
	return rows.reduce((total, row) => total + (row.count ?? 0), 0);
}

/**
 * How much comes off, as a phrase.
 *
 * A real number where every row handed one over, and the words `whole` otherwise. Any row missing
 * its count makes the whole sum wrong rather than small, so one missing is enough.
 */
function amount(rows: WallRow[], whole: string): string {
	return rows.some((row) => row.count === undefined) ? whole : filesSaid(held(rows));
}

/**
 * Everything that differs between the five kinds.
 *
 * One table, so a card's menu says the same things on a tab as it does on the wall that thing lives
 * on. Where wording could go either way, the sentence that names the most is the one kept: a
 * confirmation is read once, by somebody about to do something that cannot be undone.
 */
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
	/* A song: a piece of music some files carry. No tags of its own and no stash-box knows one.
	   It is shared, restricted and hidden like every other named thing (the grant and hide tables
	   know `song`), and a decision on it reaches the files that carry it. Two songs can turn out to
	   be one piece of music (a name typed by hand and the one AcoustID gave), so it merges. */
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

/**
 * Renaming, which is the one write with a quirk per kind.
 *
 * Every route reads an absent field as "leave it alone", so a rename sends a name and nothing else.
 * A tag's goes through the tag store, because the store holds the Tags wall's rows and puts the new
 * name on the one it holds.
 */
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

/**
 * What a wall has to tell this file about itself.
 *
 * Every one of them is a function so the answers stay live: a wall re-reads, and the rows these
 * verbs name have to be the rows it is holding now rather than the ones it held when the flows were
 * built.
 */
interface WallAround {
	/** What kind of thing this wall's rows are. */
	kind: () => EntityKind;
	/** The rows themselves, for the names and counts every sentence here is built from. */
	rows: () => WallRow[];
	/** Read the wall again, because something changed that it cannot work out for itself. */
	changed: () => void;
	/** Let go of whatever is picked, once a verb over a selection has landed. */
	clear: () => void;
	/**
	 * Whether this wall offers Pin: where a pin moves the row to the top of the very list on
	 * screen, which is every entity wall and every tab of named things. See the top of this file.
	 */
	pins?: boolean;
}

/**
 * The verbs, and the flows they open, for one wall.
 *
 * A class rather than a component so the wall can hand the handlers straight to `entityVerbs`
 * during setup: a component's exports are not readable until it has mounted, and the menu is
 * built before that. `EntityWallFlows.svelte` draws the sheets this holds open.
 */
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

	/**
	 * What the "why is this hidden" panel is open on. One thing, like the reach report.
	 *
	 * Opened from the card's own Hidden mark rather than from a verb, and held here all the same:
	 * it is a sheet about a row of this kind, so every wall, and every wall on an entity's tabs,
	 * draws the same one.
	 */
	hiddenAbout = $state<ShareTarget | null>(null);
	hiddenOpen = $state(false);

	constructor(around: WallAround) {
		this.#around = around;
	}

	/**
	 * Read the wall again.
	 *
	 * Public because the sheets are drawn by a component beside the wall rather than by the wall
	 * itself: a share applied or a merge landed changes what the wall should be showing, and the
	 * component would otherwise need the same callback handed to it a second time: two ways of
	 * saying one thing, free to be wired to two different re-reads.
	 */
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

	/**
	 * What the two panels are open on, named once for both.
	 *
	 * Sharing and Visibility ask two questions about the same thing, so they take the same targets
	 * rather than each building its own: two copies of this mapping is two chances for the panels to
	 * disagree about what a row is called.
	 */
	targetsOf(ids: string[]): ShareTarget[] {
		const type = this.facts.reach;
		// A kind with no sharing of its own has nothing for either panel to be open on.
		if (!type) return [];
		return this.rowsFor(ids).map((row) => ({ type, id: row.id, label: row.name }));
	}

	/**
	 * Whether every one of these is already hidden, which is when Hide reads Unhide.
	 *
	 * Read by the menu and the bar alike, so the two cannot disagree about which way the verb
	 * points. An id the wall is not holding counts as not hidden, and so does an empty pick.
	 */
	allHidden(ids: string[]): boolean {
		const rows = this.rowsFor(ids);
		return rows.length > 0 && rows.length === ids.length && rows.every((row) => row.hidden);
	}

	/**
	 * The stars every one of these shares, or null where they do not share one.
	 *
	 * Over the whole pick, never the row that was pressed: the pressed row's own stars would make a
	 * menu opened over a selection of forty show one of them.
	 */
	sharedRating(ids: string[]): number | null {
		const rows = this.rowsFor(ids);
		if (rows.length === 0) return null;
		const first = rows[0].rating ?? null;
		return rows.every((row) => (row.rating ?? null) === first) ? first : null;
	}

	/**
	 * Whether every one of these is already pinned, which is when Pin reads Unpin.
	 *
	 * Mixed reads as not pinned, so the verb over a mixed pick pins the lot: `allPinned`'s rule,
	 * the one every surface that pins reads.
	 */
	allPinned(ids: string[]): boolean {
		const rows = this.rowsFor(ids);
		return rows.length === ids.length && allPinned(rows.map((row) => row.pinned));
	}

	/**
	 * Whether every one of these is already a favorite, which is when the heart's row reads
	 * "Remove from favorites". Mixed reads as not, the reading `favoriteAll` acts on, so the words
	 * and the press agree. An id the wall is not holding counts as not, and so does an empty pick.
	 */
	allFavorite(ids: string[]): boolean {
		const rows = this.rowsFor(ids);
		return rows.length > 0 && rows.length === ids.length && rows.every((row) => row.favorite);
	}

	/** Open the "why is this hidden" panel on one row: the card's Hidden mark. */
	askAboutHidden(id: string): void {
		this.hiddenAbout = this.targetsOf([id])[0] ?? null;
		if (this.hiddenAbout) this.hiddenOpen = true;
	}

	/**
	 * A sheet over a selection has landed: let the picks go and read the wall again.
	 *
	 * For the share panel's Apply and for a merge. Both are verbs over a selection, and the rule
	 * for those is `clear` and then `changed`, in one place, so a share applied from a tab and the
	 * same share applied from the wall both let the picks go.
	 */
	applied(): void {
		this.#around.clear();
		this.#around.changed();
	}

	/**
	 * The list the Tag row opens out into, on the bar and the menu alike.
	 *
	 * Null where this kind cannot carry a tag, so the row is not drawn at all.
	 */
	get tagPick(): VerbPick | null {
		const facts = this.facts;
		if (!facts.tagWall) return null;
		/*
		 * The selection is not cleared after a pick: a right press keeps the flyout up for the next
		 * one, over the same selection.
		 */
		return tagPickerFor(facts.tagWall, facts.many, () => this.#around.changed());
	}

	/**
	 * Every verb this kind of thing has, as handlers.
	 *
	 * A getter rather than a field, because the kind can change under it: one tab strip draws five
	 * different walls and the component is not rebuilt between them.
	 */
	get handlers(): EntityVerbHandlers {
		const facts = this.facts;
		const handlers: EntityVerbHandlers = {
			rename: (ids) => this.askToRename(ids),
			remove: (ids) => this.askToDelete(ids)
		};
		/* Sharing, the reach report and Hidden, where the kind has them of its own: every kind
		   does today, and a kind added without them is left out here rather than offered and
		   refused. */
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
			// The flyout's box, handed on, so a per-box row asks only that box. Nothing named is
			// the Settings choice. See `autoEnrichRows`.
			handlers.enrich = (ids, box) => {
				if (stands.anyRefused(ids)) return void sayKeptLocal();
				void enrichMany(asking, ids, box ?? '');
			};
			/* Straight to its own page with the chooser already open. The chooser needs what Sift
			   already holds for every field, to draw beside what the box offers, and that page's own
			   save to apply a draft through, so the menu goes to the record screen rather than
			   growing a second copy of it. */
			handlers.lookUp = (ids) => {
				// Refused before the navigation, for the reason the walls give: a row must not take
				// somebody to a page they did not ask for and put a sheet over it asking a question
				// nothing may ask.
				if (stands.anyRefused(ids)) return void sayKeptLocal();
				void goto(`${pageOf(this.#around.kind(), ids[0])}?enrich=1`);
			};
			/* Keeping local is a stash-box question, so it is offered exactly where Enrich is. The
			   same answer for every id, as Hide gives, and the reply is marked on the holder the two
			   Enrich rows read, so the row reverses without a second ask. */
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

	/**
	 * Where each row stands with enrichment, one holder per KIND of thing this wall can draw.
	 *
	 * Per kind and not one, because a single instance of this class serves several tabs (a tag's
	 * People tab and its Sites tab are the same object) and a holder keyed only by id would
	 * answer a person's question with a Site's row. See the note over `handlers`.
	 */
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
		try {
			await writeName(this.#around.kind(), facts.wall, row.id, wanted);
			this.#around.changed();
		} catch (error) {
			// 409 is the one worth naming: the name is taken, perhaps in another capitalisation,
			// which a wall showing only one of them cannot convey. Every kind says so.
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

	/**
	 * Every id picked reaches the merge sheet, held by this wall or not. Filtering through
	 * `rowsFor` would drop an id the wall is not holding, shrinking the selection to one and
	 * turning the merge the wrong way round. The sheet reads every pick by id itself, so a row not
	 * held here is handed over as its id; one that is really gone is refused on the sheet, in
	 * words.
	 */
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

	/**
	 * Delete them, one at a time and in order.
	 *
	 * A refusal partway leaves a result somebody can read: the ones before it are gone and the ones
	 * after are still there. Sent one by one because the endpoint is one by one.
	 */
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

	/**
	 * Into the vault, or back out of it.
	 *
	 * The shared sentence, the shared Undo and the shared refusal when no PIN has been set are all
	 * `setHidden`'s: this hands it the one thing only the wall knows, which is the address.
	 */
	async hideAll(ids: string[], wanted: boolean): Promise<void> {
		if (ids.length === 0) return;
		const facts = this.facts;
		const moved = await setHidden(ids, wanted, {
			noun: facts.one,
			plural: facts.many,
			stays: vault.unlocked,
			set: (id, flag) => api.put(`/${facts.wall}/${id}/vault` as ApiPath, { body: { vault: flag } })
		});
		/* Hiding is a library change (what belongs on every scoped list just moved) so the bell
		   rings for the other screens, as the People store's own write did. The wall itself is read
		   again below either way, because a wall on an entity's tabs does not listen for the bell. */
		if (moved.length > 0) libraryChanges.changed();
		this.#around.clear();
		this.#around.changed();
	}

	/**
	 * One target state for the whole set, the way the file grid does it.
	 *
	 * If any of them is not yet a favorite the press makes them all favorites. Toggling each into
	 * whatever it was not is the reading that does nothing useful on the common press.
	 */
	async favoriteAll(ids: string[]): Promise<void> {
		const rows = this.#around.rows();
		/* A wall whose rows do not carry the heart answers "not yet" for every one of them, which
		   makes the press turn them all on: the same answer a mixed selection gets, and the one
		   that leaves a set somebody can describe. */
		const wanted = ids.some((id) => !rows.find((row) => row.id === id)?.favorite);
		await this.#opinion(ids, 'favorite', { favorite: wanted });
	}

	async rateAll(ids: string[], rating: number | null): Promise<void> {
		await this.#opinion(ids, 'rating', { rating });
	}

	/**
	 * Pin them, or take the pin out: one target state for the whole pick.
	 *
	 * The wall is ORDERED by this on the server, so it is read again for anything to move; the
	 * mark is left to that read rather than settled here first, because this file holds no rows of
	 * its own to settle it on. A refusal is said by `pinAll`, and nothing is let go.
	 */
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
