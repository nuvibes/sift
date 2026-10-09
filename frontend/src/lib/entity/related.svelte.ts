/* The related walls an entity page can show, and the one place that knows what they are. */

import { api, type ApiPath } from '$lib/api/client';
import {
	historyOfCollection,
	historyOfPerson,
	historyOfPhotoSet,
	historyOfSite,
	historyOfSong,
	historyOfTag
} from '$lib/api/history';
import { fieldOf } from '$lib/components/shell/facet-labels';
import { LOOP_WORDS, WallWords, WORDS, wordsIn } from '$lib/components/shell/wall-words';
import { ARTIST_ORDER, ENTITY_OPINION_SORTS, UNIVERSAL_SORTS } from '$lib/grid/sort-state.svelte';
import { WallSort } from '$lib/grid/wall-sort.svelte';
import { page } from '$app/state';
import type { IconName } from '$lib/design/icons';
import type { Pinnable } from '$lib/library/pinning.svelte';
import type { components } from '$lib/api/schema';

/** What kind of thing an entity page is about. Its own row is never one of its own tabs. */
export type EntityKind = 'person' | 'tag' | 'site' | 'collection' | 'photo_set' | 'song';

/** What a tab can show. `files` is the page's own wall of files. */
export type RelatedKind =
	| 'files'
	| 'photo_sets'
	| 'loops'
	| 'tags'
	| 'tags_within'
	| 'people'
	| 'sites'
	| 'sites_within'
	| 'collections'
	| 'songs';

/** What a tab on an entity page can be, which is one more thing than a wall can. */
type TabKind = RelatedKind | 'history';

/** Every number the counts route answers with: the tabs, and the marks beside them. */
type CountKind = TabKind | 'disagreements' | 'files_bytes';

/** One row of a wall and several of them, lowercase, for a sentence somebody reads. */
interface RowNoun {
	one: string;
	many: string;
}

interface RelatedSpec {
	label: string;
	icon: IconName;
	/** The endpoint its wall is a page of. `files` has none: the media grid fetches its own. */
	path?: string;
	/** What ONE of its rows is called, and what several are. */
	noun: RowNoun;
	/** Whether pressing one of this wall's cards carries the page it was pressed from as a
	 * filter. */
	carries: boolean;
}

/* The walls, and what each is called. The words are the rail's words, because a tab named
 * anything the sidebar does not say is a second vocabulary for the same things. */
const SPECS: Record<RelatedKind, RelatedSpec> = {
	// Not a wall of cards at all (the media grid draws it, and a tile opens a file rather than a
	// page), so there is no press to carry anything and no card number to keep in step with one.
	files: { label: 'Files', icon: 'article', noun: { one: 'file', many: 'files' }, carries: false },
	photo_sets: {
		label: 'Photo Sets',
		icon: 'photo_library',
		path: '/photo-sets',
		noun: { one: 'Photo Set', many: 'Photo Sets' },
		carries: true
	},
	// A mark, not a card, for the reason `files` above is not one.
	loops: {
		label: 'Loops',
		icon: 'all_inclusive',
		path: '/loops',
		noun: { one: 'loop', many: 'loops' },
		carries: false
	},
	tags: {
		label: 'Tags',
		icon: 'shoppingmode',
		path: '/tags',
		noun: { one: 'tag', many: 'tags' },
		carries: true
	},
	// The tags filed directly under this one, read off `tags.parent_id`: the twin of `sites_within`
	// below, and plain for the same reason (a column on the row, not the page's files).
	tags_within: {
		label: 'Tags',
		icon: 'shoppingmode',
		path: '/tags',
		noun: { one: 'tag', many: 'tags' },
		carries: false
	},
	people: {
		label: 'People',
		icon: 'person',
		path: '/people',
		noun: { one: 'person', many: 'people' },
		carries: true
	},
	sites: {
		label: 'Sites',
		icon: 'public',
		path: '/sites',
		noun: { one: 'Site', many: 'Sites' },
		carries: true
	},
	// The same wall and the same glyph as the row above, under the word that says which question is
	// being asked of it.
	sites_within: {
		label: 'Sites',
		icon: 'public',
		path: '/sites',
		noun: { one: 'Site', many: 'Sites' },
		carries: false
	},
	// Plain for a second reason, and it is the destination's rather than this wall's: a
	// collection's Files wall is the arrangement somebody made by hand, it takes no query at all
	// and says so on the bar.
	collections: {
		label: 'Collections',
		icon: 'box',
		path: '/collections',
		noun: { one: 'collection', many: 'collections' },
		carries: false
	},
	// The wall a song is a row of, and so where a song's page is (`pageOf`).
	songs: {
		label: 'Music',
		icon: 'music_note_2',
		path: '/songs',
		noun: { one: 'song', many: 'songs' },
		carries: true
	}
};

