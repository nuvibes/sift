/**
 * What one file belongs to, as the band under its picture draws it: who is in it, which Sites it
 * is filed under, the collections, photo sets and song holding it, and its tags. Each list is its
 * own request and its own failure, since the picture is the point; each removal moves the chip at
 * once and puts it back with a sentence if the server refuses.
 */

/* LIVE: followed by lib/components/AssetView.svelte (the band read again on the library and jobs bells: followSoon) */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import type { CoverOf } from '$lib/components/EntityPreview.svelte';
import { songs } from '$lib/entity/songs.svelte';
import { tags as tagStore } from '$lib/entity/tags.svelte';
import { collections } from '$lib/library/collections.svelte';
import { filingsOf, removeFiling, type Filing } from '$lib/library/filings';
import { photoSets } from '$lib/library/photo-sets.svelte';
import { people as peopleStore } from '$lib/people/people.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

/** Enough of a person to draw them as a face, name them and link to their page. */
type PersonRef = Pick<components['schemas']['PersonView'], 'id' | 'name'> & CoverOf;

/** A collection, photo set or song holding the file, with the fields that name its cover. */
export type Membership = { kind: 'collection' | 'photo_set' | 'song' } & Pick<
	components['schemas']['CollectionSummary'],
	'id' | 'name'
> &
	CoverOf;

/** A tag as a chip draws it: an id and a name. */
type TagRef = Pick<components['schemas']['TagView'], 'id' | 'name'>;

/** The id, the name and the fields that name the cover, off a listing row of any kind. */
function coverOf(
	one: Pick<components['schemas']['CollectionSummary'], 'id' | 'name'> & CoverOf
): Omit<Membership, 'kind'> {
	return {
		id: one.id,
		name: one.name,
		cover_asset_id: one.cover_asset_id,
		cover_upload_id: one.cover_upload_id,
		cover_at_ms: one.cover_at_ms,
		art: one.art
	};
}

/* Asked with `?asset=` on the three lists, the leaf the walls filter by. */
async function membershipsOf(assetId: string): Promise<Membership[]> {
	const [held, shot, sung] = await Promise.all([
		api.get<components['schemas']['CollectionList']>('/collections', {
			query: { asset: assetId, limit: 50 }
		}),
		api.get<components['schemas']['PhotoSetList']>('/photo-sets', {
			query: { asset: assetId, limit: 50 }
		}),
		api.get<components['schemas']['SongList']>('/songs', {
			query: { asset: assetId, limit: 1 }
		})
	]);
	return [
		...held.items.map((one) => ({ kind: 'collection' as const, ...coverOf(one) })),
		...shot.items.map((one) => ({ kind: 'photo_set' as const, ...coverOf(one) })),
		...sung.items.map((one) => ({ kind: 'song' as const, ...coverOf(one) }))
	];
}

function moved(held: unknown, read: unknown): boolean {
	return JSON.stringify(held) !== JSON.stringify(read);
}

export class FileBand {
	people = $state<PersonRef[]>([]);
	/* Which Sites the file is filed under: a file reaches a Site through an account, so its own list. */
	filings = $state<Filing[]>([]);
	partOf = $state<Membership[]>([]);
	tags = $state<TagRef[]>([]);
	/* Which file the lists were last read for, so "nothing here" waits for their answers. */
	bandFor = $state<string | null>(null);
	/** Which file the view is about now, which an answer must still be about. */
	private current: () => string;

	constructor(current: () => string) {
		this.current = current;
	}

	readonly heldBy = $derived(this.partOf.filter((one) => one.kind === 'collection'));
	readonly shotIn = $derived(this.partOf.filter((one) => one.kind === 'photo_set'));
	/* The song the file carries: one at most. */
	readonly setTo = $derived(this.partOf.filter((one) => one.kind === 'song'));

	/* The one Site a song's name could have come from, or none where two could have. */
	readonly onlySite = $derived.by(() => {
		const names = new Set(
			this.filings.flatMap((one) => (one.site_id && one.site ? [one.site] : []))
		);
		return names.size === 1 ? ([...names][0] ?? null) : null;
	});

	/** A new file: the last one's people, Sites and tags must not sit under the new picture. */
	clear(): void {
		this.people = [];
		this.filings = [];
		this.tags = [];
		this.bandFor = null;
	}

	/** Read the lists for this file all at once, each drawn as it lands. */
	async load(wanted: string): Promise<void> {
		/* A re-read that found nothing new writes nothing, so no chip is drawn twice. */
		const here = () => wanted === this.current();
		await Promise.all([
			api
				.get<PersonRef[]>(`/assets/${wanted}/people`)
				.then((inIt) => {
					if (here() && moved(this.people, inIt)) this.people = inIt;
				})
				.catch(() => {
					// Kept as drawn: no row, on a first read.
				}),
			filingsOf(wanted)
				.then((under) => {
					if (here() && moved(this.filings, under)) this.filings = under;
				})
				.catch(() => {
					// Kept as drawn, which on a first read is a file filed under nothing.
				}),
			membershipsOf(wanted)
				.catch(() => [])
				.then((held) => {
					if (here() && moved(this.partOf, held)) this.partOf = held;
				}),
			api
				.get<TagRef[]>(`/assets/${wanted}/tags`)
				.catch(() => [])
				.then((put) => {
					if (here() && moved(this.tags, put)) this.tags = put;
				})
		]);
		if (here()) this.bandFor = wanted;
	}

	/** Take this file out of a collection, a photo set or a song, as the entity's page does. */
	async leave(one: Membership): Promise<void> {
		const previous = this.partOf;
		const id = this.current();
		this.partOf = this.partOf.filter((each) => !(each.kind === one.kind && each.id === one.id));
		try {
			if (one.kind === 'collection') await collections.removeItems(one.id, [id]);
			else if (one.kind === 'song') await songs.remove(one.id, [id]);
			else await photoSets.remove(one.id, [id]);
		} catch {
			this.partOf = previous;
			toasts.show("Couldn't remove that", { tone: 'error' });
		}
	}

	async untag(tagId: string): Promise<void> {
		const previous = this.tags;
		this.tags = this.tags.filter((one) => one.id !== tagId);
		try {
			await tagStore.assign([this.current()], [tagId], false);
		} catch {
			this.tags = previous;
			toasts.show("That tag couldn't be removed", { tone: 'error' });
		}
	}

	/* No confirm: nothing is deleted or moved, and the way back is one drop onto the Site's card. */
	async unfile(usernameId: string): Promise<void> {
		const previous = this.filings;
		this.filings = this.filings.filter((filing) => filing.username_id !== usernameId);
		try {
			await removeFiling(this.current(), usernameId);
		} catch {
			this.filings = previous;
			toasts.show("That couldn't be taken off", { tone: 'error' });
		}
	}

	/* Take somebody off this file; admin only, as assigning is. */
	async detach(personId: string): Promise<void> {
		const previous = this.people;
		this.people = this.people.filter((person) => person.id !== personId);
		try {
			await peopleStore.assign([this.current()], [personId], false);
		} catch {
			this.people = previous;
			toasts.show("That person couldn't be taken off", { tone: 'error' });
		}
	}
}
