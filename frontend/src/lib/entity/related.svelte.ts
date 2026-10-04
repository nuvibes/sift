/*
 * The related walls an entity page can show, and the one place that knows what they are.
 *
 * Cross-association is one question asked six ways: an entity wall filtered to the files another
 * entity reaches. The server answers it with one parameter on the walls it already had, so the
 * client's job is small (name the tabs, build the address, read the page), and all of it lives
 * here rather than being written out on each of the five pages that has tabs.
 *
 * ## Why the tab set is data
 *
 * Six pages want a tab strip. Written per page that is six strips to keep in step, and the day a
 * seventh kind of thing is added it is six edits and one of them gets missed, which is exactly
 * how a hand-kept list goes wrong everywhere else in this codebase. So a page says what it IS and
 * this says what it can show.
 *
 * ## Every count is the count in context
 *
 * A tab's number comes from the same request that fills that tab's wall (its `total`), and never
 * from a second endpoint. Two sources for one number is two populations on one screen.
 */

import { api, type ApiPath } from '$lib/api/client';
import { fieldOf } from '$lib/components/shell/facet-labels';
import type { IconName } from '$lib/design/icons';
import type { Pinnable } from '$lib/library/pinning.svelte';
import type { components } from '$lib/api/schema';

/** What kind of thing an entity page is about. Its own row is never one of its own tabs. */
export type EntityKind = 'person' | 'tag' | 'site' | 'collection' | 'photo_set' | 'song';

/**
 * What a tab can show. `files` is the page's own wall of files.
 *
 * `sites` and `sites_within` are the same wall asking two different questions, which is why they
 * are two kinds rather than one. `sites` is "the sites these files came from" and is filtered by
 * the files; `sites_within` is "the sites that are PART OF this one" and is narrowed by a column.
 * Only a site's page has the second, because a site is the one thing here that can contain another
 * of its own kind.
 *
 * The second one has the longer name deliberately: the plain word goes to the question every page
 * asks.
 */
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

/**
 * What a tab on an entity page can be, which is one more thing than a wall can.
 *
 * `history` is NOT a `RelatedKind` and must not become one. Every wall above is the same question
 * (an entity wall filtered to this page's files), answered by one shared table that a gate holds
 * against the server's, and a history is none of that: no wall, no filter, no page of rows.
 * Putting it in that table to save naming it here would give a tab with no endpoint to all five
 * pages that read the table.
 *
 * It has a NUMBER all the same, which is what this type is for. The server answers it beside the
 * wall counts, in the same request, so every number on one strip is taken at one moment. See
 * `RelatedCounts.history`.
 */
type TabKind = RelatedKind | 'history';

/**
 * Every number the counts route answers with: the tabs, and the marks beside them.
 *
 * `disagreements` is NOT a tab and must never become one. It is how many fields a stash-box
 * disagrees with about this record: the mark the History tab wears while something is waiting,
 * and the panel that settles it is drawn at the top of that tab. It rides on the same request for
 * the reason `history` does: every number on one strip is taken at one moment, and a second
 * endpoint for one mark would be a second round trip and a second moment.
 *
 * It is a separate type from `TabKind` rather than another member of it, because `tabsFor` builds a
 * tab per member of that type's wall half and a strip with a "Disagreements" word on it is exactly
 * what this is not.
 *
 * `files_bytes` is not a tab either: it is how big the files the Files number counts are, off the
 * same read as that number, and it rides beside it so a hover card and an entity page can say the
 * size of exactly those files.
 */
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
	/**
	 * What ONE of its rows is called, and what several are.
	 *
	 * Written out rather than made from `label`, because neither half of that works. A wall is
	 * labelled in the plural, and there is no rule that turns "People" back into "person", and
	 * `labelFor` below hands a person's own wall the words "Seen with", which is not a noun at all.
	 * A sentence built from the label would read "1 people selected" on a tag's People tab and
	 * "1 seen with selected" on a person's own, which is what this exists to stop.
	 */
	noun: RowNoun;
	/**
	 * Whether pressing one of this wall's cards carries the page it was pressed from as a filter.
	 *
	 * ONE FACT, TWO READERS, AND THAT IS THE WHOLE POINT OF IT BEING HERE. It decides where a card
	 * goes (`relatedHref`) and it decides which number the card prints (`loadRelated` asks the
	 * server for the filtered tally exactly when the press carries the filtering), so what a card
	 * says and what its press opens cannot come apart. Written per wall as two sentences in two
	 * files, they would: a Seen with card printing the person's whole wall of 6 over a press that
	 * opens the two-person wall of 2, with nothing on screen saying which set was meant.
	 *
	 * Required rather than optional, so a wall added to this table has to answer the question
	 * instead of inheriting somebody else's answer.
	 */
	carries: boolean;
}