/** What one of this wall's rows is called. */
export function nounFor(kind: RelatedKind): RowNoun {
	return SPECS[kind].noun;
}

/* What each kind of page can show, in the order it shows them. */
const TABS_FOR: Record<EntityKind, RelatedKind[]> = {
	// Music (`songs`) stands right after Collections on a person's, a tag's and a Site's page, and
	// last on a Collection's: one agreed place, so the strip reads the same way on every page.
	person: ['files', 'photo_sets', 'loops', 'tags', 'sites', 'collections', 'songs', 'people'],
	// `tags_within` is what is filed UNDER this tag, read off `tags.parent_id`, as a site's
	// `sites_within` is what is part of it.
	tag: ['files', 'photo_sets', 'loops', 'tags_within', 'people', 'sites', 'collections', 'songs'],
	// `sites_within` is the one tab on this table that is not the page's files asked of another
	// wall: it is what is PART OF this site, read off `sites.parent_id`.
	site: ['files', 'photo_sets', 'loops', 'sites_within', 'people', 'tags', 'collections', 'songs'],
	// Loops sits where it sits on every other page: straight after the files.
	collection: ['files', 'loops', 'people', 'tags', 'sites', 'songs'],
	photo_set: ['files', 'people', 'tags', 'sites'],
	// Every tab but Photo Sets, in the order the other pages put them: a Photo Set is stills, and a
	// still carries no song.
	song: ['files', 'loops', 'people', 'tags', 'sites', 'collections']
};

/** The name this kind of thing is pinned under, or null where it cannot be pinned. */
export function pinnableOf(kind: RelatedKind): Pinnable | null {
	const path = SPECS[kind].path;
	return path ? (path.slice(1) as Pinnable) : null;
}

/** What the seen-with wall is called on a person's page. `people` would be a lie there. */
function labelFor(kind: RelatedKind, on: EntityKind): string {
	if (kind === 'people' && on === 'person') return 'Seen with';
	if (kind === 'files' && on === 'photo_set') return 'Pictures';
	return SPECS[kind].label;
}

/** And the glyph, where one wall means something different on one page. */
function iconFor(kind: RelatedKind, on: EntityKind): IconName {
	if (kind === 'people' && on === 'person') return 'diversity_3';
	return SPECS[kind].icon;
}

/** The wall each kind of entity is one ROW of, and through it, where its own page is. */
const WALL_OF: Record<EntityKind, RelatedKind> = {
	person: 'people',
	tag: 'tags',
	site: 'sites',
	collection: 'collections',
	photo_set: 'photo_sets',
	song: 'songs'
};

/** What kind of thing one of a wall's rows IS. */
const KIND_OF: Partial<Record<RelatedKind, EntityKind>> = {
	sites_within: 'site',
	tags_within: 'tag',
	...(Object.fromEntries(
		Object.entries(WALL_OF).map(([kind, wall]) => [wall, kind as EntityKind])
	) as Partial<Record<RelatedKind, EntityKind>>)
};

export function kindOf(showing: RelatedKind): EntityKind | null {
	return KIND_OF[showing] ?? null;
}

/** Where one entity's own page is. */
export function pageOf(on: EntityKind, id: string): string {
	const wall = SPECS[WALL_OF[on]].path;
	// Every kind with a page of its own is a row of a wall with an address.
	if (!wall) throw new Error(`${on} has no page of its own`);
	return `${wall}/${id}`;
}

