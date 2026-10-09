/*
 * A recap's deck as a video: every savable card drawn frame by frame on the client, with the
 * card's own motion, and handed to the server's ffmpeg to be encoded (`video_router.py`).
 *
 * THE SCREEN'S MOTION, NOT A SECOND ONE, in the card's two beats. First the card arrives as the
 * deck turns to it (`arrive` at the slow pace: it fades in from a little to the side, on
 * `--ease`) with its picture and its words and no number yet; then its numbers count up over
 * `--dur-ambient` on the same curve (`countUp`), and it is held, three seconds a card. The
 * durations and the curve are read from the same tokens those two read, and reduced motion is
 * honoured the same way: the card fades rather than slides, and both beats land together.
 *
 * Each frame is the card painted by `share-card.ts`, the same painting as its picture, at 1080 x
 * 1920, so a card in the video is its picture in every line. Only a frame that differs is sent:
 * the frames of the slide and the count, and then one frame held for the rest of the card's
 * three seconds. A card that may not be saved is not in the film at all, and nothing but the
 * pictures this screen drew leaves it.
 */
import { api } from '$lib/api/client';
import type { RecapCard as Card } from '$lib/library/recaps.svelte';
import { bezier, durationToken, easingToken, motion } from '$lib/shell/motion.svelte';
import { figureWords, saidOf } from '$lib/components/insights/figures';
import { paintCard } from '$lib/components/insights/share-card';

/** Frames a second, as the server encodes them. */
export const FPS = 30;

/** How long each card is on screen, in milliseconds. */
export const CARD_MS = 3000;

/** How far a card slides in from, in px of a 360-wide card: the deck's own `TURN`. */
export const TURN = 32;

/** A frame and how many frames it is held for. */
export interface Held {
	picture: Blob;
	held: number;
}

/** The motion of one card at one moment: how far it has arrived, and how far its count has run;
 *  null before the second beat, while the card has no number yet. */
export interface Moment {
	arrived: number;
	counted: number | null;
}

/** The card's moving moments, one a frame: the slide, then the count. */
export function moments(slideMs: number, countMs: number, reduced: boolean): Moment[] {
	const ease = bezier(easingToken('--ease', [0.2, 0, 0, 1]));
	const counts = reduced ? 0 : countMs;
	const beat = reduced ? 0 : slideMs;
	const frames = Math.ceil(((reduced ? slideMs : beat + counts) * FPS) / 1000);
	return Array.from({ length: frames }, (_, frame) => {
		const at = (frame * 1000) / FPS;
		const counting = at - beat;
		return {
			arrived: slideMs > 0 ? ease(Math.min(1, at / slideMs)) : 1,
			counted: counting < 0 ? null : counts > 0 ? ease(Math.min(1, counting / counts)) : 1
		};
	});
}

/** Each counting figure of a card by its final words, as `countUp` draws it part of the way, and
 *  none of them before the second beat. */
export function countingOf(card: Card): (counted: number | null) => (final: string) => string {
	const figures = [...(card.figure ? [card.figure] : []), ...card.figures].filter(
		(one) => one.unit !== 'minute_of_day' && one.value > 0
	);
	const finals = new Map(figures.map((one) => [saidOf(one.said, one.value, one.unit), one]));
	return (counted) => (final) => {
		const one = finals.get(final);
		if (!one || counted === null) return one ? '' : final;
		if (counted >= 1) return final;
		return figureWords(Math.round(one.value * counted), one.unit);
	};
}

function jpeg(canvas: HTMLCanvasElement): Promise<Blob | null> {
	return new Promise((settle) => {
		try {
			canvas.toBlob((blob) => settle(blob), 'image/jpeg', 0.9);
		} catch {
			settle(null);
		}
	});
}

/** One frame: the page's ground, and the card on it as far as it has arrived. */
function frame(card: HTMLCanvasElement, ground: string, arrived: number, reduced: boolean) {
	const canvas = document.createElement('canvas');
	canvas.width = card.width;
	canvas.height = card.height;
	const context = canvas.getContext('2d');
	if (context === null) return null;
	context.fillStyle = ground;
	context.fillRect(0, 0, canvas.width, canvas.height);
	context.globalAlpha = arrived;
	const across = reduced ? 0 : (1 - arrived) * TURN * (card.width / 360);
	context.drawImage(card, across, 0);
	return canvas;
}

/** The frames of one card: its moving ones, then the last held for the rest of its time. */
export async function filmCard(
	element: HTMLElement,
	card: Card,
	ground: string
): Promise<Held[] | null> {
	const reduced = motion.reduced;
	const slideMs = motion.duration(durationToken('--dur-slow', 320));
	const steps = moments(slideMs, durationToken('--dur-ambient', 600), reduced);
	const counting = countingOf(card);
	const out: Held[] = [];
	let painted: { counted: number | null; canvas: HTMLCanvasElement } | null = null;
	for (const step of [...steps, { arrived: 1, counted: 1 }]) {
		if (painted === null || painted.counted !== step.counted) {
			const canvas = await paintCard(element, counting(step.counted));
			if (canvas === null) return null;
			painted = { counted: step.counted, canvas };
		}
		const drawn = frame(painted.canvas, ground, step.arrived, reduced);
		const picture = drawn ? await jpeg(drawn) : null;
		if (picture === null) return null;
		out.push({ picture, held: 1 });
	}
	out[out.length - 1].held = Math.max(1, Math.round((CARD_MS * FPS) / 1000) - steps.length);
	return out;
}

/** The frames as the server reads them: each its length and how long it is held, then its bytes. */
export function filmOf(frames: readonly Held[]): Blob {
	const parts: BlobPart[] = [];
	for (const one of frames) {
		const head = new DataView(new ArrayBuffer(6));
		head.setUint32(0, one.picture.size);
		head.setUint16(4, one.held);
		parts.push(head.buffer, one.picture);
	}
	return new Blob(parts, { type: 'application/octet-stream' });
}

/** What the video is called: the recap's, as its pictures are named. */
export function filmName(period: string): string {
	return `recap-${period.replace(/[^a-z0-9-]+/gi, '-')}.mp4`;
}

/** The decks offered as a video: a week's or a day's is pictures only. */
export function filmed(period: string): boolean {
	const kind = period.split(':')[0];
	return kind === 'year' || kind === 'month';
}

/** The film encoded by the server, as an MP4. */
export function encodeFilm(recapId: string, film: Blob): Promise<Blob> {
	const form = new FormData();
	form.set('frames', film, 'frames');
	return api.postForFile(`/insights/recaps/${encodeURIComponent(recapId)}/video`, form);
}
