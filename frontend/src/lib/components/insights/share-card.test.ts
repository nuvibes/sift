/* A recap card as a picture: what it is called, the lines it paints its words in, and what it
 * paints of a card the page laid out. The test browser has no canvas and no layout, so both are
 * stood in for: a canvas that writes down every call, and boxes for every element and every word
 * (a word starts a new line every forty letters). What is held is the order and the content of the
 * painting; the look of it is checked in a real browser beside its card on the DESIGN GALLERY. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import type { RecapCard as Card } from '$lib/library/recaps.svelte';

import RecapCard from './RecapCard.svelte';
import { cardPicture, linesOf, pictureName, type Word } from './share-card';

describe('a picture of a recap card', () => {
	it('is named for the recap and the card, safe as a file name', () => {
		expect(pictureName('week:2026-W39', 0)).toBe('recap-week-2026-W39-1.png');
	});

	/* The words are painted on the lines the page broke them into, each line from its own first
	   word, so the picture wraps where the card wraps. */
	it('paints the words on the lines the page broke them into', () => {
		const text = 'You viewed 19 hours last week.';
		const at = (word: string, left: number, top: number): Word => {
			const start = text.indexOf(word);
			return { start, end: start + word.length, left, top, height: 20 };
		};
		expect(
			linesOf(text, [
				at('You', 0, 0),
				at('viewed', 30, 1),
				at('19', 90, 0),
				at('hours', 0, 24),
				at('last', 50, 24),
				at('week.', 0, 48)
			])
		).toEqual([
			{ text: 'You viewed 19', left: 0, top: 0, height: 20 },
			{ text: 'hours last', left: 0, top: 24, height: 20 },
			{ text: 'week.', left: 0, top: 48, height: 20 }
		]);
		expect(linesOf('', [])).toEqual([]);
	});
});

/* ---- The stand-ins: a canvas that writes down what it is told, and a layout. ---- */

type Call = [string, ...unknown[]];
let calls: Call[] = [];
let canvasMissing = false;

function recorder(): CanvasRenderingContext2D {
	const gradient = (kind: string) => ({
		addColorStop: (at: number, colour: string) => calls.push([`${kind}.stop`, at, colour])
	});
	const target: Record<string, unknown> = {
		globalAlpha: 1,
		measureText: (text: string) => ({
			width: text.length * 10,
			fontBoundingBoxAscent: 14,
			fontBoundingBoxDescent: 4
		}),
		createLinearGradient: (...args: number[]) => {
			calls.push(['createLinearGradient', ...args]);
			return gradient('linear');
		},
		createRadialGradient: (...args: number[]) => {
			calls.push(['createRadialGradient', ...args]);
			return gradient('radial');
		}
	};
	return new Proxy(target, {
		get: (into, name: string) =>
			name in into ? into[name] : (...args: unknown[]) => void calls.push([name, ...args]),
		set: (into, name: string, value) => {
			into[name] = value;
			calls.push([`set ${name}`, value]);
			return true;
		}
	}) as unknown as CanvasRenderingContext2D;
}

const CARD_BOX = { left: 100, top: 50, width: 360, height: 640 };
const LINE = 40;

/* Each word's box: a line every forty letters of its text, a letter five pixels wide. */
function wordBox(this: Range): DOMRect[] {
	const line = Math.floor(this.startOffset / LINE);
	const left = CARD_BOX.left + 24 + (this.startOffset % LINE) * 5;
	const top = CARD_BOX.top + 300 + line * 20;
	return [{ left, top, height: 20, width: 5 } as DOMRect];
}

/* Styles the test document cannot work out: the ring's paint and hole, and the pictures' fit. */
const realStyle = window.getComputedStyle.bind(window);
function styleOf(element: Element): CSSStyleDeclaration {
	const real = realStyle(element);
	const over: Record<string, string> = {};
	if (element.classList.contains('ring')) {
		over.backgroundImage =
			'conic-gradient(rgb(10, 20, 30) 0deg 14deg, transparent 14deg 15deg, rgb(40, 50, 60) 15deg 29deg)';
		over['--hole'] = '62%';
	}
	if (element.classList.contains('ground')) over.filter = 'blur(32px)';
	if (element.classList.contains('picture') && element.classList.contains('mark'))
		over.objectFit = 'contain';
	if (element.classList.contains('framed')) {
		over.overflowX = 'hidden';
		over.borderTopLeftRadius = '10px';
	}
	if (element.closest('.head, .foot')) over.textTransform = 'uppercase';
	if (element.classList.contains('ground-scrim')) over.backgroundColor = 'rgb(5, 5, 5)';
	if (element.classList.contains('quiet')) over.backgroundColor = 'rgba(0, 0, 0, 0)';
	return new Proxy(real, {
		get: (into, name: string) => {
			if (name === 'getPropertyValue')
				return (key: string) => over[key] ?? into.getPropertyValue(key);
			if (name in over) return over[name];
			const value = Reflect.get(into, name, into);
			return typeof value === 'function' ? value.bind(into) : value;
		}
	});
}