/** The glyph one kind of entity wears, wherever it is drawn as itself. */
export function iconOf(on: EntityKind): IconName {
	return SPECS[WALL_OF[on]].icon;
}

/** The query parameter that filters a wall to this page's subject. */
const NARROWS: Record<EntityKind, string> = {
	person: 'person',
	tag: 'tag',
	site: 'site',
	collection: 'collection',
	photo_set: 'photo_set',
	song: 'song'
};

/** The filtering one page applies to one wall. Two exceptions, and each of them is a wall being
 * asked a question its ordinary parameter does not mean. */
export function narrowingFor(on: EntityKind, id: string, showing: RelatedKind): URLSearchParams {
	const params = new URLSearchParams();
	if (on === 'person' && showing === 'people') params.set('with_person', id);
	else if (on === 'site' && showing === 'sites_within') params.set('parent', id);
	else if (on === 'tag' && showing === 'tags_within') params.set('parent', id);
	// A song's Loops tab: the marks wall reads a song through the query language's own `songs`
	// field (a song's id or name), as every file filter is read there.
	else if (on === 'song' && showing === 'loops') params.set('songs', id);
	else params.set(NARROWS[on], id);
	return params;
}

/** WHERE ONE OF THIS WALL'S CARDS LEADS, and it is the same table that decides what the card
 * counts. */
export function relatedHref(
	on: EntityKind,
	showing: RelatedKind,
	rowId: string,
	named?: string
): string {
	const kind = kindOf(showing);
	// `files` and `loops` are walls of media rather than of named things, so there is no page to
	// lead to: the media grid draws those and a tile opens a file.
	if (!kind) throw new Error(`${showing} is drawn by the media grid and has no card to press`);
	const page = pageOf(kind, rowId);
	if (!SPECS[showing].carries || !named) return page;
	return `${page}?${new URLSearchParams({ [fieldOf(on)]: named })}`;
}

interface RelatedTab {
	id: RelatedKind;
	label: string;
	/** The wall's glyph. Carried here for the HEADING the wall draws, never for the tab itself:
	 * the tabs are words, and a row of words with icons in it is the chip row they replaced. */
	icon: IconName;
	href: string;
	count?: number;
	/** How big the files `count` counts are, in bytes: the Files tab's only, and only where the
	 * answer said. */
	size?: number;
}

/** The tabs for one page, as addresses. `base` is the page's own address. */
export function tabsFor(
	on: EntityKind,
	id: string,
	base: string,
	counts: Partial<Record<CountKind, number>> = {}
): RelatedTab[] {
	return TABS_FOR[on].map((kind) => ({
		id: kind,
		label: labelFor(kind, on),
		icon: iconFor(kind, on),
		href: kind === 'files' ? base : `${base}?show=${kind}`,
		count: counts[kind],
		...(kind === 'files' && counts.files_bytes !== undefined ? { size: counts.files_bytes } : {})
	}));
}

/** Whichever tab an address names, or `files` when it names none or one this page does not have. */
export function showing(on: EntityKind, raw: string | null): RelatedKind {
	const wanted = (raw ?? 'files') as RelatedKind;
	return TABS_FOR[on].includes(wanted) ? wanted : 'files';
}

/** One row of ANY of the six walls, which is why so little of it is certain. */
export type RelatedRow = Pick<components['schemas']['PersonView'], 'id' | 'name'> &
	Partial<
		Pick<
			components['schemas']['PersonView'],
			| 'locked'
			| 'cover_asset_id'
			| 'cover_upload_id'
			| 'cover_track_id'
			| 'cover_at_ms'
			| 'cover_frame'
			| 'art'
			| 'asset_count'
			| 'favorite'
			| 'pinned'
			| 'rating'
			| 'vault'
			| 'shared'
			| 'restricted'
		> &
			/* A Site and a tag spell Hidden `hidden`; a Site says where its share was decided. */
			Pick<components['schemas']['SiteView'], 'hidden' | 'shared_here' | 'restricted_here'> &
			Pick<components['schemas']['CollectionSummary'], 'item_count'> &
			/* Whether the shipped icon pack has this site's logo. */
			Pick<components['schemas']['SiteView'], 'icon'> &
			Pick<components['schemas']['LoopSummary'], 'duration_ms' | 'asset_id' | 'start_ms'> &
			/* Who a song credits, drawn under its card on a Music tab as on the Music wall. */
			Pick<components['schemas']['SongSummary'], 'artists'>
	>;