/*
 * The walls, and what each is called. The words are the rail's words, because a tab named anything
 * the sidebar does not say is a second vocabulary for the same things.
 */
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
	// being asked of it. Not a new icon: these ARE sites, and a second mark for the same kind of
	// row would say they were a different kind of thing. The noun follows for the same reason: the
	// rows are sites whichever question reached them.
	//
	// It carries NOTHING, and that is the one place this table differs from the row above it. This
	// is the only wall here that is not about the page's files: a site is part of a network because
	// of a column on its row, so the network as a filter on the member's wall asks a different
	// question, and on most libraries an empty one. So the card counts the whole, which is what
	// its plain press opens.
	sites_within: {
		label: 'Sites',
		icon: 'public',
		path: '/sites',
		noun: { one: 'Site', many: 'Sites' },
		carries: false
	},
	// Plain for a second reason, and it is the destination's rather than this wall's: a collection's
	// Files wall is the arrangement somebody made by hand, it takes no query at all and says so on
	// the bar. A filter sent there would be a chip nothing could act on, so the press opens the
	// whole collection and the card says how big the whole collection is.
	collections: {
		label: 'Collections',
		icon: 'box',
		path: '/collections',
		noun: { one: 'collection', many: 'collections' },
		carries: false
	},
	// The wall a song is a row of, and so where a song's page is (`pageOf`). Labelled Music, the
	// word the sidebar entry, a file's own Music field and Settings > Music all use: the PAGE is the
	// music in the library, and one row of it is still a song. A Music tab stands on a person's, a
	// tag's, a Site's and a Collection's page (`TABS_FOR` below). The glyph is the Music settings
	// section's, the beamed pair that names a piece of music, and it is also what a song with no
	// cover is drawn as.
	//
	// It CARRIES, as the people, tags and Sites walls do: a song pressed on a person's Music tab opens
	// the song's page filtered to that person's files, and its card counts exactly those
	// (`GET /songs` reads `count=narrowed`).
	songs: {
		label: 'Music',
		icon: 'music_note_2',
		path: '/songs',
		noun: { one: 'song', many: 'songs' },
		carries: true
	}
};

/**
 * What one of this wall's rows is called.
 *
 * Off the KIND rather than off the tab's heading: the heading is a plural, sometimes a phrase, and
 * always the word for the WALL, while a selection bar is a sentence about the rows on it. A
 * person's "Seen with" tab holds people, so one of them is a person, and the bar says so.
 */
export function nounFor(kind: RelatedKind): RowNoun {
	return SPECS[kind].noun;
}

/*
 * What each kind of page can show, in the order it shows them.
 *
 * `files` first everywhere, because it is what somebody came to the page for. Nothing lists its own kind: "the people on this person" is
 * answered by `people` filtered with `with_person`, which is the seen-with wall and is named
 * that, rather than by a tab that reads as a page linking to itself.
 */
