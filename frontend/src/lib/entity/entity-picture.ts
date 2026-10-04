/*
 * The picture one entity is drawn as, in the one place every surface reads it from.
 *
 * A card, the chip that opens it, the rows of a pick flyout and the groups a history row opens to
 * are the same thing at several sizes, and they must not answer "which picture" more than once: a
 * chip wearing a cover beside a card wearing a monogram is one thing drawn as two.
 *
 * The address is the ENTITY's own cover (`/api/people/<id>/cover`, `/api/photo-sets/<id>/cover`)
 * and never the file behind `cover_asset_id`: an uploaded cover has no file behind it, and a
 * cover naming a MOMENT of a video is not the frame the file itself is drawn as.
 *
 * `instead` is the site's own mark, and only a Site has one. A site with no chosen cover answers
 * 404 at its cover address, which is ordinary and is exactly what a second address is for. It is the
 * same three-way rule the pick rows and the Sites wall follow: a chosen cover wins, else the mark
 * Sift already holds, else the letter.
 *
 * EVERY kind goes through here, and that is load-bearing: the file dialog's rows draw a collection,
 * a photo set and a tag by this rule too. A 404 at the cover address is the ordinary answer for a
 * thing nobody has chosen one for, and `Avatar` draws the first letter behind it, which is why
 * there is no "has it got one" question to ask here and why the answer has the same shape for all
 * five.
 *
 * No `mark` flag is passed, and that is a real decision rather than an oversight. `Avatar.mark`
 * exists to stop a LOGO being cropped to a box that is not square; the chip's `.shot` and the
 * card's face are both square, so contain and cover agree on a square mark and the flag would
 * change nothing. Where it would matter is a wide cover in a square box, and there cropping is
 * right.
 *
 * ## Why it is a module of its own and not `EntityPreview`'s
 *
 * The history row opens to groups of named things, each drawn as the chip the band draws, and
 * `lib/components/common` importing a composition from `lib/components` would close a loop: that
 * composition imports the primitives back out of `common/index.ts`, which is the module the row is
 * exported from. A rule that half the app must share cannot live somewhere half the app cannot
 * import, so it lives here, where it depends on nothing that draws.
 */
import type { components } from '$lib/api/schema';
import { coverUrl } from '$lib/entity/art';
import { creatorArt } from '$lib/entity/creator-art.svelte';
import { faceCoverUrl } from '$lib/people/faces.svelte';
import { iconOf, pageOf, type EntityKind } from '$lib/entity/related.svelte';
import type { IconName } from '$lib/design/icons';
import type { ChipPicture } from '$lib/components/common/Chip.svelte';
import type { ChoicePicture, PickChoice } from '$lib/components/common/verbs';

/**
 * What a caller holding the entity's own row already knows about its cover.
 *
 * A `Pick<>` of the server's own view rather than a copy of it: every entity view spells these
 * fields the same way, and the server's shapes are described once, in `lib/api/schema.d.ts`.
 *
 * Optional, because most chips are drawn from a name and an id and nothing else. Where it is given,
 * the address names WHICH cover it is (the upload, or the file and its chosen moment) and
 * carries the account's token, and that is the only address the server will let the browser keep
 * for a week (`kernel/covers.py names_its_cover`). Without it the address is bare and the picture
 * is re-checked on every visit: correct, and one conditional request dearer.
 */
export type CoverOf = Partial<
	/* A person's view, because it carries all four fields; a username has no picture of its own
	   to describe. */
	Pick<
		components['schemas']['PersonView'],
		'cover_asset_id' | 'cover_upload_id' | 'cover_at_ms' | 'cover_frame' | 'art'
	>
> &
	/* A Site's row also names the SHIPPED logo it is answered with while nobody has chosen a
	   picture (`SiteView.icon`, the logo's token), so a site chip drawn from its row is kept too. */
	Partial<Pick<components['schemas']['SiteView'], 'icon'>>;

/** A chip's picture: the cover, the mark behind it and the name behind that. */
type EntityPicture = ChipPicture;

/**
 * What a kind of thing is drawn as where nobody chose it a picture, when that is not its letter.
 *
 * A song alone: a song with no cover is the music glyph (the Songs page's own), on its card, its
 * page, a chip and a hover card alike, because a wall of songs is a wall of music and a coloured
 * letter reads as somebody's initial. Every other kind keeps its letter. Read here, in the one
 * place that answers "which picture", so no surface can draw a song's letter by forgetting it.
 */
export function glyphOf(kind: EntityKind): IconName | undefined {
	return kind === 'song' ? iconOf('song') : undefined;
}

/** The cover, the mark behind it and the name behind that, for one entity of any of the five kinds. */
export function entityPicture(
	kind: EntityKind,
	id: string,
	name: string,
	cover?: CoverOf
): EntityPicture {
	/* No second address: a Site's mark is the shipped logo, which its cover address already
	   answers with when nobody has chosen a picture, and a Site the pack does not cover shows its
	   letter. Nothing is ever fetched from the site for it. */
	const mark = null;
	const src = cover
		? coverUrl(pageOf(kind, id), cover.art ?? null, {
				uploadId: cover.cover_upload_id,
				assetId: cover.cover_asset_id,
				atMs: cover.cover_at_ms,
				frame: cover.cover_frame,
				icon: cover.icon
			})
		: coverUrl(pageOf(kind, id));
	const glyph = glyphOf(kind);
	return { src, instead: mark, name, ...(glyph ? { glyph } : {}) };
}