interface RelatedPage {
	items: RelatedRow[];
	total: number;
}

/** Every tab's number for one page, in one request. */
export async function loadCounts(
	on: EntityKind,
	id: string
): Promise<Partial<Record<CountKind, number>>> {
	return (await readCounts(on, id)).numbers;
}

/** The strip's numbers, and the boxes the History tab's mark names, from the one request. */
async function readCounts(
	on: EntityKind,
	id: string
): Promise<{ numbers: Partial<Record<CountKind, number>>; boxes: string[] }> {
	try {
		const answer = await api.get<Record<string, unknown>>(`/related/${on}/${id}` as ApiPath);
		const named = answer.disagreement_boxes;
		// Nulls are "this page has no such tab" and must not become noughts: a nought is a real
		// answer worth drawing, and collapsing the two would put "Tags 0" on a tag's own page.
		return {
			numbers: Object.fromEntries(
				Object.entries(answer).filter(([, value]) => typeof value === 'number')
			) as Partial<Record<CountKind, number>>,
			boxes: Array.isArray(named) ? named.filter((one) => typeof one === 'string') : []
		};
	} catch {
		return { numbers: {}, boxes: [] };
	}
}

/** Each kind's thread, at the cap its History tab opens with. */
const THREADS: Record<EntityKind, (id: string) => Promise<unknown[]>> = {
	person: historyOfPerson,
	tag: historyOfTag,
	site: historyOfSite,
	collection: historyOfCollection,
	photo_set: historyOfPhotoSet,
	song: historyOfSong
};

/** The History tab's number: the length of the very thread the tab draws. */
async function readThread(on: EntityKind, id: string): Promise<number | undefined> {
	try {
		return (await THREADS[on](id)).length;
	} catch {
		return undefined;
	}
}

/** The numbers on one page's tab strip, kept for as long as the page is about the same thing. */
export class TabCounts {
	/** What to draw beside each word, and the one mark that is not a word. */
	current = $state<Partial<Record<CountKind, number>>>({});
	/** The boxes the History tab's mark names, by name, beside `current.disagreements`. */
	boxes = $state<string[]>([]);

	/* Which subject the numbers belong to, and which request is the current one. */
	#about = '';
	#on: EntityKind = 'person';
	#generation = 0;