const TABS_FOR: Record<EntityKind, RelatedKind[]> = {
	// Music (`songs`) stands right after Collections on a person's, a tag's and a Site's page, and
	// last on a Collection's: one agreed place, so the strip reads the same way on every page.
	person: ['files', 'photo_sets', 'loops', 'tags', 'sites', 'collections', 'songs', 'people'],
	// `tags_within` is what is filed UNDER this tag, read off `tags.parent_id`, as a site's
	// `sites_within` is what is part of it.
	tag: ['files', 'photo_sets', 'loops', 'tags_within', 'people', 'sites', 'collections', 'songs'],
	// `sites_within` is the one tab on this table that is not the page's files asked of another wall: it is
	// what is PART OF this site, read off `sites.parent_id`. Drawn on every site's page and not
	// only on the ones that have children, because that is the rule every other tab here follows:
	// a nought is a real answer and an absent tab means "no such wall".
	site: ['files', 'photo_sets', 'loops', 'sites_within', 'people', 'tags', 'collections', 'songs'],
	// Loops sits where it sits on every other page: straight after the files. A collection has no
	// Photo Sets tab, so that is the second slot here. Must agree with the server's `TABS_FOR`
	// in `slices/related/router.py`, which is where the reasoning for the set lives and which a
	// gate compares this against.
	collection: ['files', 'loops', 'people', 'tags', 'sites', 'songs'],
	photo_set: ['files', 'people', 'tags', 'sites'],
	// Every tab but Photo Sets, in the order the other pages put them: a Photo Set is stills, and a
	// still carries no song. Its Tags tab is its files' tags: a song has no tags of its own.
	song: ['files', 'loops', 'people', 'tags', 'sites', 'collections']
};

/**
 * The name this kind of thing is pinned under, or null where it cannot be pinned.
 *
 * Read off `SPECS[kind].path` rather than from a second table, and that is the point rather than a
 * saving: the path is already the answer to "what is this kind called in an address", which is the
 * same question a pin asks. A second map would be a second answer, free to drift, and the one it
 * would drift on is `photo_sets`, whose address says `photo-sets`.
 *
 * `files` and `loops` have no path, and neither can be pinned: both are walls of MEDIA rather than
 * of named things. So the absence of a path is not a coincidence this leans on: it is the same
 * fact, which is why the null falls out rather than being listed.
 */
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

/**
 * And the glyph, where one wall means something different on one page.
 *
 * The exact shape `labelFor` above already has, and it is here for the same reason: a person's
 * `people` wall is not "People", it is who else is in their files. So the single figure that
 * names a person would say "one person", which is the page you are already on. The group of three
 * says a relationship between people, which is what the wall holds.
 *
 * Only the one exception, and it is read off the same pair of facts the label is, rather than a
 * second table free to disagree with it.
 */
function iconFor(kind: RelatedKind, on: EntityKind): IconName {
	if (kind === 'people' && on === 'person') return 'diversity_3';
	return SPECS[kind].icon;
}

/**
 * The wall each kind of entity is one ROW of, and through it, where its own page is.
 *
 * Written as a map from the kind to the wall rather than as a second table of addresses, and that
 * is the point rather than a saving: `SPECS` already answers "what is this kind called in an
 * address", which is the same question a page address asks. A second map would be free to drift,
 * and the one it would drift on is `photo_sets`, whose address says `photo-sets`: the identical
 * trap `pinnableOf` above is written the way it is to avoid.
 */
const WALL_OF: Record<EntityKind, RelatedKind> = {
	person: 'people',
	tag: 'tags',
	site: 'sites',
	collection: 'collections',
	photo_set: 'photo_sets',
	song: 'songs'
};

/**
 * What kind of thing one of a wall's rows IS.
 *
 * The inverse of `WALL_OF` above and built from it, rather than a second table: a wall and the kind
 * of thing on it are one fact, and two tables of it are two chances to disagree, which is the same
 * reasoning `pinnableOf` and `pageOf` are each written under.
 *
 * `sites_within` is the one entry that is not in `WALL_OF` at all, because it is not a wall of its
 * own: it is the sites wall asked a different question. A row of it is a Site, so it answers with
 * one. `files` and `loops` answer null: neither is a named thing, which is the same absence
 * `pinnableOf` reports for them and for the same reason.
 */
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