/**
 * What a row already holds about the picture its thing is drawn by: the server's own cover columns,
 * spelled as every entity view spells them. All optional, because a row from a narrower read is
 * still a row; a column it lacks is a branch below it cannot take.
 */
export type Drawable = {
	id: string;
	name: string;
	/** Where a tag sits in a picker's tree. See `underParents` in `tags.svelte.ts`. */
	depth?: number;
	within?: string | null;
	/** The tag a tag is filed under, as the server sends it on a row not yet placed in a tree. */
	parent_name?: string | null;
} & Partial<
	Pick<
		components['schemas']['PersonView'],
		'cover_asset_id' | 'cover_upload_id' | 'cover_at_ms' | 'cover_frame' | 'cover_track_id' | 'art'
	>
> &
	Partial<Pick<components['schemas']['SiteView'], 'icon'>>;

/**
 * THE PICTURE A THING IS DRAWN BY in a picker: the rule its card and its page apply
 * (`EntityCard`, `EntityHeader`), for every kind, so a person looks the same in "Who is this?" as
 * on their page.
 *
 * An upload first, the one cover with no file behind it; then a face, cut from the file at a size
 * worth looking at; then the file at its chosen moment, with the file's own still for the window
 * before that moment is rendered; then, for a person nobody chose a cover for, the picture the
 * site shows them with; then a Site's shipped logo, drawn whole. Nothing chosen and nothing
 * shipped is no picture at all, and the row draws its letter without asking for an address that
 * can only answer 404.
 */
export function drawnBy(kind: EntityKind, row: Drawable): ChoicePicture | undefined {
	const page = pageOf(kind, row.id);
	const art = row.art ?? null;
	const frame = row.cover_frame ?? null;
	if (row.cover_upload_id) {
		return { src: coverUrl(page, art, { uploadId: row.cover_upload_id, frame }) };
	}
	if (row.cover_asset_id && row.cover_track_id) {
		return { src: faceCoverUrl(row.cover_track_id, art) };
	}
	if (row.cover_asset_id) {
		return {
			src: coverUrl(page, art, { assetId: row.cover_asset_id, atMs: row.cover_at_ms, frame }),
			instead: `/api/assets/${encodeURIComponent(row.cover_asset_id)}/thumb`
		};
	}
	if (kind === 'person' && creatorArt.has(row.name)) {
		return { src: `/api/creator-art/${encodeURIComponent(row.name)}` };
	}
	if (row.icon) return { src: coverUrl(page, art, { icon: row.icon }), mark: true };
	return undefined;
}

/** One thing as a row in a picker: its name, and the picture `drawnBy` answers for it. */
export function pickRow(kind: EntityKind, row: Drawable): PickChoice {
	const picture = drawnBy(kind, row);
	return {
		id: row.id,
		name: row.name,
		...(picture ? { picture } : {}),
		...(row.depth ? { depth: row.depth } : {}),
		/* A row placed in a tree says its branch only where the row above is not its parent
		   (`within`); a row that was not placed says its parent whenever it has one. */
		...((row.within !== undefined ? row.within : row.parent_name)
			? { within: (row.within !== undefined ? row.within : row.parent_name) as string }
			: {})
	};
}

/**
 * THE SITE'S OWN MARK FOR ONE LINK, by the host the link is on, or null where there is no host.
 *
 * A person's links are addresses on sites, and a row of addresses reads as a row of sites only when
 * each one carries its site's logo. The logo comes from the pack that ships with Sift, asked by the
 * HOST alone (`GET /api/sites/icons/for`): the server walks up from a subdomain to the site it
 * belongs to, so `en.wikipedia.org` draws Wikipedia's mark, and a host the pack does not know
 * answers 404, which the caller reads as "draw the text alone".
 *
 * The host and never the address, so a link's path and query (a profile id, a search) are not
 * sent anywhere they were not already going. Built here rather than in the component that draws a
 * link, for the reason the rest of this module exists: "which picture stands for this" has one
 * answer per kind of thing, and a second copy of the address shape is the one that drifts.
 */
function linkMark(url: string): string | null {
	let host: string;
	try {
		host = new URL(url).hostname;
	} catch {
		return null;
	}
	if (!host.includes('.')) return null;
	return `/api/sites/icons/for?host=${encodeURIComponent(host.toLowerCase())}`;
}

/**
 * EVERY PICTURE THAT MAY STAND FOR ONE LINK, best first: the one answer every surface drawing a
 * person's or a site's links reads.
 *
 * The pack's logo for the link's host, and nothing after it: nothing is fetched from a site for its
 * mark, so the list holds at most the one. `siteName` is still taken so the callers need not
 * change, and is not read.
 *
 * Still a list, because the pack's answer can be 404 (a host the pack does not know), and the
 * caller then draws its own stand-in (the plain link glyph in the row under a name, the words alone
 * in the record panel); a caller walking a list needs no second path for "none".
 */
export function linkMarks(url: string, siteName?: string | null): string[] {
	const found: string[] = [];
	const byHost = linkMark(url);
	if (byHost) found.push(byHost);
	return found;
}
