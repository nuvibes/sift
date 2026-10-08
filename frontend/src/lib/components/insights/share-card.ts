/*
 * A recap card as a picture: the card on screen painted again onto a canvas 1080 wide, for the
 * deck to hand to the one door every screenshot in Sift takes (`$lib/player/snapshot`'s
 * `deliver`: the clipboard, the library or a download, as the person's own screenshot setting
 * says).
 *
 * PAINTED FROM THE PAGE. A browser cannot read the pixels of its own page (only the desktop app's
 * shell can take the window), so the card is painted again from where the page laid it out: its
 * ground, every picture, the ring, and every line of its words in its own face and colour, at its
 * own place, three times over for a card 360 wide. One layout serves both, so the picture and the
 * card cannot drift. Only a card that may be saved is offered (`RecapCard`'s `savable`): never a
 * locked tile, never a card that names something hidden.
 *
 * A picture on the card that cannot be drawn (none, a 404, a failed decode) is left off, and the
 * card is saved without it rather than not at all.
 *
 * The deck's video paints the same way, frame by frame (`deck-video.ts`), with each figure at the
 * number it has counted up to by that frame (`counting`).
 */
/** The picture's width: a phone's screen at the resolution a phone shows one. */
const PICTURE_WIDTH = 1080;

/** What a picture of a card is called: the recap and the card, so two cards of one recap differ. */
export function pictureName(period: string, index: number): string {
	const safe = period.replace(/[^a-z0-9-]+/gi, '-');
	return `recap-${safe}-${index + 1}.png`;
}

/** A word of a run of text, and the box the page drew it in. */
export interface Word {
	start: number;
	end: number;
	left: number;
	top: number;
	height: number;
}

/** A line of a run of text as the page broke it: its words, and where its first one starts. */
export interface Line {
	text: string;
	left: number;
	top: number;
	height: number;
}

/** The words of a run joined into the lines the page broke them into: a word whose box stands
 *  lower than the line's middle starts the next line. */
export function linesOf(text: string, words: readonly Word[]): Line[] {
	const lines: Line[] = [];
	let first: Word | null = null;
	let last: Word | null = null;
	const close = () => {
		if (first && last) {
			lines.push({
				text: text.slice(first.start, last.end),
				left: first.left,
				top: first.top,
				height: first.height
			});
		}
	};
	for (const word of words) {
		if (first && word.top < first.top + first.height / 2) {
			last = word;
			continue;
		}
		close();
		first = word;
		last = word;
	}
	close();
	return lines;
}

/* A colour as the card resolves it, read off a probe inside it, so a derived token (a mix in
   OKLCH) arrives as a colour the canvas can paint rather than as the expression. */
function colourOf(card: HTMLElement, colour: string): string {
	const probe = document.createElement('span');
	probe.style.setProperty('color', colour);
	card.append(probe);
	const value = getComputedStyle(probe).color;
	probe.remove();
	return value;
}

/* A length as written in a computed style, in px; a share of `of` where it is a percentage. */
function pixels(value: string, of: number): number {
	const number = Number.parseFloat(value);
	if (Number.isNaN(number)) return 0;
	return value.endsWith('%') ? (number * of) / 100 : number;
}

interface Painting {
	context: CanvasRenderingContext2D;
	origin: DOMRect;
	scale: number;
	/** A figure's words at this moment, given its final words; absent, the final words. */
	counting?: (final: string) => string;
}

function boxOf(painting: Painting, element: Element) {
	const rect = element.getBoundingClientRect();
	return {
		x: rect.left - painting.origin.left,
		y: rect.top - painting.origin.top,
		width: rect.width,
		height: rect.height
	};
}

function roundedBox(painting: Painting, element: Element, style: CSSStyleDeclaration): void {
	const box = boxOf(painting, element);
	const radius = Math.min(
		pixels(style.borderTopLeftRadius, box.width),
		box.width / 2,
		box.height / 2
	);
	painting.context.beginPath();
	painting.context.roundRect(box.x, box.y, box.width, box.height, radius);
}

/* The card's ground, as its stylesheet draws it: the accent's run deepest at the foot, and a light
   of the first shade over the top. */
function paintGround(painting: Painting, card: HTMLElement): void {
	const { context } = painting;
	const { width, height } = painting.origin;
	const shade1 = colourOf(card, 'var(--sift-accent-shade-1)');
	const run = context.createLinearGradient(0, 0, 0, height);
	run.addColorStop(0, shade1);
	run.addColorStop(1, colourOf(card, 'var(--sift-accent-shade-2)'));
	context.fillStyle = run;
	context.fillRect(0, 0, width, height);
	// radial-gradient(120% 60% at 20% 0%, shade-1, transparent 70%): an ellipse, drawn as a circle
	// squeezed to its height.
	const across = 1.2 * width;
	context.save();
	context.translate(0.2 * width, 0);
	context.scale(1, (0.6 * height) / across);
	const light = context.createRadialGradient(0, 0, 0, 0, 0, across);
	light.addColorStop(0, shade1);
	// The shade itself, clear: a canvas fades towards the stop's own colour, so `transparent`
	// (clear black) would darken the edge of the light where CSS does not.
	light.addColorStop(0.7, colourOf(card, 'rgb(from var(--sift-accent-shade-1) r g b / 0)'));
	context.fillStyle = light;
	context.fillRect(-across, -across, 2 * across, 2 * across);
	context.restore();
}

