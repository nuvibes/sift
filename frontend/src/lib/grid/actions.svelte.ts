/*
 * The things you can do to a file, or to a set of them: favorite, hide, share, delete, save, copy.
 *
 * Not inside the grid: search results are the same tiles over the same layout and need the same
 * selection, and a second copy of these is the shape every drift starts as: one copy gains a
 * confirmation the other never gets; one clears the selection when it finishes and one leaves the
 * bar up over a job that is over.
 *
 * So the actions are here, once, and a screen supplies the three things that genuinely differ: how
 * to look a file up, how to stop showing one, and how to record that one has changed. Nothing here
 * knows what a grid row is or how a screen is laid out.
 *
 * What is deliberately NOT here: the dialogs. Sharing and deleting both ask a question first, and a
 * dialog is a piece of a screen. What this owns is the decision about which files an answer applies
 * to; the screen owns the asking.
 */

import { counted } from '$lib/entity/entity-counts';
import { api, ApiError } from '$lib/api/client';
import { copyText } from '$lib/shell/clipboard';
import { saveAsset, saveEach } from '$lib/capture/copy-out';
import { collections } from '$lib/library/collections.svelte';
import type { Selection } from '$lib/components/common';
import type { Choice } from '$lib/components/common/PickDialog.svelte';
import { setHidden as applyHidden } from '$lib/library/hiding';
import { libraryChanges } from '$lib/library/changes.svelte';
import { people } from '$lib/people/people.svelte';
import { photoSets } from '$lib/library/photo-sets.svelte';
import { songs } from '$lib/entity/songs.svelte';
import type { ShareTarget } from '$lib/library/sharing';
import { tags } from '$lib/entity/tags.svelte';
import {
	announceRefusal,
	announceSkipped,
	mergeBulk,
	overChunks,
	type BulkWriteDone
} from '$lib/library/bulk';
import { toasts } from '$lib/shell/toasts.svelte';
import { vault } from '$lib/shell/vault.svelte';
import type { components } from '$lib/api/schema';
import type { OpinionPatch } from '$lib/library/changes.svelte';
import { setFilesPinned } from '$lib/library/pinning.svelte';

/** The little of a file these actions need to know. Both the grid's rows and a search result
 *  already carry all of it, which is what makes one implementation possible. */
/**
 * The least a row has to be for the bar over a selection to act on it.
 *
 * Taken out of the server's own definition of a file rather than described again here: `media_type`
 * decides whether Save copies or downloads, `original_filename` names the copy, and the rest is
 * what the verbs read back. `hidden` is optional here and not there: a screen that never draws
 * the vault does not carry it, and over something already hidden the Hide verb points the other
 * way, so its absence and its being false are the same answer.
 */
export type Actionable = Pick<
	components['schemas']['AssetSummary'],
	'id' | 'media_type' | 'favorite' | 'rating' | 'concealed' | 'original_filename'
> &
	Partial<Pick<components['schemas']['AssetSummary'], 'hidden' | 'pinned'>>;

/**
 * How one of the five "put these files on that" writes is being made.
 *
 * `keepSelection` is the picker's, and the picker is every door's Add to (the right-click menu, a
 * file's own, the selection bar's): a right press keeps the flyout up for the next pick, and the
 * next pick acts on the SAME selection. Clearing it after the first would leave the flyout
 * standing over a selection that was not there any more. A caller that finishes the job in one
 * write leaves it off, and the selection goes with it, which is what takes the bar away.
 */
export interface PutOnHow {
	keepSelection?: boolean;
}