	/** Point it at a page. Safe to call from an effect on every render: it acts only when the
	 * subject actually changes, so it cannot loop and cannot refetch on an unrelated update. */
	follow(on: EntityKind, id: string): void {
		if (!id || this.#about === id) return;
		this.#about = id;
		this.#on = on;
		// Cleared before the request rather than when it lands, or the previous subject's numbers
		// sit beside this one's words for as long as the fetch takes.
		this.current = {};
		this.boxes = [];
		const mine = ++this.#generation;
		void readCounts(on, id).then((found) => {
			if (mine !== this.#generation) return;
			// Merged UNDER what a wall has already reported, not over it: a wall that loaded while
			// this was in flight has the fresher answer, and clobbering it would make a number
			// visibly change for no reason a second after the page settled.
			const panelSaw = this.current.disagreements !== undefined;
			this.current = { ...found.numbers, ...this.current };
			if (!panelSaw) this.sawBoxes(found.boxes);
		});
		this.#thread(on, id, mine);
	}

	/** History's number, off its own request; the strip's generation guards it the same way. */
	#thread(on: EntityKind, id: string, mine: number): void {
		void readThread(on, id).then((lines) => {
			if (mine !== this.#generation || lines === undefined) return;
			this.saw('history', lines);
		});
	}

	/** Ask the strip again for the page it already follows, when the library moves. */
	refresh(): void {
		if (!this.#about) return;
		const mine = ++this.#generation;
		void readCounts(this.#on, this.#about).then((found) => {
			if (mine !== this.#generation) return;
			this.current = { ...this.current, ...found.numbers };
			this.sawBoxes(found.boxes);
		});
		this.#thread(this.#on, this.#about, mine);
	}

	/** What a wall found when it loaded. The freshest answer there is for that one number. */
	saw(kind: CountKind, total: number): void {
		if (this.current[kind] === total) return;
		/* A Files number that moved leaves the size it was read with describing other files, so
		   the size goes with it until the strip is asked again: a size is said of the files
		   counted or not at all. */
		if (kind === 'files') {
			const next = { ...this.current, files: total };
			delete next.files_bytes;
			this.current = next;
			return;
		}
		this.current = { ...this.current, [kind]: total };
	}

	/** What the panel under History found: how many fields wait, and the boxes they are about. */
	sawDisagreements(total: number, named: readonly string[]): void {
		this.saw('disagreements', total);
		this.sawBoxes(named);
	}

	/** Which boxes the waiting fields are about, as the panel or the strip's request found them. */
	sawBoxes(named: readonly string[]): void {
		if (named.length === this.boxes.length && named.every((one, at) => one === this.boxes[at]))
			return;
		this.boxes = [...named];
	}
}

/* How many rows one page of a related wall holds before the wall is measured; see `RelatedWall`. */
export const RELATED_PER_PAGE = 60;

/** The orders a card tab offers: its top-level wall's, read from the shared lists. */
export function ordersFor(showing: RelatedKind): readonly { value: string; label: string }[] {
	const shared = [...UNIVERSAL_SORTS, ...ENTITY_OPINION_SORTS];
	return showing === 'songs' ? [...shared, ARTIST_ORDER] : shared;
}

/** What a card tab is in until somebody chooses: the walls' own default, most files first. */
const TAB_DEFAULT_SORT = 'largest';

const tabSorts = new Map<string, WallSort>();

/** The remembered order for one kind of card tab, kept per wall like the walls' own. */
export function tabSort(showing: RelatedKind): WallSort {
	const wall = (SPECS[showing].path ?? `/${showing}`).slice(1);
	let held = tabSorts.get(wall);
	if (!held) {
		const known = ordersFor(showing).map((one) => one.value);
		held = new WallSort(`sift.related.${wall}.sort`, TAB_DEFAULT_SORT, known);
		tabSorts.set(wall, held);
	}
	return held;
}

/** Where a box searching names keeps its words: never `q`, which the bar reads as a query. */
export const NAME_WORDS = LOOP_WORDS;

/** A tab's search box: its words live in the address under `name`, which the tab links never
 * carry, so every tab starts empty. */
export class TabWords {
	/** What the box shows. */
	term = $state('');
	readonly #name: string;
	readonly #words: WallWords;
	/** The words in the address: what the tab is asked by. */
	asked = $derived.by(() => wordsIn(page.url, this.#name));

	constructor(name: string = WORDS) {
		this.#name = name;
		this.#words = new WallWords(name);
		// Words arriving another way (the chip's cross, Back, a link) go in the box; its own echo does not.
		$effect(() => {
			const arrived = this.asked;
			if (!this.#words.echoed(arrived)) this.term = arrived;
		});
	}

	write(typed: string): void {
		this.#words.write(page.url, typed);
	}
}

/** One page of a related wall, and its total, which is also the tab's count. */
export async function loadRelated(
	on: EntityKind,
	id: string,
	showing: RelatedKind,
	{
		limit = 60,
		offset = 0,
		words = '',
		sort
	}: { limit?: number; offset?: number; words?: string; sort?: string } = {}
): Promise<RelatedPage> {
	const path = SPECS[showing].path as ApiPath | undefined;
	if (!path) throw new Error(`${showing} has no wall of its own; the media grid fetches it`);
	const query: Record<string, string | number> = { limit, offset };
	// A carrying card counts the two together, as its press opens them (`relatedHref`).
	if (SPECS[showing].carries) query.count = 'narrowed';
	if (words) {
		query.prefix = words;
		query.anywhere = 'true';
	}
	if (sort) query.sort = sort;
	for (const [key, value] of narrowingFor(on, id, showing)) query[key] = value;
	const answer = await api.get<RelatedPage>(path, { query });
	return { items: answer.items ?? [], total: answer.total ?? 0 };
}
