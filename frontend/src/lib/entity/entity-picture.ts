/* The picture one entity is drawn as, in the one place every surface reads it from. */
import type { components } from '$lib/api/schema';
import { coverUrl } from '$lib/entity/art';
import { creatorArt } from '$lib/entity/creator-art.svelte';
import { faceCoverUrl } from '$lib/people/faces.svelte';
import { iconOf, pageOf, type EntityKind } from '$lib/entity/related.svelte';
import type { IconName } from '$lib/design/icons';
import type { ChipPicture } from '$lib/components/common/Chip.svelte';
import type { ChoicePicture, PickChoice } from '$lib/components/common/verbs';

/** What a caller holding the entity's own row already knows about its cover. */
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

/** What a kind of thing is drawn as where nobody chose it a picture, when that is not its letter. */
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
	   answers with when nobody has chosen a picture, and a Site the pack does not cover shows
	   its letter. */
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

/** What a row already holds about the picture its thing is drawn by: the server's own cover
 * columns, spelled as every entity view spells them. */
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

/** THE PICTURE A THING IS DRAWN BY in a picker: the rule its card and its page apply
 * (`EntityCard`, `EntityHeader`), for every kind, so a person looks the same in "Who is this?" */
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

/** THE SITE'S OWN MARK FOR ONE LINK, by the host the link is on, or null where there is no host. */
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

/** EVERY PICTURE THAT MAY STAND FOR ONE LINK, best first: the one answer every surface drawing a
 * person's or a site's links reads. */
export function linkMarks(url: string, siteName?: string | null): string[] {
	const found: string[] = [];
	const byHost = linkMark(url);
	if (byHost) found.push(byHost);
	return found;
}
