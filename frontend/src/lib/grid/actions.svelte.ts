/*
 * The things that can be done to a file or a set, once for every screen of tiles. A screen supplies
 * how to look one up, stop showing one and record a change; the dialogs are the screen's.
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

/** The least a row must be for the bar to act on it; `hidden` absent reads as false. */
export type Actionable = Pick<
	components['schemas']['AssetSummary'],
	'id' | 'media_type' | 'favorite' | 'rating' | 'concealed' | 'original_filename'
> &
	Partial<Pick<components['schemas']['AssetSummary'], 'hidden' | 'pinned'>>;

/** `keepSelection`: a right press on the picker keeps the flyout up for the next pick. */
export interface PutOnHow {
	keepSelection?: boolean;
}

export interface Surroundings<Item extends Actionable> {
	lookup: (id: string) => Item | undefined;
	/** Cleared by every action that finishes, except a picker write (see `PutOnHow`). */
	selection: Selection;
	/** Move this screen's copy before the server answers. */
	setState?: (
		id: string,
		state: OpinionPatch,
		keep?: (item: { favorite: boolean }) => boolean
	) => void;
	forget?: (id: string) => void;
	refresh?: () => void;
	/** Favorites shows favorites, so un-hearting one there takes it off. */
	stillBelongs?: (item: { favorite: boolean }) => boolean;
	showingHidden?: () => boolean;
}

export type DeleteMode = 'sift' | 'disk';

type LandedKind = 'tag' | 'collection' | 'person' | 'site' | 'photo_set' | 'song';

