/* Asking the server what an edit would do, and then asking it to do it.
 *
 * The same thin shape the compression module has, and for the same reason: every judgement is the
 * server's. Whether a rectangle falls off the edge of the photograph, whether a width is larger
 * than the picture, whether a clip runs past the end, what the copy will be called and what format
 * it will come back in: all of it is answered from what probing the file recorded, and none of
 * it is worked out here. A second implementation of the rules is a second answer waiting to
 * disagree with the first.
 */

import { api } from '$lib/api/client';
import { lengthClock } from '$lib/shell/duration';
import type { SpriteSheet } from '$lib/player/trickplay';
import type { components } from '$lib/api/schema';

/**
 * What the editor needs to know about the one file it is open on.
 *
 * Declared here rather than beside each surface that opens it. A tile carries what a grid needs to
 * draw it; this is what the editor needs to draw a picture and a timeline, and a second copy of the
 * list is a second answer to what "enough to edit this" means.
 */
export type EditableAsset = Pick<
	components['schemas']['AssetDetail'],
	'id' | 'media_type' | 'width' | 'height' | 'duration_ms' | 'filename' | 'art' | 'sprite'
>;

/**
 * The six verbs. Which of them are offered depends on what kind of file it is.
 *
 * Read from the server's own schema rather than written out here: a copy of an enum goes stale in
 * the one direction nothing checks, and the panel could not describe a verb the server had grown.
 */
export type Operation = components['schemas']['Operation'];

/** Quarter turns, and the two mirrors. Anything else has to invent pixels in the corners. */
export type Turn = 'right' | 'left' | 'half' | 'mirror' | 'flip';

/** One thing to do to the picture, in the pixels of the picture as it is SEEN. */
export type EditStep = components['schemas']['EditStep'];

export type EditRequest = components['schemas']['EditRequest'];

export type EditVerdict = components['schemas']['EditVerdict'];

export type EditStarted = components['schemas']['EditStarted'];

/** How big the picture is as it is SEEN, which is not always the size Sift recorded. */
export type EditFrame = components['schemas']['EditFrame'];

/**
 * The size to aim at, asked once when the editor opens.
 *
 * Its own question rather than part of the verdict, because a rectangle is dragged over a picture
 * and until the size of that picture is known there is nothing to drag over.
 */
export async function frame(assetId: string): Promise<EditFrame> {
	return api.get<EditFrame>(`/assets/${encodeURIComponent(assetId)}/edit/frame`);
}

export async function preflight(assetId: string, request: EditRequest): Promise<EditVerdict> {
	return api.post<EditVerdict>(`/assets/${encodeURIComponent(assetId)}/edit/preflight`, {
		body: request
	});
}

export async function start(assetId: string, request: EditRequest): Promise<EditStarted> {
	return api.post<EditStarted>(`/assets/${encodeURIComponent(assetId)}/edit`, { body: request });
}

/**
 * A moment in a file as somebody would say it: `1:30`, `1:02:05`. From MILLISECONDS, the unit the
 * edit routes speak, and rounded as a length is: the clock itself is `$lib/shell/duration`'s.
 */
export function clock(milliseconds: number): string {
	return lengthClock(Math.max(0, milliseconds) / 1000);
}

/**
 * What happened when a stretch of a video was asked for as a clip.
 *
 * A refusal carries the server's own sentence, because the reasons are specific and actionable:
 * the folder is read-only, there is already a file called that beside this one. A flat "that did
 * not work" throws away the half somebody can do something about.
 */
/* Not exported: the only thing that returns one is `saveClip` below, and every caller reads
   it off that return type. An export nobody imports is a name marked shareable that nobody
   shares, which is what `public-surface.test.ts` refuses. */
type ClipOutcome = { made: true } | { made: false; because: string };

/**
 * Cut a stretch of a video into the library as a clip.
 *
 * ONE path, called from wherever a marked stretch is turned into a file, so a clip made from the
 * player and a clip made from anywhere else cannot come to mean two different things.
 *
 * **A clip and not a trim, and the difference is the whole promise.** They are one command on the
 * server and differ in one thing: a trim copies the packets already in the file, which is instant
 * and can only begin every few seconds; a clip decodes and re-encodes, which begins precisely where
 * the mark does. A 1.9 second mark on a file with a starting point every 5 seconds would come back
 * from a trim as a 6 second piece.
 *
 * `asLoop` asks the server to put a row on the Loops screen for the file it produces, about THAT
 * file, whole, rather than about the video it was cut from.
 *
 * **Preflighted first**, so a refusal arrives as a sentence rather than as a job that fails out of
 * sight. Nothing is overwritten: an edit always writes a new file beside the original, which is the
 * promise the whole editor is built on.
 *
 * **No filename is sent, deliberately.** Sift derives one carrying the moment the cut starts at
 * (`holiday-from-23s.mp4`), and that is better than anything a caller can send: naming the copy
 * after the VIDEO would make every clip of one video the same name, and the second would be refused
 * because something is already called that.
 */
export async function clipTheStretch(
	assetId: string,
	startMs: number,
	durationMs: number,
	options: { asLoop?: boolean } = {}
): Promise<ClipOutcome> {
	if (!Number.isFinite(durationMs) || durationMs <= 0) {
		return { made: false, because: 'That has no length to save.' };
	}
	const request: EditRequest = {
		steps: [
			{
				operation: 'clip',
				start_ms: Math.max(0, Math.round(startMs)),
				duration_ms: Math.round(durationMs)
			}
		],
		/*
		 * Ask for the loop in the SAME request, rather than posting one separately first.
		 *
		 * A loop written from the screen would be about the SOURCE video, and opening it would play
		 * the whole film with two markers on the bar. The row has to be about the CLIP, and the
		 * clip does not exist yet: it is a background encode whose id is not known until it lands.
		 * So the intent travels with the edit and the server marks the file it produced.
		 */
		as_loop: options.asLoop === true
	};
	try {
		const verdict = await preflight(assetId, request);
		if (!verdict.allowed) {
			// `reason`, which is what the answer actually calls it. A wrong field name here would
			// make every refusal show the fallback while the real sentence was fetched and thrown
			// away, and a wrong field name reads as a working screen.
			return { made: false, because: verdict.reason || "That can't be saved as a clip." };
		}
		await start(assetId, request);
		return { made: true };
	} catch {
		return { made: false, because: "That couldn't be saved as a clip." };
	}
}