export interface Surroundings<Item extends Actionable> {
	/** One file as this screen holds it, or undefined once it has gone. */
	lookup: (id: string) => Item | undefined;
	/** What is picked. Cleared by every action that finishes, which is what takes the bar away,
	 *  except a write from the menu's picker, which keeps it (see `PutOnHow`). */
	selection: Selection;
	/**
	 * Move this screen's own copy of a file, before the server has answered.
	 *
	 * The heart on a tile moves at once and the server's answer is kept when it lands. A screen
	 * with nothing to move (one that simply re-reads) leaves this out.
	 */
	setState?: (
		id: string,
		state: OpinionPatch,
		keep?: (item: { favorite: boolean }) => boolean
	) => void;
	/** Stop showing this file. Left out by a screen that would rather re-read than edit in place. */
	forget?: (id: string) => void;
	/** Re-read, because something changed that this screen cannot work out for itself. */
	refresh?: () => void;
	/**
	 * Whether a file still belongs on this screen once its heart has moved.
	 *
	 * Favorites shows favorites, so un-hearting one there takes it off. Everywhere else it stays.
	 */
	stillBelongs?: (item: { favorite: boolean }) => boolean;
	/** Whether this screen is the one that shows hidden files. Unhiding there is what takes a row
	 *  off it, and hiding again would be doing nothing, twice. */
	showingHidden?: () => boolean;
}

export type DeleteMode = 'sift' | 'disk';

/** The six things a file can be put on from the bar or the menu. */
type LandedKind = 'tag' | 'collection' | 'person' | 'site' | 'photo_set' | 'song';

/**
 * How each kind says what just happened, and where its name goes.
 *
 * A table rather than five `if`s inside the sentence builder: what differs between the five is
 * WORDING, and wording written inline among the counting is wording that gets edited for one kind
 * and not the other four. Read straight down, this is also the list somebody checks when the
 * product decides a Site is called something else.
 *
 * `lead` stops before the name, which follows it as a link (a toast's piece). `many` is the whole sentence, because several things picked at once have no one
 * page to point at.
 */
const LANDED: Record<
	LandedKind,
	{
		wall: string;
		lead: (files: string, single: boolean) => string;
		many: (files: string, single: boolean, onto: number) => string;
	}
> = {
	tag: {
		wall: '/tags',
		lead: (files, single) => `${files} ${single ? 'was' : 'were'} tagged`,
		many: (files, single, onto) =>
			`${files} ${single ? 'was' : 'were'} tagged with ${counted(onto)} tags`
	},
	collection: {
		wall: '/collections',
		// "added to", like the other four: Add is the one word for joining a list, and the sheet
		// that sent it says Add. That a collection is a place where a file takes a position in an
		// order is true, and not a second verb's worth.
		lead: (files, single) => `${files} ${single ? 'was' : 'were'} added to the collection`,
		many: (files, single, onto) =>
			`${files} ${single ? 'was' : 'were'} added to ${counted(onto)} collections`
	},
	person: {
		wall: '/people',
		// A person is named bare. "added to the person Ada" is how a database talks about
		// somebody; "added to Ada" is how anybody else does.
		lead: (files, single) => `${files} ${single ? 'was' : 'were'} added to`,
		many: (files, single, onto) =>
			`${files} ${single ? 'was' : 'were'} added to ${counted(onto)} people`
	},
	site: {
		wall: '/sites',
		// The project's own word, capitalised as the product capitalises it. "site" is the plain
		// gloss and is right in a sentence explaining what a Site is; it is wrong as the name.
		lead: (files, single) => `${files} ${single ? 'was' : 'were'} added to the Site`,
		many: (files, single, onto) =>
			`${files} ${single ? 'was' : 'were'} added to ${counted(onto)} Sites`
	},
	photo_set: {
		wall: '/photo-sets',
		lead: (files, single) => `${files} ${single ? 'was' : 'were'} added to the Photo Set`,
		many: (files, single, onto) =>
			`${files} ${single ? 'was' : 'were'} added to ${counted(onto)} Photo Sets`
	},
	/* A file carries one song, so "added to the song" may also have moved it off another one;
	   the sentence says where it is now, which is the fact. Several songs at once is a pick that
	   cannot be kept (the last one wins), so `many` is never reached from a menu, and says so
	   plainly if it ever is. */
	song: {
		wall: '/songs',
		lead: (files, single) => `${files} ${single ? 'was' : 'were'} added to the song`,
		many: (files, single) => `${files} ${single ? 'was' : 'were'} added to a song`
	}
};

