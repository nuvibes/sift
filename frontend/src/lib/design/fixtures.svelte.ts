/*
 * The stand-in data the gallery draws its specimens from.
 *
 * ## Why this is a module and not a block of `const`s on the page
 *
 * Fixtures invented where each is needed (lists of fake people, fake tags, fake assets, one per
 * section) do not agree with each other: the gallery would show the tag chip with one set of tags
 * and the tag editor with another. That is the same failure the components on this page exist to
 * fix, one level up.
 *
 * One place, one set of names, one library. Every specimen on the page draws from it.
 *
 * ## The two halves
 *
 * **Invented** is most of it: shapes the components take as props, written out below. Nothing here
 * touches the server and nothing here can fail.
 *
 * **Borrowed** is the handful of components that fetch their OWN data from an id: the faces on a
 * file, what a person appears in, how well Sift recognises them. Those cannot be faked from here
 * without reimplementing the thing being drawn, which is the one thing the gallery may not do. So
 * `borrowed` asks the library for one asset and one person and hands their ids over.
 *
 * If the library is empty, or the request fails, the ids stay null and those specimens draw their
 * own empty state. That is not a degraded gallery: an empty state is a state, it is the one a
 * fresh install sees, and it is worth being able to look at.
 */
import { api } from '$lib/api/client';
import type { EditableAsset } from '$lib/edit/edit.svelte';
import type { MoveTarget } from '$lib/components/MoveDialog.svelte';
import type { ShareTarget } from '$lib/library/sharing';
import type { TileItem } from '$lib/components/Tile.svelte';
import type { components } from '$lib/api/schema';
import {
	LIVE,
	LOOKUP_KEY,
	LOOKUP_ROUTE_KEY,
	type LookupWire
} from '$lib/settings-ui/music-lookup.svelte';

/* LIVE: nothing moves it (the DESIGN GALLERY's samples, read once to draw its components with real data) */

/* ------------------------------------------------------------------ borrowed */

interface Borrowed {
	/** One file from the library, for the specimens that read a file's own connections. */
	assetId: string | null;
	/** One person, for the specimens that read a person's. */
	personId: string | null;
	/** Whether the two questions have been answered, either way. */
	asked: boolean;
}

const found = $state<Borrowed>({ assetId: null, personId: null, asked: false });

/** One request each, on the first call, however many specimens ask. */
let asking: Promise<void> | null = null;

async function ask(): Promise<void> {
	try {
		const assets = await api.get<components['schemas']['AssetPageResponse']>('/assets', {
			query: { limit: 1 }
		});
		found.assetId = assets.items[0]?.id ?? null;
	} catch {
		found.assetId = null;
	}
	try {
		const people = await api.get<components['schemas']['PeopleList']>('/people', {
			query: { limit: 1 }
		});
		found.personId = people.items[0]?.id ?? null;
	} catch {
		found.personId = null;
	}
	found.asked = true;
}

/**
 * One real file and one real person from this library, for the specimens that fetch their own.
 *
 * Reading it starts the request if nothing has yet. Null until it lands, and null for ever on an
 * empty library. See the note at the top for why that is a specimen rather than a failure.
 */
export function borrowed(): Borrowed {
	asking ??= ask();
	return found;
}

/* ----------------------------------------------------------------- invented */

/** A file, as the wall sees one. Enough for a tile to draw every mark it can. */
export const TILE: TileItem = {
	id: 'gallery-tile',
	media_type: 'video',
	duration_ms: 112_000,
	/* Part-way through, so the gallery draws the resume bar a wall draws on a tile somebody left
	   in the middle of. */
	resume_ms: 40_000,
	shared: true,
	shared_here: true,
	views: 3
};

/** The same file, as the editor sees one. */
export const EDITABLE: EditableAsset = {
	id: 'gallery-tile',
	media_type: 'video',
	width: 1920,
	height: 1080,
	duration_ms: 112_000,
	filename: 'a clip somebody imported.mp4',
	art: null,
	sprite: null
};

/** Somewhere to move things to. Two, so the picker has a choice to make. */
export const FOLDERS: readonly MoveTarget[] = [
	{ id: 'one', name: 'Sift Downloads', path: '/media/downloads' },
	{ id: 'two', name: 'Imported', path: '/media/imported' }
];

/** What a sharing sheet is open on. */
export const SHARING: ShareTarget[] = [{ type: 'item', id: 'gallery-tile', label: 'One clip' }];

/** People, wherever a specimen needs some. The same names throughout the page. */
export const PEOPLE = [
	{ id: 'p1', name: 'Someone' },
	{ id: 'p2', name: 'Somebody else' },
	{ id: 'p3', name: 'A third person' }
];

/** Tags, likewise. A coloured one and two plain, which is the mix an ordinary library has. */
export const TAGS = [
	{ id: 't1', name: 'beach' },
	{ id: 't2', name: 'sunset' },
	{ id: 't3', name: 'portrait' }
];

/* ------------------------------------------------------------ music lookup */

/**
 * The music lookup block, answering from memory: pressing its switch, saving a key or pressing Test
 * here changes nothing on the server and sends nothing to AcoustID. Only the declarations (the
 * words the switch and the route are drawn with, the disclosure among them) are read from the
 * live settings, so the gallery shows the server's own sentence rather than a copy of it.
 */
export function lookupInMemory(): LookupWire {
	let held = { on: false, key_set: false, key_ready: false, route: null as string | null };
	/* A few files owed a lookup once it could be asked, as the server counts them. */
	const now = () => ({
		...held,
		owed: held.on && held.key_ready ? 3 : 0,
		not_known: 0,
		/* Ask again's count and its wait, as the server answers them. */
		ask_again: 0,
		ask_again_after_days: 30,
		/* The Enrich menu's half of the answer: the lookup's word and name, and whether a press
		   would send anything. */
		source: 'acoustid',
		label: 'AcoustID',
		ready: held.on && held.key_ready
	});
	return {
		read: async () => now(),
		declared: () => LIVE.declared(),
		save: async (values) => {
			if (LOOKUP_KEY in values) held = { ...held, on: values[LOOKUP_KEY] === true };
			if (LOOKUP_ROUTE_KEY in values) {
				const route = values[LOOKUP_ROUTE_KEY];
				held = { ...held, route: typeof route === 'string' ? route : null };
			}
		},
		setKey: async () => {
			held = { ...held, key_set: true, key_ready: true };
			return now();
		},
		deleteKey: async () => {
			held = { ...held, key_set: false, key_ready: false };
			return now();
		},
		test: async () => ({ ok: true, said: 'Nothing was sent. This is the gallery.' }),
		askAgain: async () => ({
			job_id: null,
			queued: 0,
			left_out: 0,
			said: 'Nothing was sent. This is the gallery.'
		})
	};
}