/**
 * Where one entity's own page is.
 *
 * Exported because two things outside a page need it: the card that opens on a hovered chip
 * draws the name as a link to the page and reads the page's cover from the same address, and the
 * band that draws the chip needs the same address for the chip's own link. A kind written out at
 * each call site is how `/photo-sets` comes to be spelt `/photo_sets` somewhere.
 */
export function pageOf(on: EntityKind, id: string): string {
	const wall = SPECS[WALL_OF[on]].path;
	// Every kind with a page of its own is a row of a wall with an address. `files` and `loops` are
	// the only pathless walls and neither of them is an entity, so this throw is that fact made
	// checkable rather than assumed. See `pinnableOf`, where the same absence means something else.
	if (!wall) throw new Error(`${on} has no page of its own`);
	return `${wall}/${id}`;
}

/**
 * The glyph one kind of entity wears, wherever it is drawn as itself.
 *
 * The same one the rail draws for its wall, the same one a chip carries and the same one that tab's
 * heading uses, because it is read off `SPECS`, which already answers "what mark does this kind of
 * thing wear". Exported for the header on an entity's own page: the row somebody pressed and the
 * page that opened have to say the same thing twice, which is what makes a navigation feel like it
 * landed, and a second table of glyphs would be the drift `pageOf` and `pinnableOf` above are each
 * written the way they are to avoid.
 *
 * NOT `iconFor` next door, which answers a different question: that one is what a wall of OTHER
 * things is called on the page showing it, and its one exception (a person's own people wall is
 * "Seen with", drawn as a group) is exactly wrong here. A person's page wears the single figure,
 * because the page is about one person.
 */
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

/**
 * The filtering one page applies to one wall.
 *
 * Two exceptions, and each of them is a wall being asked a question its ordinary parameter does not
 * mean. A person's own `people` wall is "the people on this person's files", which is right, and
 * the server needs to know to drop their own row, which `with_person` is what says. A site's
 * `sites_within` wall is what is PART OF it, which is a column on the row rather than anything about
 * files: filtered by `site` it would be the sites this site's own files came from, which is
 * this site, so the tab would be the page you are already on.
 */
export function narrowingFor(on: EntityKind, id: string, showing: RelatedKind): URLSearchParams {
	const params = new URLSearchParams();
	if (on === 'person' && showing === 'people') params.set('with_person', id);
	else if (on === 'site' && showing === 'sites_within') params.set('parent', id);
	else if (on === 'tag' && showing === 'tags_within') params.set('parent', id);
	// A song's Loops tab: the marks wall reads a song through the query language's own `songs`
	// field (a song's id or name), as every file filter is read there. It declares no `song`
	// parameter, and one it does not declare is ignored, which would draw every mark in the library.
	else if (on === 'song' && showing === 'loops') params.set('songs', id);
	else params.set(NARROWS[on], id);
	return params;
}

/**
 * WHERE ONE OF THIS WALL'S CARDS LEADS, and it is the same table that decides what the card counts.
 *
 * ONE CLICK, ONE RULE: a thing goes to its own page, and a thing reached THROUGH something else
 * goes there carrying that something else as a filter. So Jane Else, pressed on Jane Doe's Seen
 * with tab, opens Jane Else's page showing the files the two are both on, with `people: Jane Doe`
 * drawn as a chip on the bar, which is a filter a reader can see, take off and add to. A plain link
 * would throw away the only thing that made the card worth pressing from where it was pressed, and
 * a wall that quietly showed the intersection under one name with nothing saying so would be worse
 * still.
 *
 * An ordinary parameter of the query language, not a typed chip in the search box: the
 * destination's wall already reads it, the modal already writes it, and the back button already
 * knows what it is. `fieldOf` is what knows which field names this kind of thing.
 *
 * ## Why it lives here rather than on the wall that draws the cards
 *
 * Because the number under the name has to be the size of whatever this opens (`SPECS.carries`). A
 * wall that built the address while the server was never told which tally the card was about would
 * print a number that does not know about the filtering: 6 over a wall of 2. One table answers
 * both.
 *
 * `named` is the page's own NAME and not its id, because that is what the query language resolves.
 * Nothing at all while the page has not said its name yet: an address with an empty filter on it
 * draws a chip for nobody. A name two things share filters to both, which is the wider direction
 * and the safe one for a filter somebody can see; it is also the one way this link can open a wall
 * bigger than the number on the card, and it is the query language's rule rather than this table's.
 */
