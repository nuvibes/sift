/**
 * Addresses for the pictures Sift generates: the still on a tile, and the clip that plays on it.
 *
 * A picture's address may carry a token, which the server sends alongside the file it belongs to.
 * The token names what the picture currently is and how many times what this account may see has
 * changed, so an address goes on meaning the same picture until one of those moves. That is what
 * lets the browser keep its copy for a week instead of asking about every tile on every visit.
 *
 * Without a token the address is left bare, and the server answers a token-less address the careful
 * way (`keeps` in `kernel/serving.py`): it is checked on every use, which is a slower screen rather
 * than a broken one. That matters because a week-long cache on a name that cannot move would draw a
 * picture from the browser's own store after the account's permissions had moved, with no request
 * and therefore no permission check.
 *
 * Built here rather than at each tile because the same three addresses are drawn on the grid, the
 * history strip, a folder, a collection, a search and the near-duplicate wall. A token appended in
 * six places is a token forgotten in one, and a picture whose address forgets the token is one that
 * silently goes back to being re-checked, which nothing on screen would show.
 */

import { frameToken, type Frame } from '$lib/entity/cover-frame';
import type { components } from '$lib/api/schema';
import outlines from '$lib/generated/icon-outlines.json';

/* The one icon this file needs as a drawing rather than as text. Generated beside the icon
   codepoint map by `npm run fonts`, out of the same font every other icon is rendered from. */
const HIDDEN_MARK = outlines.visibility_off;

type Summary = components['schemas']['AssetSummary'];

/** Something the server described that has pictures. `art` is absent on a concealed placeholder,
 *  and `concealed` says so where the row carries it: a `Pick<>` of the server's own shape, so the
 *  three spell what the server spells. */
export type Pictured = Pick<Summary, 'id'> & Partial<Pick<Summary, 'art' | 'concealed'>>;

function addressed(path: string, art: string | null | undefined): string {
	return art ? `${path}?v=${encodeURIComponent(art)}` : path;
}

/* The mark drawn where a picture is being withheld: a face crop, or a concealed file's still.
 *
 * A picture rather than a component, and that is the whole reason it works everywhere at once.
 * Nine screens draw a face, each with its own size, radius and object-fit written against `img`.
 * A component would need every one of those rules rewritten, and scoped styles do not reach
 * into a child anyway. A data address is still an `<img>`, so it inherits all of it and no screen
 * has to be told about the vault.
 *
 * The SHAPE is the icon set's own `visibility_off`, taken out of the icon font at build time, so
 * a hidden file and a hidden face wear the same crossed-out eye as every other hidden thing. A
 * mark drawn by hand would be a second copy of an icon.
 *
 * The colour is READ OFF THE PAGE rather than written here. A `currentColor` inside a data
 * address resolves against nothing (the picture has no page to inherit from), so a hardcoded
 * grey would be the same grey under every base, which is the one thing a theme is for. Asking the
 * document for the token gives whatever the chosen base resolved it to, and the answer is kept
 * until it changes.
 */
/* Only reached where there is no document to ask: server-side rendering, and a test that has not
   built one. A CSS system colour rather than a value of our own: it is the browser's own word for
   text that is deliberately dimmed, so it needs no token and cannot go stale against the palette. */
const FALLBACK_INK = 'graytext';

/* Turned over, because a font draws up from the baseline and an SVG draws down from the top. The
   view box is already the glyph's own bounds with its vertical axis flipped, so the drawing has to
   be flipped to sit inside it. */