/* The ring: a conic gradient of one colour per hour, its middle cut out at the ring's `--hole`. */
const SEGMENT = /([a-z-]+\([^()]*\)|transparent|#[0-9a-f]+)\s+([\d.]+)deg\s+([\d.]+)deg/gi;

function paintRing(painting: Painting, element: Element, style: CSSStyleDeclaration): void {
	const box = boxOf(painting, element);
	const outer = Math.min(box.width, box.height) / 2;
	const inner = (outer * pixels(style.getPropertyValue('--hole') || '0%', 100)) / 100;
	const x = box.x + box.width / 2;
	const y = box.y + box.height / 2;
	const turn = (degrees: number) => ((degrees - 90) * Math.PI) / 180;
	for (const [, colour, from, to] of style.backgroundImage.matchAll(SEGMENT)) {
		if (colour === 'transparent') continue;
		painting.context.beginPath();
		painting.context.arc(x, y, outer, turn(Number(from)), turn(Number(to)));
		painting.context.arc(x, y, inner, turn(Number(to)), turn(Number(from)), true);
		painting.context.fillStyle = colour;
		painting.context.fill();
	}
}

function paintPicture(painting: Painting, image: HTMLImageElement, style: CSSStyleDeclaration) {
	if (!image.complete || image.naturalWidth === 0) return;
	const box = boxOf(painting, image);
	const fit = style.objectFit;
	const across = image.naturalWidth;
	const down = image.naturalHeight;
	const scale =
		fit === 'contain'
			? Math.min(box.width / across, box.height / down)
			: Math.max(box.width / across, box.height / down);
	const width = fit === 'fill' ? box.width : across * scale;
	const height = fit === 'fill' ? box.height : down * scale;
	const blur = /blur\(([\d.]+)px\)/.exec(style.filter);
	painting.context.save();
	if (blur) painting.context.filter = `blur(${Number(blur[1]) * painting.scale}px)`;
	painting.context.drawImage(
		image,
		box.x + (box.width - width) / 2,
		box.y + (box.height - height) / 2,
		width,
		height
	);
	painting.context.restore();
}

function paintWords(painting: Painting, node: Text, style: CSSStyleDeclaration): void {
	const { context } = painting;
	// A figure still counting up is painted at the figure it lands on, or where a frame has it.
	const landed = node.parentElement?.closest('[data-final]')?.getAttribute('data-final');
	const final = landed == null ? null : (painting.counting?.(landed) ?? landed);
	const words: Word[] = [];
	const range = document.createRange();
	for (const match of node.data.matchAll(/\S+/g)) {
		range.setStart(node, match.index);
		range.setEnd(node, match.index + match[0].length);
		const rect = range.getClientRects()[0];
		if (!rect) continue;
		words.push({
			start: match.index,
			end: match.index + match[0].length,
			left: rect.left - painting.origin.left,
			top: rect.top - painting.origin.top,
			height: rect.height
		});
	}
	context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
	context.letterSpacing = style.letterSpacing === 'normal' ? '0px' : style.letterSpacing;
	context.fillStyle = style.color;
	context.textBaseline = 'alphabetic';
	for (const line of linesOf(node.data, words)) {
		const text = final ?? line.text;
		const said = style.textTransform === 'uppercase' ? text.toUpperCase() : text;
		const metrics = context.measureText(said);
		const ascent = metrics.fontBoundingBoxAscent;
		const descent = metrics.fontBoundingBoxDescent;
		context.fillText(said, line.left, line.top + (line.height - ascent - descent) / 2 + ascent);
	}
}

function paintElement(painting: Painting, element: Element, card: HTMLElement): void {
	if (!element.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return;
	const style = getComputedStyle(element);
	const { context } = painting;
	context.save();
	context.globalAlpha *= Number(style.opacity);
	if (element !== card) {
		if (style.backgroundImage.startsWith('conic-gradient')) paintRing(painting, element, style);
		const ground = style.backgroundColor;
		if (ground && ground !== 'transparent' && !/,\s*0\)$/.test(ground)) {
			roundedBox(painting, element, style);
			context.fillStyle = ground;
			context.fill();
		}
		if (style.overflowX !== 'visible') {
			roundedBox(painting, element, style);
			context.clip();
		}
	}
	if (element instanceof HTMLImageElement) paintPicture(painting, element, style);
	for (const child of element.childNodes) {
		if (child instanceof Text && child.data.trim()) paintWords(painting, child, style);
		else if (child instanceof HTMLElement) paintElement(painting, child, card);
	}
	context.restore();
}

/** The card painted at 1080 wide onto a canvas, each counting figure at `counting`'s words. Null
 *  for a card that may not be saved (the second lock on the deck's door), one not laid out, or
 *  where the browser gives no canvas. */
export async function paintCard(
	card: HTMLElement,
	counting?: (final: string) => string
): Promise<HTMLCanvasElement | null> {
	if (card.dataset.savable !== 'true') return null;
	if (card.getBoundingClientRect().width === 0) return null;
	const canvas = document.createElement('canvas');
	const context = canvas.getContext('2d');
	if (context === null) return null;
	await document.fonts.ready;
	await Promise.all(
		[...card.querySelectorAll('img')].map((image) => image.decode().catch(() => undefined))
	);
	// Read after the waits: the page may have moved under the card while they ran.
	const origin = card.getBoundingClientRect();
	const scale = PICTURE_WIDTH / origin.width;
	canvas.width = PICTURE_WIDTH;
	canvas.height = Math.round(origin.height * scale);
	context.scale(scale, scale);
	const painting = { context, origin, scale, counting };
	paintGround(painting, card);
	paintElement(painting, card, card);
	return canvas;
}

/** The card painted at 1080 wide, as a PNG, or null where `paintCard` gives no canvas. */
export async function cardPicture(card: HTMLElement): Promise<Blob | null> {
	const canvas = await paintCard(card);
	if (canvas === null) return null;
	return new Promise((settle) => {
		try {
			canvas.toBlob((blob) => settle(blob), 'image/png');
		} catch {
			settle(null);
		}
	});
}