export function relatedHref(
	on: EntityKind,
	showing: RelatedKind,
	rowId: string,
	named?: string
): string {
	const kind = kindOf(showing);
	// `files` and `loops` are walls of media rather than of named things, so there is no page to
	// lead to: the media grid draws those and a tile opens a file. Checkable rather than assumed,
	// the same way `pageOf` refuses a kind with no wall of its own.
	if (!kind) throw new Error(`${showing} is drawn by the media grid and has no card to press`);
	const page = pageOf(kind, rowId);
	if (!SPECS[showing].carries || !named) return page;
	return `${page}?${new URLSearchParams({ [fieldOf(on)]: named })}`;
}

interface RelatedTab {
	id: RelatedKind;
	label: string;
	/**
	 * The wall's glyph. Carried here for the HEADING the wall draws, never for the tab itself: the
	 * tabs are words, and a row of words with icons in it is the chip row they replaced.
	 */
	icon: IconName;
	href: string;
	count?: number;
	/**
	 * How big the files `count` counts are, in bytes: the Files tab's only, and only where the
	 * answer said. Never a tab's own figure: a strip of words with sizes in it is the strip
	 * saying twice what the page's header says once; the hover card draws it beside the count.
	 */
	size?: number;
}

/**
 * The tabs for one page, as addresses.
 *
 * `base` is the page's own address. The chosen tab rides in `?show=`, so every tab is somewhere the
 * back button can return to and a link somebody sends opens on the tab they were looking at.
 * `files` is written without the parameter, so the plain address is still the plain page.
 */
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

/**
 * One row of ANY of the six walls, which is why so little of it is certain.
 *
 * Six routes answer this function, each with its own shape: a person, a tag, a site, a
 * collection, a photo set, a loop. What every one of them carries is an id and a name; everything
 * below that is one wall's and not another's, and a card draws what it was given. So the fields are
 * taken out of the shapes that really send them rather than described again here, and a wall
 * gaining a field the card cannot read has to go through the server's own definition to happen.
 */
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
			/* Whether the shipped icon pack has this site's logo. A person's Sites tab draws the
			   same `EntityCard` the Sites wall draws, off the same `GET /sites` listing, so
			   without this field a site with a logo and no chosen cover would get a letter here
			   and its logo on the wall. Site-only, and optional with every other field for that
			   reason. */
			Pick<components['schemas']['SiteView'], 'icon'> &
			Pick<components['schemas']['LoopSummary'], 'duration_ms' | 'asset_id' | 'start_ms'> &
			/* Who a song credits, drawn under its card on a Music tab as on the Music wall. */
			Pick<components['schemas']['SongSummary'], 'artists'>
	>;

interface RelatedPage {
	items: RelatedRow[];
	total: number;
}