beforeEach(() => {
	calls = [];
	canvasMissing = false;
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(((kind: string) =>
		canvasMissing || kind !== '2d' ? null : recorder()) as HTMLCanvasElement['getContext']);
	vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((done) =>
		done(new Blob(['png'], { type: 'image/png' }))
	);
	vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
		const box = this.classList.contains('recap-card')
			? CARD_BOX
			: { left: CARD_BOX.left + 24, top: CARD_BOX.top + 60, width: 240, height: 320 };
		return { ...box, x: box.left, y: box.top, right: 0, bottom: 0, toJSON: () => box } as DOMRect;
	});
	vi.spyOn(window, 'getComputedStyle').mockImplementation(styleOf);
	Object.defineProperty(Range.prototype, 'getClientRects', { value: wordBox, configurable: true });
	Object.defineProperty(Element.prototype, 'checkVisibility', {
		value(this: Element) {
			return !this.hasAttribute('data-unseen');
		},
		configurable: true
	});
	Object.defineProperty(HTMLImageElement.prototype, 'decode', {
		value: () => Promise.resolve(),
		configurable: true
	});
	Object.defineProperty(HTMLImageElement.prototype, 'complete', {
		get: () => true,
		configurable: true
	});
	Object.defineProperty(HTMLImageElement.prototype, 'naturalWidth', {
		get: () => 256,
		configurable: true
	});
	Object.defineProperty(HTMLImageElement.prototype, 'naturalHeight', {
		get: () => 256,
		configurable: true
	});
	Object.defineProperty(document, 'fonts', {
		value: { ready: Promise.resolve() },
		configurable: true
	});
});

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	vi.restoreAllMocks();
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
});

const piece = (text: string, over: Partial<Card['statement'][number]> = {}) => ({
	text,
	kind: null,
	id: null,
	href: null,
	gone: false,
	rest: [],
	lead: '',
	...over
});

function card(over: Partial<Card>): Card {
	return {
		id: 'headline',
		kind: 'headline',
		statement: [piece('You viewed 19 hours last week: 6 of videos, 6 of pictures, 5 in Theater.')],
		figure: {
			label: 'Viewed',
			value: 68_400_000,
			unit: 'ms',
			hidden_part: 0,
			caption: [],
			defines: [],
			said: '19 h',
			hidden_said: '',
			trend: []
		},
		cover: null,
		rows: [],
		chart: null,
		hidden_things: [],
		hidden: false,
		...over
	};
}

function draw(one: Card): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(RecapCard, {
		target: host,
		props: { card: one, heading: 'Your week', place: '1 of 4', foot: 'September 21 to 27' }
	});
	flushSync();
	return host.querySelector('.recap-card') as HTMLElement;
}

const painted = () => calls.filter(([name]) => name === 'fillText').map(([, text]) => text);

