/*
 * A recap card as a picture: the card's words, its figure and its picture drawn onto a canvas the
 * shape of a phone's screen, and handed to the one door every screenshot in Sift takes
 * (`$lib/player/snapshot`'s `deliver`: the clipboard, the library or a download, as the person's
 * own screenshot setting says).
 *
 * DRAWN, NOT PHOTOGRAPHED. A browser cannot read the pixels of its own page (only the desktop app's
 * shell can take the window), so the card is drawn again here from what it says, in the same
 * tokens: the ground is the accent's run, the figure in its tint, the words in the ink, the faces
 * the page is set in. What goes on it is exactly what the card on screen says and shows, and only
 * for a card that may be taken (`RecapCard`'s `shareable`): never a locked tile, never a card that
 * names something hidden.
 *
 * The picture on a card is the thing's own cover from Sift's own address, so drawing it leaves the
 * canvas readable; one that cannot be drawn (none, a 404, a failed decode) is left off and the card
 * is drawn without it rather than not at all.
 */
import { deliver } from '$lib/player/snapshot';
import type { RecapCard } from '$lib/library/recaps.svelte';

/** The picture's size: a phone's screen, stood up, at the resolution a phone shows one. */
const SHARE_WIDTH = 1080;
const SHARE_HEIGHT = 1920;

/** What the picture of a card says, all of it already in words. */
interface ShareCard {
	heading: string;
	place: string;
	span: string;
	label: string;
	figure: string;
	statement: string;
	cover: string | null;
}

/** A card's words as the picture says them: the server's, joined, never composed here. */
export function shareOf(card: RecapCard, heading: string, place: string, span: string): ShareCard {
	return {
		heading,
		place,
		span,
		label: card.figure?.label ?? '',
		figure: card.figure?.said ?? '',
		statement: card.statement.map((piece) => piece.text).join(''),
		cover: card.cover ?? null
	};
}

/** What a picture of a card is called: the recap and the card, so two cards of one recap differ. */
export function shareName(period: string, index: number): string {
	const safe = period.replace(/[^a-z0-9-]+/gi, '-');
	return `recap-${safe}-${index + 1}.png`;
}

/* A token's colour as the page resolves it, read off a probe element so a derived token (a mix in
   OKLCH) arrives as a colour the canvas can paint rather than as the expression. */
function colourOf(token: string): string {
	if (typeof document === 'undefined') return '';
	const probe = document.createElement('span');
	probe.style.setProperty('color', `var(${token})`);
	document.body.append(probe);
	const value = getComputedStyle(probe).color;
	probe.remove();
	return value;
}

function fontOf(token: string, fallback: string): string {
	if (typeof document === 'undefined') return fallback;
	return getComputedStyle(document.documentElement).getPropertyValue(token).trim() || fallback;
}

/* The words broken into lines no wider than `width`, a word at a time. */
function lines(context: CanvasRenderingContext2D, text: string, width: number): string[] {
	const out: string[] = [];
	let line = '';
	for (const word of text.split(/\s+/)) {
		const next = line ? `${line} ${word}` : word;
		if (line && context.measureText(next).width > width) {
			out.push(line);
			line = word;
		} else {
			line = next;
		}
	}
	if (line) out.push(line);
	return out;
}

async function pictureAt(address: string | null): Promise<HTMLImageElement | null> {
	if (!address) return null;
	const image = new Image();
	image.src = address;
	try {
		await image.decode();
		return image.naturalWidth > 0 ? image : null;
	} catch {
		return null;
	}
}

/** The card drawn onto a canvas, as a PNG. Null where the browser gives no canvas to draw on. */
async function drawShareCard(card: ShareCard): Promise<Blob | null> {
	const canvas = document.createElement('canvas');
	canvas.width = SHARE_WIDTH;
	canvas.height = SHARE_HEIGHT;
	const context = canvas.getContext('2d');
	if (context === null) return null;

	const display = fontOf('--font-display', 'system-ui, sans-serif');
	const text = fontOf('--font-sans', 'system-ui, sans-serif');
	const ink = colourOf('--sift-ink');
	const ink2 = colourOf('--sift-ink-2');
	const tint = colourOf('--sift-accent-tint-1');

	const ground = context.createLinearGradient(0, 0, 0, SHARE_HEIGHT);
	ground.addColorStop(0, colourOf('--sift-accent-shade-1'));
	ground.addColorStop(1, colourOf('--sift-accent-shade-2'));
	context.fillStyle = ground;
	context.fillRect(0, 0, SHARE_WIDTH, SHARE_HEIGHT);

	const edge = 96;
	const inner = SHARE_WIDTH - 2 * edge;
	context.textBaseline = 'alphabetic';

	// The head: the recap's name and the card's place in it.
	context.font = `600 34px ${text}`;
	context.fillStyle = ink2;
	context.fillText(card.heading.toUpperCase(), edge, edge + 34);
	context.textAlign = 'right';
	context.fillText(card.place.toUpperCase(), SHARE_WIDTH - edge, edge + 34);
	context.textAlign = 'left';

	// The picture, cut to the 3:4 a cover is shot at, with the card's corner.
	const picture = await pictureAt(card.cover);
	if (picture) {
		const width = 640;
		const height = 853;
		const x = (SHARE_WIDTH - width) / 2;
		const y = 230;
		const scale = Math.max(width / picture.naturalWidth, height / picture.naturalHeight);
		const w = picture.naturalWidth * scale;
		const h = picture.naturalHeight * scale;
		context.save();
		context.beginPath();
		context.roundRect(x, y, width, height, 36);
		context.clip();
		context.drawImage(picture, x + (width - w) / 2, y + (height - h) / 2, w, h);
		context.restore();
	}

	// From the foot up: the span and the mark, the sentence, the figure and its label.
	let y = SHARE_HEIGHT - edge;
	context.font = `500 32px ${text}`;
	context.fillStyle = ink2;
	context.fillText(card.span, edge, y);
	context.font = `600 44px ${display}`;
	context.fillStyle = ink;
	context.textAlign = 'right';
	context.fillText('Sift', SHARE_WIDTH - edge, y);
	context.textAlign = 'left';
	y -= 110;

	context.font = `600 64px ${display}`;
	const said = lines(context, card.statement, inner).slice(0, 6);
	context.fillStyle = ink;
	for (const [at, line] of said.entries()) {
		context.fillText(line, edge, y - (said.length - 1 - at) * 78);
	}
	y -= said.length * 78 + 40;

	if (card.figure) {
		context.font = `600 190px ${display}`;
		context.fillStyle = tint;
		context.fillText(card.figure, edge, y);
		y -= 210;
		context.font = `500 38px ${text}`;
		context.fillStyle = ink2;
		context.fillText(card.label, edge, y);
	}

	return new Promise((settle) => {
		try {
			canvas.toBlob((blob) => settle(blob), 'image/png');
		} catch {
			settle(null);
		}
	});
}

/** Draw the card and hand it to where this person's screenshots go. False where it could not. */
export async function shareCard(card: ShareCard, name: string): Promise<boolean> {
	const picture = await drawShareCard(card);
	if (picture === null) return false;
	await deliver(picture, name);
	return true;
}