/** A table of WORDING per kind; `lead` stops before the name, which follows as a link. */
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
		lead: (files, single) => `${files} ${single ? 'was' : 'were'} added to the collection`,
		many: (files, single, onto) =>
			`${files} ${single ? 'was' : 'were'} added to ${counted(onto)} collections`
	},
	person: {
		wall: '/people',
		// A person is named bare.
		lead: (files, single) => `${files} ${single ? 'was' : 'were'} added to`,
		many: (files, single, onto) =>
			`${files} ${single ? 'was' : 'were'} added to ${counted(onto)} people`
	},
	site: {
		wall: '/sites',
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
	/* One song a file: the sentence says where it is now. */
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
	 * Pin to ONE target state for the whole set, in one request; a wall ordered by the pin re-reads
	 * (`refresh`), since nothing leaves it.
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
		/* The write is `pinning`'s, one spelling of one address. */
		const done = await setFilesPinned(ids, pinned);
		if (done.skipped > 0) {
			// All or nothing on the screen: the reply counts what was left out, never which.
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
		// Ordered by this, so re-read.
		this.#around.refresh?.();
		this.#done(ids, pinned ? 'Pinned' : 'Unpinned');
		this.#selection.clear();
	}

	/** One target state: any not yet a favorite makes them all favorites. */
	async favorite(ids: string[]): Promise<void> {
		const wanted = ids.some((id) => !this.#around.lookup(id)?.favorite);
		const before = ids.map((id) => this.#around.lookup(id));
		for (const one of before) {
			if (!one) continue;
			// Optimistic, WITHOUT the drop test: a removed row has nowhere to come back to.
			this.#around.setState?.(one.id, { favorite: wanted, rating: one.rating });
		}
		/* ONE request for the selection (`overChunks` splits at the server's cap). */
		const done = await overChunks(ids, (chunk) =>
			api.post<BulkWriteDone>('/assets/favorite', { body: { asset_ids: chunk, favorite: wanted } })
		);
		if (done.skipped > 0) {
			/* All or nothing on the screen, as the pin. */
			for (const one of before) {
				if (!one) continue;
				this.#around.setState?.(one.id, { favorite: one.favorite, rating: one.rating });
			}
			announceSkipped(done);
			this.#around.refresh?.();
			this.#selection.clear();
			return;
		}
		/* Settled on the value written; the drop test belongs here, where it landed. */
		for (const one of before) {
			if (!one) continue;
			this.#around.setState?.(
				one.id,
				{ favorite: wanted, rating: one.rating },
				this.#around.stillBelongs
			);
		}
		if (!this.#around.setState) this.#around.refresh?.();
		// Said only for more than one file.
		this.#done(ids, wanted ? 'Favorited' : 'Removed from Favorites');
		// The action is over, so the selection is too.
		this.#selection.clear();
	}

	/** Nothing for a single file, a sentence for a set. */
	#done(ids: string[], verb: string): void {
		if (ids.length < 2) return;
		toasts.show(`${verb} ${counted(ids.length)} files`, { tone: 'success' });
	}

	/** Whether the file leaves depends on whether the vault is open. */
	async hide(ids: string[], hide: boolean): Promise<void> {
		const leaves = hide ? !vault.unlocked : (this.#around.showingHidden?.() ?? false);
		const moved = await applyHidden(ids, hide, {
			noun: 'file',
			set: (id, wanted) => api.put(`/assets/${id}/vault`, { body: { vault: wanted } }),
			/* The whole selection in one request. */
			setMany: (wanted, vault) =>
				api.post<BulkWriteDone>('/assets/vault', { body: { asset_ids: wanted, vault } }),
			forget: leaves ? (id) => this.#around.forget?.(id) : undefined,
			stays: !leaves
		});
		if (moved.length === 0) return;
		this.#selection.clear();
		// Staying put, it still wears a new mark.
		if (!leaves) libraryChanges.changed();
		if (leaves && !this.#around.forget) this.#around.refresh?.();
	}

	shareTargets(ids: string[]): ShareTarget[] {
		return ids.map((id) => ({
			type: 'item' as const,
			id,
			label: this.#around.lookup(id)?.original_filename ?? 'This file'
		}));
	}

	/**
	 * A delete already agreed to, in ONE request, the rows going together. Not a transaction: bytes
	 * deleted cannot come back, so what went and what would not are each said once.
	 */
	async remove(ids: string[], mode: DeleteMode): Promise<void> {
		if (ids.length === 0) return;
		/* The server's own shape (`components`), generated and checked. */
		let answer: BulkWriteDone;
		const forget = this.#around.forget;
		if (forget) for (const id of ids) forget(id);
		try {
			answer = await overChunks(ids, (chunk) =>
				api.post<BulkWriteDone>('/assets/delete', {
					body: { asset_ids: chunk, mode }
				})
			);
		} catch (failure) {
			// The request failed whole: nothing was deleted, so the rows are read back.
			if (forget) this.#around.refresh?.();
			this.#selection.clear();
			toasts.show(
				failure instanceof ApiError ? (failure.detail ?? failure.message) : "Couldn't delete those",
				{ tone: 'error' }
			);
			return;
		}

		/*
		 * The server says HOW MANY went, not which: where anything was refused, the page is
		 * re-read.
		 */
		if (answer.skipped === 0) {
			if (!forget) this.#around.refresh?.();
		} else {
			this.#around.refresh?.();
		}
		this.#selection.clear();

		/* Said once; it settles pairs on the duplicate screens. */
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
		// Through the shared announcer, as every bulk write.
		announceSkipped(answer);
	}

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
		/* Awaited: it checks each file is there before claiming the download. */
		await saveEach(chosen);
		this.#selection.clear();
	}

	/** One value for the set; a failure part-way stops. */
	async rate(ids: string[], rating: number | null): Promise<void> {
		const before = ids.map((id) => this.#around.lookup(id));
		for (const one of before) {
			if (!one) continue;
			this.#around.setState?.(one.id, { favorite: one.favorite, rating });
		}
		const done = await overChunks(ids, (chunk) =>
			api.post<BulkWriteDone>('/assets/rating', { body: { asset_ids: chunk, rating } })
		);
		if (done.skipped > 0) {
			// Every row back, and a re-read.
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
	 * What landed, on what, as a person would say it, with the name as a link (`ToastPiece`). The
	 * names come from the caller's picks; without one, the count.
	 */
	#landed(ids: string[], chosen: readonly Choice[], kind: LandedKind, skipped = 0): void {
		/* What actually landed, not what was asked for. */
		const landed = ids.length - skipped;
		const files = landed === 1 ? 'The file' : `${counted(landed)} files`;
		const one = LANDED[kind];
		/* One known name is a link; several have no page to point at. */
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

	/** One request; nothing is removed. */
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

	async collect(
		ids: string[],
		chosen: readonly Choice[],
		how: PutOnHow = {}
	): Promise<BulkWriteDone | null> {
		if (chosen.length === 0) return null;
		try {
			// One request per collection, in turn, so a failure is one message.
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

	async photoSet(
		ids: string[],
		chosen: readonly Choice[],
		how: PutOnHow = {}
	): Promise<BulkWriteDone | null> {
		if (chosen.length === 0) return null;
		try {
			// One request per set, in turn.
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

	/** A file carries ONE song, so it moves to this one. */
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

	/** Through the server's file-moving route, where the checks and the undo record live. */
	async move(ids: string[], folderId: string): Promise<void> {
		if (ids.length === 0 || !folderId) return;
		// One request per chunk; a refusal is reported once at the end.
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
			// The server's sentence says how to fix it.
			toasts.show(done.reason ?? "Some of those couldn't be moved", { tone: 'error' });
		}
	}

	async copyLink(id: string): Promise<void> {
		const url = new URL(`/asset/${id}`, location.origin).href;
		if (await copyText(url)) {
			toasts.show('Link copied', { tone: 'success' });
		} else {
			toasts.show("The link couldn't be copied", { tone: 'error' });
		}
	}
}
