/** Addresses for the pictures Sift generates: the still on a tile, and the clip that plays on it. */

import { frameToken, type Frame } from '$lib/entity/cover-frame';
import type { components } from '$lib/api/schema';
import outlines from '$lib/generated/icon-outlines.json';

/* The one icon this file needs as a drawing rather than as text. */
const HIDDEN_MARK = outlines.visibility_off;

type Summary = components['schemas']['AssetSummary'];

/** Something the server described that has pictures. */
export type Pictured = Pick<Summary, 'id'> & Partial<Pick<Summary, 'art' | 'concealed'>>;

function addressed(path: string, art: string | null | undefined): string {
	return art ? `${path}?v=${encodeURIComponent(art)}` : path;
}

/* The mark drawn where a picture is being withheld: a face crop, or a concealed file's still. */
/* Only reached where there is no document: server-side rendering, or a test without one. */
const FALLBACK_INK = 'graytext';

/* Turned over, because a font draws up from the baseline and an SVG draws down from the top. */
function hiddenMarkOf(ink: string): string {
	const svg =
		`<svg xmlns="http://www.w3.org/2000/svg" viewBox="${HIDDEN_MARK.view}">` +
		`<g transform="scale(1,-1)"><path fill="${ink}" d="${HIDDEN_MARK.path}"/></g></svg>`;
	return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

let drawn = { ink: '', url: '' };

/** The hidden mark in the ink this theme resolved, built once per colour. */
export function hiddenMark(): string {
	const ink =
		typeof document === 'undefined'
			? FALLBACK_INK
			: getComputedStyle(document.documentElement).getPropertyValue('--sift-ink-3').trim() ||
				FALLBACK_INK;
	if (drawn.ink !== ink) drawn = { ink, url: hiddenMarkOf(ink) };
	return drawn.url;
}

/** The still. A concealed file gets the hidden mark instead, as `cropUrl` does for a face. */
export function thumbUrl(item: Pictured): string {
	if (item.concealed) return hiddenMark();
	return addressed(`/api/assets/${encodeURIComponent(item.id)}/thumb`, item.art);
}

/** The short clip that plays while the cursor rests on a tile. */
export function previewUrl(item: Pictured): string {
	return addressed(`/api/assets/${encodeURIComponent(item.id)}/preview`, item.art);
}

/** A row with enough on it to say whether its hover clip is worth asking for. */
type Clippable = Pick<
	components['schemas']['AssetSummary'],
	'media_type' | 'preview' | 'concealed'
>;

/** Whether a tile should ask for its hover clip at all. */
export function clipWasBuilt(item: Clippable): boolean {
	if (item.concealed) return false;
	if (!item.preview) return false;
	return item.media_type === 'video' || item.media_type === 'gif';
}

/** The picture belonging to a ROW rather than to a file. */
export function rowStillUrl(wallPath: string, item: Pictured): string {
	return addressed(`/api${wallPath}/${encodeURIComponent(item.id)}/thumb`, item.art);
}

/** What names WHICH picture an entity's cover answers with: the upload, a file's moment, a logo. */
interface CoverNamed {
	assetId?: string | null;
	atMs?: number | null;
	uploadId?: string | null;
	icon?: string | null;
	/** The window of the picture it is drawn as (`lib/entity/cover-frame.ts`). */
	frame?: Frame | null;
}

/** The picture an ENTITY is drawn as: a person, a Site, an account, a tag, an album, a shelf. */
export function coverUrl(entityPath: string, art?: string | null, cover?: CoverNamed): string {
	/* Cut into the wall and the id rather than pasted whole, and that is not cosmetic. */
	const clean = entityPath.split(/[?#]/)[0].replace(/^\/|\/$/g, '');
	const cut = clean.lastIndexOf('/');
	const wall = clean.slice(0, cut);
	const id = clean.slice(cut + 1);
	return addressed(`/api/${wall}/${encodeURIComponent(id)}/cover`, coverToken(art, cover));
}

/* The token for an entity's cover, which has to name WHICH cover it is. */
function coverToken(
	art: string | null | undefined,
	cover: CoverNamed | undefined
): string | null | undefined {
	/* An uploaded cover names itself and nothing else. */
	/* The window last, after whichever picture it is a window of. */
	const framed = cover?.frame ? `.${frameToken(cover.frame)}` : '';
	if (cover?.uploadId) return `${art ?? ''}.${cover.uploadId}${framed}`;
	if (cover?.assetId) {
		const moment = cover.atMs == null ? '' : `.${cover.atMs}`;
		return `${art ?? ''}.${cover.assetId}${moment}${framed}`;
	}
	/* Nothing chosen: the address answers with the site's SHIPPED logo, named by its token. */
	if (cover?.icon) return `${art ?? ''}.${cover.icon}`;
	return art;
}

/** An entity's cover as its WHOLE picture: what the frame editor draws its window over. */
export function wholeCoverUrl(entityPath: string, art?: string | null, cover?: CoverNamed): string {
	const address = coverUrl(entityPath, art, { ...cover, frame: null });
	return `${address}${address.includes('?') ? '&' : '?'}whole=1`;
}

/** The scrub strip: every frame of the timeline preview, as one picture. */
export function spriteUrl(item: Pictured): string {
	return addressed(`/api/assets/${encodeURIComponent(item.id)}/sprite`, item.art);
}