/**
 * Every tab's number for one page, in one request.
 *
 * Filled one at a time as each tab is opened, six of a person's seven would have no number until
 * somebody pressed them. So the strip would read "Files 214" and then six bare words, and a tab
 * with nothing behind it would be indistinguishable from one nobody has looked at. This is the map,
 * and a map with six blanks on it is not one.
 *
 * The server answers from the same listings the walls draw, at one row each, so the number beside a
 * word and the cards under it still come from one question. **It does not replace the count a wall
 * reports when it loads**. That stays, because it is the fresher of the two the moment somebody
 * is looking at a tab, and because a wall must never depend on a second request having succeeded.
 *
 * An empty object on failure. A strip with no numbers is perfectly usable; a page that refused to
 * draw because a secondary fetch failed would not be.
 *
 * EXPORTED, because a second thing asks the same question: the card a person's avatar opens on
 * hover shows what that person has, and it must show the same numbers their own page does. Asking
 * it here rather than fetching six walls from the card is the whole of why one request exists.
 */
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

/**
 * The numbers on one page's tab strip, kept for as long as the page is about the same thing.
 *
 * One class rather than the same twenty lines on each of five pages, and the same twenty lines
 * five times is how two of them quietly come to behave differently. Every entity page holds one of
 * these and does two things with it: points it at whatever the page is about, and tells it what a
 * wall found when a wall loads.
 *
 * ## The two sources, and why both
 *
 * `follow` asks the server for every tab's number at once, so a page opens with a complete strip.
 * `saw` is what the wall on screen reported, and it OVERWRITES the map's number for that tab: it
 * is the fresher of the two the moment somebody is looking at it, and it keeps working when the
 * map's request failed. Neither is a second population: the counts route runs the same listings the
 * walls do, so the two agree by construction rather than by luck.
 */
export class TabCounts {
	/** What to draw beside each word, and the one mark that is not a word. A tab with no entry has
	 *  no number, which is not a nought. */
	current = $state<Partial<Record<CountKind, number>>>({});
	/** The boxes the History tab's mark names, by name, beside `current.disagreements`. */
	boxes = $state<string[]>([]);

	/*
	 * Which subject the numbers belong to, and which request is the current one. Plain fields, not
	 * `$state`: `follow` reads them and writes `current`, and a guard that reads state its own body
	 * writes is an effect that runs itself twice.
	 */
	#about = '';
	#on: EntityKind = 'person';
	#generation = 0;