function hiddenMarkOf(ink: string): string {
	const svg =
		`<svg xmlns="http://www.w3.org/2000/svg" viewBox="${HIDDEN_MARK.view}">` +
		`<g transform="scale(1,-1)"><path fill="${ink}" d="${HIDDEN_MARK.path}"/></g></svg>`;
	return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

let drawn = { ink: '', url: '' };

/**
 * The hidden mark in the ink this theme resolved, built once per colour.
 *
 * Drawn wherever a picture is being withheld: a face on a file in the vault (`cropUrl`), and the
 * still of a concealed file (`thumbUrl`). One mark for one idea, in one place.
 */
export function hiddenMark(): string {
	const ink =
		typeof document === 'undefined'
			? FALLBACK_INK
			: getComputedStyle(document.documentElement).getPropertyValue('--sift-ink-3').trim() ||
				FALLBACK_INK;
	if (drawn.ink !== ink) drawn = { ink, url: hiddenMarkOf(ink) };
	return drawn.url;
}

/**
 * The still.
 *
 * A concealed file gets the hidden mark instead of an address, the way `cropUrl` answers for a face
 * in the vault. The server refuses that still exactly as it refuses the file, so a tile that asked
 * anyway would draw the browser's torn-page glyph and cost a refused request on every visit,
 * which reads as Sift being broken rather than as something being kept back.
 */
export function thumbUrl(item: Pictured): string {
	if (item.concealed) return hiddenMark();
	return addressed(`/api/assets/${encodeURIComponent(item.id)}/thumb`, item.art);
}

/** The short clip that plays while the cursor rests on a tile. */
export function previewUrl(item: Pictured): string {
	return addressed(`/api/assets/${encodeURIComponent(item.id)}/preview`, item.art);
}

/** A row with enough on it to say whether its hover clip is worth asking for. A `Pick<>` of the
 *  server's own shape rather than a description of it: a mark's row spells these three the same way
 *  a file's does, deliberately, so both walls satisfy this. */
type Clippable = Pick<
	components['schemas']['AssetSummary'],
	'media_type' | 'preview' | 'concealed'
>;

/**
 * Whether a tile should ask for its hover clip at all.
 *
 * Here rather than in the two walls that ask, because the rule is one rule. `previewUrl` is
 * already shared for the same reason.
 *
 * Two things have to be true. It has to MOVE: a photograph has no clip, and a GIF does, which is
 * not obvious: leaving GIFs out would leave every one of them a frozen first frame under the
 * cursor while the clip is on disk. And the clip has to have been BUILT, which only the server
 * knows and says: the art token is true the moment ANY picture exists, so a file with a still and
 * no clip would pass it and 404.
 */
export function clipWasBuilt(item: Clippable): boolean {
	if (item.concealed) return false;
	if (!item.preview) return false;
	return item.media_type === 'video' || item.media_type === 'gif';
}

/**
 * The picture belonging to a ROW rather than to a file.
 *
 * On most walls a row IS a file and `thumbUrl` is the answer. On a wall of marks it is not: a mark
 * is a moment of a video, and drawn by the file, every mark of one video would wear that video's
 * first frame: two moments of one long video would be identical tiles with different words.
 *
 * The address is the row's own, on its own wall: `/api/loops/<mark id>/thumb`. Built from the
 * wall's path rather than hard-coded, so a second kind of row with a picture of its own inherits
 * this instead of adding a branch. The moment is never in the address: the server reads it off
 * the row, which is what stops a caller asking for an unbounded number of pictures of one file.
 */
export function rowStillUrl(wallPath: string, item: Pictured): string {
	return addressed(`/api${wallPath}/${encodeURIComponent(item.id)}/thumb`, item.art);
}

/**
 * What names WHICH picture an entity's cover address answers with: the upload, or the file and its
 * chosen moment, or (for a Site nobody has chosen one for) the shipped logo's token
 * (`SiteView.icon`). Each is optional because a screen passes what the row it holds carries.
 */
interface CoverNamed {
	assetId?: string | null;
	atMs?: number | null;
	uploadId?: string | null;
	icon?: string | null;
	/** The window of the picture it is drawn as (`lib/entity/cover-frame.ts`). A reframe changes the
	 *  picture behind the address, so the address names the window too. */
	frame?: Frame | null;
}

/**
 * The picture an ENTITY is drawn as: a person, a Site, an account, a tag, an album, a shelf.
 *
 * Built from the thing's OWN page address (`/people/<id>` becomes `/api/people/<id>/cover`), so
 * every screen that can link to something can draw its picture, with nothing new to pass around and
 * no map from a kind to a path kept anywhere. Query and fragment are dropped, because an address
 * with a chosen tab on it is still the same thing's address.
 *
 * The sibling of `rowStillUrl` and for the same reason: what is drawn is not a file, it is a thing
 * that POINTS at one. And, since a cover may name which moment of a video it is, the address of
 * the file would draw the wrong frame. One builder rather than an address made by hand on each
 * screen, so a chosen moment is honoured everywhere and there is one place to fix it.
 *
 * The moment is never in the address. The server reads it off the entity's row, which is what stops
 * a caller asking for an unbounded number of distinct pictures of one file: the same rule the
 * marks' own picture is built on, written down on both sides.
 *
 * A 404 is ordinary: no cover chosen, nothing this account may open, or a chosen frame whose
 * picture has not been rendered yet. Every caller already draws a letter when the picture fails.
 */
export function coverUrl(entityPath: string, art?: string | null, cover?: CoverNamed): string {
	/*
	 * Cut into the wall and the id rather than pasted whole, and that is not cosmetic.
	 *
	 * The gate that checks every route has something calling it compares the client's addresses
	 * with the server's SEGMENT BY SEGMENT, blanking what each side interpolates. Written as
	 * `/api${everything}` the whole address is two segments and can never match a five-segment
	 * route, so the gate would report every route this builds as dead. Written this way the shape
	 * is legible: a wall, an id, a name.
	 */
	const clean = entityPath.split(/[?#]/)[0].replace(/^\/|\/$/g, '');
	const cut = clean.lastIndexOf('/');
	const wall = clean.slice(0, cut);
	const id = clean.slice(cut + 1);
	return addressed(`/api/${wall}/${encodeURIComponent(id)}/cover`, coverToken(art, cover));
}

/*
 * The token for an entity's cover, which has to name WHICH cover it is.
 *
 * The entity token on its own does not. It is `face_version(stamp)` on the server, whose own
 * comment gives the reason it can be just the stamp: "a crop is written when the face is found and
 * a cover when somebody chooses it, and neither is ever rewritten". That holds for a face crop, but
 * a cover is a chosen file, and choosing a different one does not move the stamp. The address
 * would stay identical while the picture behind it changed, and the reply carries
 * `immutable, max-age=1 week`, so changing a cover would show nothing, on the machine that changed
 * it, for a week.
 *
 * Folding the chosen file and moment in makes the address name its contents, which is the same rule
 * `art_version` follows for every other picture in Sift and the thing that makes a long cache safe.
 * It is a cache key and never a capability (nothing checks it on the way in, exactly as the
 * server's own note says), so the client may compose it, and the client is where both halves are
 * already known.
 */
function coverToken(
	art: string | null | undefined,
	cover: CoverNamed | undefined
): string | null | undefined {
	/* An uploaded cover names itself and nothing else. There is no file, so there is no moment and
	   no `art` worth folding in: the id is minted per picture and never reused, so it IS the
	   contents. It is checked first because the two can never both be here: one statement writes
	   the pair, so choosing either clears the other. */
	/* The window last, after whichever picture it is a window of. The server compares the same
	   string (`kernel/covers.py names_its_cover`). */
	const framed = cover?.frame ? `.${frameToken(cover.frame)}` : '';
	if (cover?.uploadId) return `${art ?? ''}.${cover.uploadId}${framed}`;
	if (cover?.assetId) {
		const moment = cover.atMs == null ? '' : `.${cover.atMs}`;
		return `${art ?? ''}.${cover.assetId}${moment}${framed}`;
	}
	/* Nothing chosen, so the address answers with the site's SHIPPED logo, and the logo's own token
	   names it: the release and a digest of its bytes. After `art` for the reason an upload
	   carries it: the logo is not a library file, so the account's token is the one thing that
	   moves when what this account may see does. Checked last because a chosen cover wins over the
	   logo on the server too; the server keeps the logo only under exactly this address
	   (`kernel/covers.py names_the_shipped`). */
	if (cover?.icon) return `${art ?? ''}.${cover.icon}`;
	return art;
}

/**
 * An entity's cover as its WHOLE picture, whatever window it is framed to: what the frame editor
 * draws its window over (`CoverFramer`). The same address as `coverUrl` with `whole=1` on it, and a
 * token naming the picture without the window. The server serves and keeps it that way
 * (`kernel/covers.py frame_served`).
 */
export function wholeCoverUrl(entityPath: string, art?: string | null, cover?: CoverNamed): string {
	const address = coverUrl(entityPath, art, { ...cover, frame: null });
	return `${address}${address.includes('?') ? '&' : '?'}whole=1`;
}

/** The scrub strip: every frame of the timeline preview, as one picture. */
export function spriteUrl(item: Pictured): string {
	return addressed(`/api/assets/${encodeURIComponent(item.id)}/sprite`, item.art);
}