export class AssetActions<Item extends Actionable> {
	#around: Surroundings<Item>;

	constructor(around: Surroundings<Item>) {
		this.#around = around;
	}

	get #selection(): Selection {
		return this.#around.selection;
	}

	/**
	 * Pin, for one file or a whole selection: keep it at the top of whatever wall it is on.
	 *
	 * ONE target state for the whole set, decided by the caller and passed in, not a toggle per
	 * file. That is the same rule the heart below follows and the same one the five entity walls
	 * follow: over a set where some are pinned and some are not, toggling each into whatever it was
	 * not leaves the set MORE mixed than it started, and there is no sentence that describes what
	 * the row did.
	 *
	 * Sent as ONE request for the selection. The reply is counts and not opinions (a list write
	 * names no rows, by design), so the row is settled onto the value that was ASKED for, which is
	 * not in doubt once nothing has been skipped. The whole opinion still comes back on the
	 * single-file write `setFilePinned` does, and every screen holding one of these is told the new
	 * opinion over the live channel either way.
	 *
	 * !! Without the drop test, for the same reason the heart is: a pinned file is still a member
	 * of every wall it was on, so nothing leaves, but a wall ORDERED by the pin has to be re-read
	 * for the row to move, and that is `refresh`, not `setState`.
	 */
	async pin(ids: string[], pinned: boolean): Promise<void> {
		const before = ids.map((id) => this.#around.lookup(id));
		for (const one of before) {
			if (!one) continue;
			this.#around.setState?.(one.id, {
				favorite: one.favorite,
				rating: one.rating,
				pinned
			});
		}
		/* The write itself is `pinning`'s, not this file's. A collection's own wall pins from a
		   right-click menu without a selection anywhere near it, and two spellings of one
		   address is how one opinion comes to be written four different ways. ONE request for
		   the selection. */
		const done = await setFilesPinned(ids, pinned);
		if (done.skipped > 0) {
			// All or nothing on the screen, exactly as the entity walls are: a wall showing three of
			// five pinned after one press is a wall nobody can reason about. The reply says how many
			// were left out and never which, so what is on screen goes back to what it was and the
			// re-read below shows whatever did land.
			for (const one of before) {
				if (!one) continue;
				this.#around.setState?.(one.id, {
					favorite: one.favorite,
					rating: one.rating,
					pinned: one.pinned ?? false
				});
			}
			announceSkipped(done);
			this.#around.refresh?.();
			return;
		}
		// The wall is ORDERED by this, so the rows have to be read again for anything to move. The
		// heart does not need it and does not do it; this does, and that is the whole difference.
		this.#around.refresh?.();
		this.#done(ids, pinned ? 'Pinned' : 'Unpinned');
		this.#selection.clear();
	}

	/**
	 * Favorite, for one file or a whole selection.
	 *
	 * A selection is set to a single target state: if any of them is not yet a favorite, the
	 * press makes them all favorites. Toggling each into whatever it was not is the reading that
	 * does nothing useful on the common press.
	 */
	async favorite(ids: string[]): Promise<void> {
		const wanted = ids.some((id) => !this.#around.lookup(id)?.favorite);
		const before = ids.map((id) => this.#around.lookup(id));
		for (const one of before) {
			if (!one) continue;
			// Optimistic, and deliberately WITHOUT the drop test: a row removed on the way out has
			// nowhere to come back to if the write is refused, and putting it back where it was is
			// not something this can do. The heart still moves at once; only the leaving waits.
			this.#around.setState?.(one.id, { favorite: wanted, rating: one.rating });
		}
		/* ONE request for the whole selection, not one per file: a selection of a hundred and
		   thirty-four would be a hundred and thirty-four round trips, each awaited before the
		   next began. `overChunks` splits anything over the server's own cap. */
		const done = await overChunks(ids, (chunk) =>
			api.post<BulkWriteDone>('/assets/favorite', { body: { asset_ids: chunk, favorite: wanted } })
		);
		if (done.skipped > 0) {
			/* All or nothing on the SCREEN, which is the rule the pin above already follows. The
			   reply says how many were left out and never which (naming them is what the access
			   model refuses to do), so the only rows this can be sure of are the ones it started
			   with. What did land stays landed on the server and the re-read below shows it. */
			for (const one of before) {
				if (!one) continue;
				this.#around.setState?.(one.id, { favorite: one.favorite, rating: one.rating });
			}
			announceSkipped(done);
			this.#around.refresh?.();
			this.#selection.clear();
			return;
		}
		/* Settled onto the value that was written rather than onto a per-file reply, because the
		   list route answers counts and not opinions. And the value is not in doubt: the request
		   said what it wanted and nothing was skipped. The drop test belongs here, where the write
		   is known to have landed. */
		for (const one of before) {
			if (!one) continue;
			this.#around.setState?.(
				one.id,
				{ favorite: wanted, rating: one.rating },
				this.#around.stillBelongs
			);
		}
		if (!this.#around.setState) this.#around.refresh?.();
		// Said out loud only when it was aimed at more than one file. A heart on a single tile moves
		// under the pointer that pressed it, and a message about something already on the screen is
		// noise; a bar covering forty says nothing at all without this, because the tiles it changed
		// may all be scrolled past.
		this.#done(ids, wanted ? 'Favorited' : 'Removed from Favorites');
		// The action is over, so the selection is too. Leaving it picked leaves the bar up over a
		// job that has finished, and the next click somewhere else acts on files nobody meant.
		this.#selection.clear();
	}

	/**
	 * One sentence after a verb that worked, or nothing at all for a single file.
	 *
	 * The rule is the same everywhere: an action whose whole effect is visible where it happened
	 * says nothing, and an action covering a set says what it did. Written once so the six verbs
	 * that finish quietly cannot each decide it differently, three of them with no sentence and
	 * three with one.
	 */
	#done(ids: string[], verb: string): void {
		if (ids.length < 2) return;
		toasts.show(`${verb} ${counted(ids.length)} files`, { tone: 'success' });
	}

	/**
	 * Hide, or unhide: the same action pointed the other way.
	 *
	 * Whether the file leaves the screen depends on the screen rather than on the file. Hiding
	 * needs a PIN to EXIST, not the vault to be open, so it can be done either way and the two
	 * have opposite answers: with the vault shut the file is gone from here and the tile goes with
	 * it; with the vault OPEN it is still perfectly visible and only gains a mark saying so.
	 */
	async hide(ids: string[], hide: boolean): Promise<void> {
		const leaves = hide ? !vault.unlocked : (this.#around.showingHidden?.() ?? false);
		const moved = await applyHidden(ids, hide, {
			noun: 'file',
			set: (id, wanted) => api.put(`/assets/${id}/vault`, { body: { vault: wanted } }),
			/* The whole selection in ONE request. The per-file `set` above stays because the five
			   other kinds that hide have no list endpoint, and because the undo of a single file is
			   still a single write. */
			setMany: (wanted, vault) =>
				api.post<BulkWriteDone>('/assets/vault', { body: { asset_ids: wanted, vault } }),
			forget: leaves ? (id) => this.#around.forget?.(id) : undefined,
			stays: !leaves
		});
		if (moved.length === 0) return;
		this.#selection.clear();
		// Staying put still means the file has changed: it wears the Hidden mark now, and the
		// panel behind it has a new line. Nothing else re-reads it, so this does.
		if (!leaves) libraryChanges.changed();
		if (leaves && !this.#around.forget) this.#around.refresh?.();
	}

	/** The files a share dialog is about to be pointed at, named as a person would recognize them. */
	shareTargets(ids: string[]): ShareTarget[] {
		return ids.map((id) => ({
			type: 'item' as const,
			id,
			label: this.#around.lookup(id)?.original_filename ?? 'This file'
		}));
	}

	/**
	 * Carry out a delete somebody has already agreed to.
	 *
	 * **ONE request for the whole selection, and that is the point of it.** One per file, awaited
	 * in turn, each dropping its own tile the instant it answered, would flicker its way down the
	 * wall one picture at a time, and each would announce that the library had changed, so every
	 * screen holding a list would re-read once per file. The work is never the cost; the round
	 * trips are.
	 *
	 * So the rows go together, once, after the answer arrives. The wall changes in one step, which
	 * is what somebody who pressed one button is expecting to see.
	 *
	 * **It is not a transaction and must not become one.** Bytes deleted from a disk cannot be put
	 * back, so a set that fails partway really has deleted what it deleted: the server counts
	 * what went and what would not, and the two sentences below say exactly that rather than
	 * pretending it was all or nothing.
	 *
	 * One toast for what went and one for what would not, rather than one per file: a selection
	 * spanning a read-only folder would otherwise raise the same message thirty times.
	 */
	async remove(ids: string[], mode: DeleteMode): Promise<void> {
		if (ids.length === 0) return;
		/* The server's own shape, not a copy of it written here. A hand-rolled `{ removed, refused,
		   reason }` says nothing when the server moves: it goes quietly wrong, and the screen is
		   where somebody finds out. `components` is generated from the schema the server produces
		   and CI refuses a mismatch, so this cannot drift without something saying so. */
		let answer: BulkWriteDone;
		try {
			answer = await overChunks(ids, (chunk) =>
				api.post<BulkWriteDone>('/assets/delete', {
					body: { asset_ids: chunk, mode }
				})
			);
		} catch (failure) {
			// The whole request failed rather than individual files: nothing was deleted, so
			// nothing is dropped from the wall.
			this.#selection.clear();
			toasts.show(
				failure instanceof ApiError ? (failure.detail ?? failure.message) : "Couldn't delete those",
				{ tone: 'error' }
			);
			return;
		}

		/*
		 * Which rows to drop, and why it is not simply "the ones that went".
		 *
		 * The server says HOW MANY went, not which, and it cannot usefully say which, because a
		 * refusal is about a folder rather than about a file, so the set that failed is not
		 * something a client can act on row by row. Where everything went, everything goes; where
		 * anything was refused, the page is read again and the truth comes back from the server.
		 * Dropping a guess would leave a tile missing for a file that is still there.
		 */
		if (answer.skipped === 0) {
			for (const id of ids) this.#around.forget?.(id);
			if (!this.#around.forget) this.#around.refresh?.();
		} else {
			this.#around.refresh?.();
		}
		this.#selection.clear();

		/*
		 * Said ONCE, after the whole set, rather than per file. Reclaim space and the duplicate
		 * queue are the ones that matter: both are lists of files stored more than once or looking
		 * alike, and deleting one of a pair is exactly the event that settles a pair.
		 */
		if (answer.changed > 0) libraryChanges.changed();

		if (answer.changed > 0) {
			const what = answer.changed === 1 ? 'file' : `${counted(answer.changed)} files`;
			toasts.show(
				mode === 'sift'
					? `Removed ${what} from Sift. The ${answer.changed === 1 ? 'file is' : 'files are'} still on disk.`
					: `Deleted ${what} from disk`,
				{ tone: 'success' }
			);
		}
		// Said after the success, and only once: what went is the answer to what was asked, and
		// what would not go is the exception to it. Through the shared announcer, so this screen
		// and the six other bulk writes say it the same way and all offer the same button.
		announceSkipped(answer);
	}

	/**
	 * Save from the menu. One file is copied or downloaded by its own type; a whole selection is
	 * downloaded, each file at once.
	 */
	async save(ids: string[]): Promise<void> {
		const only = ids.length === 1 ? this.#around.lookup(ids[0]) : undefined;
		if (only) {
			await saveAsset(only);
			this.#selection.clear();
			return;
		}
		const chosen = ids
			.map((id) => this.#around.lookup(id))
			.filter((asset): asset is Item => asset !== undefined && !asset.concealed);
		/* Awaited: it asks the server whether each file is actually there before it claims the
		   download happened. The selection is cleared afterwards, which is also the honest order:
		   it clears when the work is done rather than while it is still going. */
		await saveEach(chosen);
		this.#selection.clear();
	}

	/**
	 * Set the rating on every file being acted on, and on no others.
	 *
	 * One value for the whole set rather than a nudge each: the stars are a choice with five
	 * answers, so "three stars" means three stars on all of them. The optimistic write goes out per
	 * file because the endpoint is per file, and a failure part-way stops rather than carrying on:
	 * a half-rated selection with no message is worse than a refusal.
	 */
	async rate(ids: string[], rating: number | null): Promise<void> {
		const before = ids.map((id) => this.#around.lookup(id));
		for (const one of before) {
			if (!one) continue;
			this.#around.setState?.(one.id, { favorite: one.favorite, rating });
		}
		// ONE request for the whole selection, for the reason the heart above gives.
		const done = await overChunks(ids, (chunk) =>
			api.post<BulkWriteDone>('/assets/rating', { body: { asset_ids: chunk, rating } })
		);
		if (done.skipped > 0) {
			// Every row back where it was, and a re-read. See the heart above for why a partial
			// answer cannot be applied to named rows.
			for (const one of before) {
				if (!one) continue;
				this.#around.setState?.(one.id, { favorite: one.favorite, rating: one.rating });
			}
			announceSkipped(done);
			this.#around.refresh?.();
			this.#selection.clear();
			return;
		}
		for (const one of before) {
			if (!one) continue;
			this.#around.setState?.(
				one.id,
				{ favorite: one.favorite, rating },
				this.#around.stillBelongs
			);
		}
		if (!this.#around.setState) this.#around.refresh?.();
		this.#done(ids, rating === null ? 'Cleared the rating on' : `Rated ${rating} stars,`);
		this.#selection.clear();
	}

	/**
	 * What landed, on what, as a sentence a person would say, and the name as a link.
	 *
	 * ## Why each kind has its own wording
	 *
	 * One shared `3 files went on 1 tag.` for all five is one sentence doing five jobs: it counts
	 * the things instead of naming them, so somebody who has just filed forty clips under a person
	 * is told a number they already knew and not the name they were about to check. "went on 1
	 * collection" is not a sentence anybody says out loud, and the one thing worth a click (the
	 * collection) is not there to click.
	 *
	 * So each kind has its own wording, because each kind IS a different event: a tag goes ON a
	 * file, a file goes INTO a collection, and a person is simply named. The name is handed over as
	 * a piece of its own (see `ToastPiece`), so the punctuation has one author and
	 * nothing here searches a finished sentence for a substring.
	 *
	 * ## Why the names come from the caller
	 *
	 * The caller has just been handed the very rows somebody ticked: `Choice`s, with the id and
	 * the name in them. Asking a store to look the name up again would be a second answer to a
	 * question already answered, and on a kind whose list is not loaded on this screen it would be
	 * a request to make a sentence with.
	 *
	 * A name that did not arrive is not a reason to say nothing: the count wording is the fallback,
	 * which is also what an OLDER caller passing ids alone gets.
	 */
	#landed(ids: string[], chosen: readonly Choice[], kind: LandedKind, skipped = 0): void {
		/* What actually landed, not what was asked for. Saying "3 files went on 1 tag" beside a
		   second toast saying one of them could not be included is the app contradicting itself in
		   two lines, and the person is left counting. */
		const landed = ids.length - skipped;
		const files = landed === 1 ? 'The file' : `${counted(landed)} files`;
		const one = LANDED[kind];
		/* One name, and we know it: the sentence names it and the name is a link. Several, or one
		   whose name never arrived: the count, and nothing to follow: a link has to go SOMEWHERE,
		   and there is no page for "2 photo sets". */
		const only = chosen.length === 1 ? chosen[0] : undefined;
		if (only && only.name) {
			const href = `${one.wall}/${encodeURIComponent(only.id)}`;
			toasts.show(
				[`${one.lead(files, landed === 1)} `, { text: only.name, kind, id: only.id, href }],
				{
					tone: 'success'
				}
			);
			return;
		}
		toasts.show(`${one.many(files, landed === 1, chosen.length)}`, { tone: 'success' });
	}

	/**
	 * Put tags on every file being acted on.
	 *
	 * One request for the whole set, which is what the endpoint takes. Nothing is removed: a bar
	 * that toggled would take a tag off half a selection and put it on the other half, and neither
	 * half is what anybody meant by pressing Tag.
	 */
	async tag(
		ids: string[],
		chosen: readonly Choice[],
		how: PutOnHow = {}
	): Promise<BulkWriteDone | null> {
		if (chosen.length === 0) return null;
		try {
			const done = await tags.assign(
				ids,
				chosen.map((one) => one.id)
			);
			libraryChanges.changed();
			if (done.changed > 0) this.#landed(ids, chosen, 'tag', done.skipped);
			announceSkipped(done);
			if (!how.keepSelection) this.#selection.clear();
			return done;
		} catch {
			toasts.show("That tag couldn't be added", { tone: 'error' });
			return null;
		}
	}

	/** Add every file being acted on to each chosen collection, at the end of it. */
	async collect(
		ids: string[],
		chosen: readonly Choice[],
		how: PutOnHow = {}
	): Promise<BulkWriteDone | null> {
		if (chosen.length === 0) return null;
		try {
			// One call per collection, because that is the shape of the endpoint: it takes a
			// collection and the items going into it, so the whole selection goes in ONE request
			// per destination, never one per file. Sequential rather than at once, so a failure
			// part-way is one message about one thing instead of a race of them.
			const each: BulkWriteDone[] = [];
			for (const one of chosen) each.push(await collections.add(one.id, ids));
			const done = mergeBulk(each);
			libraryChanges.changed();
			if (done.changed > 0) this.#landed(ids, chosen, 'collection', done.skipped);
			announceSkipped(done);
			if (!how.keepSelection) this.#selection.clear();
			return done;
		} catch {
			toasts.show("Those couldn't be added", { tone: 'error' });
			return null;
		}
	}

	/** File every file being acted on under each chosen person. Moves nothing on disk. */
	async assign(
		ids: string[],
		chosen: readonly Choice[],
		how: PutOnHow = {}
	): Promise<BulkWriteDone | null> {
		if (chosen.length === 0) return null;
		try {
			const done = await people.assign(
				ids,
				chosen.map((one) => one.id)
			);
			libraryChanges.changed();
			if (done.changed > 0) this.#landed(ids, chosen, 'person', done.skipped);
			announceSkipped(done);
			if (!how.keepSelection) this.#selection.clear();
			return done;
		} catch {
			toasts.show("That person couldn't be added", { tone: 'error' });
			return null;
		}
	}

	/** Say that every file being acted on came from each chosen site. Moves nothing on disk. */
	async site(
		ids: string[],
		chosen: readonly Choice[],
		how: PutOnHow = {}
	): Promise<BulkWriteDone | null> {
		if (chosen.length === 0) return null;
		try {
			const done = await people.filedUnder(
				ids,
				chosen.map((one) => one.id)
			);
			libraryChanges.changed();
			if (done.changed > 0) this.#landed(ids, chosen, 'site', done.skipped);
			announceSkipped(done);
			if (!how.keepSelection) this.#selection.clear();
			return done;
		} catch {
			toasts.show("Those couldn't be added to a Site", { tone: 'error' });
			return null;
		}
	}

	/** Put every file being acted on into each chosen set. Moves nothing on disk. */
	async photoSet(
		ids: string[],
		chosen: readonly Choice[],
		how: PutOnHow = {}
	): Promise<BulkWriteDone | null> {
		if (chosen.length === 0) return null;
		try {
			// One call per set, because that is the shape of the endpoint: it takes a set and the
			// pictures going into it, so the whole selection goes in ONE request per destination,
			// never one per picture. Sequential rather than at once, so a failure part-way is one
			// message about one thing instead of a race of them.
			const each: BulkWriteDone[] = [];
			for (const one of chosen) each.push(await photoSets.add(one.id, ids));
			const done = mergeBulk(each);
			libraryChanges.changed();
			if (done.changed > 0) this.#landed(ids, chosen, 'photo_set', done.skipped);
			announceSkipped(done, 'picture');
			if (!how.keepSelection) this.#selection.clear();
			return done;
		} catch {
			toasts.show("Those couldn't be added", { tone: 'error' });
			return null;
		}
	}

	/**
	 * Put every file being acted on on the chosen song. Moves nothing on disk.
	 *
	 * A file carries ONE song, so a file already on another moves to this one; the server counts
	 * it as changed and the sentence says where it landed. One song is written whatever was
	 * handed over: a pick is one row, and several would only leave the files on the last.
	 */
	async song(
		ids: string[],
		chosen: readonly Choice[],
		how: PutOnHow = {}
	): Promise<BulkWriteDone | null> {
		const one = chosen[0];
		if (!one) return null;
		try {
			const done = await songs.add(one.id, ids);
			libraryChanges.changed();
			if (done.changed > 0) this.#landed(ids, [one], 'song', done.skipped);
			announceSkipped(done);
			if (!how.keepSelection) this.#selection.clear();
			return done;
		} catch {
			toasts.show("Those couldn't be added to the song", { tone: 'error' });
			return null;
		}
	}

	/**
	 * Move every file being acted on into one folder, on the disk.
	 *
	 * Through the server's own file-moving route, one file at a time, because that route is where
	 * the checks live: it is the only path that verifies the folder was handed over read-write,
	 * refuses a name that would collide, and records the move so it can be undone. Nothing here
	 * touches a path, and there is no second way to do this.
	 *
	 * One destination for the whole set, asked once before this is called. A refusal on one file is
	 * reported once at the end rather than per file, the way a delete does it.
	 */
	async move(ids: string[], folderId: string): Promise<void> {
		if (ids.length === 0 || !folderId) return;
		// One request per chunk of the selection rather than one per file: the server moves each
		// file exactly as the single route does and reports the counts, so a refusal on one file is
		// reported once at the end rather than per file, the way a delete does it.
		const done = await overChunks(ids, (chunk) =>
			api.post<BulkWriteDone>('/assets/move', { body: { asset_ids: chunk, folder_id: folderId } })
		);
		this.#selection.clear();
		if (done.changed > 0) {
			libraryChanges.changed();
			toasts.show(`Moved ${done.changed === 1 ? 'the file' : `${counted(done.changed)} files`}`, {
				tone: 'success'
			});
		}
		if (done.skipped > 0) {
			// The server's own sentence, which is the product here: "hand the folder over as
			// read-write" is what somebody needs in order to fix it.
			toasts.show(done.reason ?? "Some of those couldn't be moved", { tone: 'error' });
		}
	}

	/** A link to the file's own page, on the clipboard. Absolute, so it works pasted anywhere. */
	async copyLink(id: string): Promise<void> {
		const url = new URL(`/asset/${id}`, location.origin).href;
		// Through the helper, not the clipboard API directly: that one does not exist at all on a
		// plain-http address, which is how a self-hosted Sift is normally reached.
		if (await copyText(url)) {
			toasts.show('Link copied', { tone: 'success' });
		} else {
			toasts.show("The link couldn't be copied", { tone: 'error' });
		}
	}
}