	/**
	 * Point it at a page. Safe to call from an effect on every render: it acts only when the
	 * subject actually changes, so it cannot loop and cannot refetch on an unrelated update.
	 */
	follow(on: EntityKind, id: string): void {
		if (!id || this.#about === id) return;
		this.#about = id;
		this.#on = on;
		// Cleared before the request rather than when it lands, or the previous subject's numbers
		// sit beside this one's words for as long as the fetch takes. The words are the same either
		// way, so nothing else on screen would give it away.
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
	}

	/**
	 * Ask the strip again for the page it already follows, when the library moves.
	 *
	 * The walls re-read on the library's bell and report what they found, but only the tab on
	 * screen has a wall, and History has none: an act on this page (artists edited, a share, a
	 * tag put on) wrote its History line and left the number beside History, and every tab not on
	 * screen, at what the page opened with. Quiet: the numbers drawn stay until the new ones land,
	 * and the answer wins over them, being the later read of the two.
	 */
	refresh(): void {
		if (!this.#about) return;
		const mine = ++this.#generation;
		void readCounts(this.#on, this.#about).then((found) => {
			if (mine !== this.#generation) return;
			this.current = { ...this.current, ...found.numbers };
			this.sawBoxes(found.boxes);
		});
	}

	/**
	 * What a wall found when it loaded. The freshest answer there is for that one number.
	 *
	 * A `CountKind` rather than a `RelatedKind`, because the panel that draws a record's
	 * disagreements is the same case as a wall: while it is on screen it has just read them, and
	 * its answer is fresher than the map's, so settling one takes the mark down without waiting
	 * for the whole strip to be asked again.
	 *
	 * !! A COUNT THAT HAS NOT MOVED IS NOT A CHANGE, and writing it anyway spins the whole page.
	 *
	 * A NEW OBJECT on every call, identical contents or not, loops: the Loops tab draws
	 * `AssetGrid`, which reports its total from an effect rather than once per fetch. So the grid
	 * reports, a new object is made, the tab strip re-derives off it, the grid's `title` and `icon`
	 * arrive as fresh props, the grid re-renders, its effect runs again, it reports the same
	 * number. Round for ever, until Svelte gives up with `effect_update_depth_exceeded` and takes
	 * the page's whole effect graph down with it.
	 *
	 * What that looks like is nothing to do with counting: the page FREEZES. Clicking anything in
	 * the rail changes the address and leaves the screen exactly as it was, on every entity that
	 * has a Loops tab: people, sites and tags alike. It reads as broken routing.
	 *
	 * The guard is here rather than at the caller because this is the thing that owns the state,
	 * and a guard at one caller leaves the next one to find the same hole. It is the same rule the
	 * change bus holds: announce when something actually moved, never when it was merely asked.
	 */
	saw(kind: CountKind, total: number): void {
		if (this.current[kind] === total) return;
		/* A Files number that moved leaves the size it was read with describing other files, so the
		   size goes with it until the strip is asked again: a size is said of the files counted or
		   not at all. */
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

	/**
	 * Which boxes the waiting fields are about, as the panel or the strip's request found them. The
	 * same guard as `saw`: the same names again are not a change, and a new array every time would
	 * re-derive the strip for nothing.
	 */
	sawBoxes(named: readonly string[]): void {
		if (named.length === this.boxes.length && named.every((one, at) => one === this.boxes[at]))
			return;
		this.boxes = [...named];
	}
}

/* How many rows one page of a related wall holds, before the wall has been laid out and measured.
 *
 * The same fallback the four entity walls carry. It is a first page, not a ceiling: a tab with
 * more than this many rows pages on rather than stopping here under a heading counting all of
 * them. See `RelatedWall`, which measures.
 */
export const RELATED_PER_PAGE = 60;

/**
 * One page of a related wall.
 *
 * Every wall answers with `items` and `total` already, so there is one reader rather than six. The
 * total it returns is what fills the tab's count: the same request, so the number beside the word
 * and the cards under it cannot come from two different questions.
 */
export async function loadRelated(
	on: EntityKind,
	id: string,
	showing: RelatedKind,
	{ limit = 60, offset = 0 }: { limit?: number; offset?: number } = {}
): Promise<RelatedPage> {
	const path = SPECS[showing].path as ApiPath | undefined;
	if (!path) throw new Error(`${showing} has no wall of its own; the media grid fetches it`);
	// The filtering as plain values, handed to the client rather than glued onto the address. The
	// generated `ApiPath` is one of the server's own paths and nothing else, which is what stops a
	// hand-built query string from quietly naming a route that does not exist.
	const query: Record<string, string | number> = { limit, offset };
	// WHICH NUMBER EACH CARD IS ABOUT, off the same fact that decides where the card leads. A press
	// that carries this page as a filter opens the two of them together, so the card has to count
	// the two of them together; a plain press opens the whole thing and the card counts the whole.
	// Read off `SPECS` rather than decided here, because a second answer is how a card would come to
	// say six over a wall of two. See `relatedHref`.
	//
	// !! ONLY THE PEOPLE WALL READS IT SO FAR. The tags, Photo Sets and Sites listings ignore a
	// parameter they do not declare, so their cards still print the whole count over a press that
	// filters. That is the same disagreement, on three more walls, written down here rather than
	// left to be rediscovered. Asking every carrying wall for it here is deliberate: the day
	// one of those listings learns the parameter, its cards are right with nothing to change on this
	// side, and the alternative is a second table saying which walls may be asked.
	if (SPECS[showing].carries) query.count = 'narrowed';
	for (const [key, value] of narrowingFor(on, id, showing)) query[key] = value;
	const page = await api.get<RelatedPage>(path, { query });
	return { items: page.items ?? [], total: page.total ?? 0 };
}
