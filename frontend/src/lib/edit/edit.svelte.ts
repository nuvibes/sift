/* Asking the server what an edit would do, and then asking it to do it: every judgement is the
 * server's, so none of the rules is worked out here. */

import { api } from '$lib/api/client';
import { lengthClock } from '$lib/shell/duration';
import type { SpriteSheet } from '$lib/player/trickplay';
import type { components } from '$lib/api/schema';

/** What the editor needs to know about the one file it is open on. */
export type EditableAsset = Pick<
	components['schemas']['AssetDetail'],
	'id' | 'media_type' | 'width' | 'height' | 'duration_ms' | 'filename' | 'art' | 'sprite'
>;

/** The six verbs, read from the server's schema so a new one is never missed. */
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

/** The size to aim at, asked once when the editor opens, before anything can be dragged. */
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

/** A moment in a file from milliseconds, as a length is written: `1:30`, `1:02:05`. */
export function clock(milliseconds: number): string {
	return lengthClock(Math.max(0, milliseconds) / 1000);
}

/** A clip's outcome; a refusal carries the server's own sentence, which says what to do. */
type ClipOutcome = { made: true } | { made: false; because: string };

/**
 * Cut a stretch of a video into the library as a clip: re-encoded, so it begins exactly at the
 * mark; preflighted, so a refusal is a sentence; no filename sent, so Sift names it by its start.
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
		// In the same request: the loop row has to be about the clip, whose id is not known yet.
		as_loop: options.asLoop === true
	};
	try {
		const verdict = await preflight(assetId, request);
		if (!verdict.allowed) {
			// `reason` is the answer's field; a wrong name would show the fallback on every refusal.
			return { made: false, because: verdict.reason || "That can't be saved as a clip." };
		}
		await start(assetId, request);
		return { made: true };
	} catch {
		return { made: false, because: "That couldn't be saved as a clip." };
	}
}