describe('a card painted as a picture', () => {
	it("paints the ground, then every line of the card's words in order, at three times", async () => {
		const picture = await cardPicture(draw(card({})));

		expect(picture?.type).toBe('image/png');
		// 1080 wide is three times the card's 360.
		expect(calls).toContainEqual(['scale', 3, 3]);
		// The ground first: the accent's run top to foot, then its light.
		const names = calls.map(([name]) => name);
		expect(names.indexOf('createLinearGradient')).toBeLessThan(names.indexOf('fillText'));
		expect(calls).toContainEqual(['createLinearGradient', 0, 0, 0, 640]);
		expect(names).toContain('createRadialGradient');
		expect(calls.filter(([name]) => name === 'fillRect').length).toBeGreaterThanOrEqual(2);
		// The words: the head in capitals, the figure, the sentence on its two lines, the foot.
		expect(painted()).toEqual([
			'YOUR WEEK',
			'1 OF 4',
			'Viewed',
			'19 h',
			'You viewed 19 hours last week: 6 of videos,',
			'6 of pictures, 5 in Theater.',
			'SEPTEMBER 21 TO 27'
		]);
	});

	it("paints a Site's mark whole over its blurred ground, inside the frame's corner", async () => {
		await cardPicture(
			draw(
				card({
					kind: 'top_site',
					statement: [piece('Your most-viewed Site was '), piece('Quillhouse', { kind: 'site' })],
					cover: '/api/sites/s1/cover'
				})
			)
		);

		const names = calls.map(([name]) => name);
		// The frame clips, then the ground is drawn blurred and the mark contained over it.
		expect(names).toContain('clip');
		expect(calls).toContainEqual(['set filter', 'blur(96px)']);
		const pictures = calls.filter(([name]) => name === 'drawImage');
		expect(pictures).toHaveLength(2);
		// Contained: a 256 square in a 240 x 320 box is 240 wide, standing 40 down from the top.
		expect(pictures[1].slice(2)).toEqual([24, 100, 240, 240]);
		expect(names.indexOf('clip')).toBeLessThan(names.indexOf('drawImage'));
		expect(calls).toContainEqual(['set fillStyle', 'rgb(5, 5, 5)']);
	});

	it('paints a ranked list row by row, and the ring as its hours', async () => {
		const row = (name: string, cover: string | null = null) => ({
			piece: piece(name, { kind: 'person', id: name, href: '#' }),
			value: 3_600_000,
			unit: 'ms' as const,
			said: '1 h',
			cover
		});
		await cardPicture(
			draw(
				card({
					kind: 'top_five',
					figure: null,
					statement: [piece('The people you viewed most.')],
					rows: [row('Elina Sorrel', '/api/people/p1/cover'), row('Cassia Lynn')]
				})
			)
		);
		const words = painted();
		expect(words.indexOf('Elina Sorrel')).toBeLessThan(words.indexOf('Cassia Lynn'));
		expect(words).toContain('The people you viewed most.');

		calls = [];
		mounted && void unmount(mounted);
		host.remove();
		const hours = Array.from({ length: 24 }, (_, hour) => ({
			label: String(hour),
			said: '',
			parts: [{ kind: 'all', value: hour, said: '' }]
		}));
		await cardPicture(draw(card({ kind: 'when', chart: { bars: hours } as Card['chart'] })));
		// Two painted hours of the stood-in paint, each an arc out and an arc back.
		const fills = calls.filter(([name]) => name === 'set fillStyle').map(([, value]) => value);
		expect(fills).toContain('rgb(10, 20, 30)');
		expect(fills).toContain('rgb(40, 50, 60)');
		expect(calls.filter(([name]) => name === 'arc')).toHaveLength(4);
	});

	it('paints nothing of a card that may not be saved, nor of one not laid out', async () => {
		expect(await cardPicture(draw(card({ hidden: true, statement: [] })))).toBeNull();
		mounted && void unmount(mounted);
		host.remove();
		expect(await cardPicture(draw(card({ hidden_things: ['p1'] })))).toBeNull();
		expect(calls).toEqual([]);

		const loose = document.createElement('article');
		loose.dataset.savable = 'true';
		vi.mocked(Element.prototype.getBoundingClientRect).mockReturnValue({ width: 0 } as DOMRect);
		expect(await cardPicture(loose)).toBeNull();
	});

	it('is no picture where the browser gives no canvas, and leaves out what is not seen', async () => {
		canvasMissing = true;
		expect(await cardPicture(draw(card({})))).toBeNull();
		mounted && void unmount(mounted);
		host.remove();

		canvasMissing = false;
		const drawn = draw(card({}));
		drawn.querySelector('.foot')?.setAttribute('data-unseen', '');
		drawn.querySelector('.head')?.classList.add('quiet');
		await cardPicture(drawn);
		expect(painted()).not.toContain('SEPTEMBER');
	});

	it('is no picture where the canvas cannot be written out', async () => {
		vi.mocked(HTMLCanvasElement.prototype.toBlob).mockImplementation(() => {
			throw new Error('tainted');
		});
		expect(await cardPicture(draw(card({})))).toBeNull();
	});
});
