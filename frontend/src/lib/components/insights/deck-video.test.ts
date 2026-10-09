/* The deck as a video: the moments of a card's motion, its figures mid-count, the frames as the
 * server reads them, and a card filmed from its painting. The painting itself is `share-card`'s,
 * held in its own test; here it is stood in for. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { RecapCard as Card } from '$lib/library/recaps.svelte';

const { paintCard, postForFile } = vi.hoisted(() => ({
	paintCard: vi.fn(),
	postForFile: vi.fn()
}));
vi.mock('$lib/components/insights/share-card', () => ({ paintCard }));
vi.mock('$lib/api/client', () => ({ api: { postForFile } }));

import {
	CARD_MS,
	FPS,
	countingOf,
	encodeFilm,
	filmCard,
	filmName,
	filmOf,
	filmed,
	moments,
	type Moment
} from './deck-video';
import { motion } from '$lib/shell/motion.svelte';

type Figure = NonNullable<Card['figure']>;

const figure = (label: string, value: number, unit: Figure['unit'], said = ''): Figure => ({
	label,
	value,
	unit,
	hidden_part: 0,
	caption: [],
	defines: [],
	said,
	hidden_said: '',
	trend: []
});

function card(over: Partial<Card> = {}): Card {
	return {
		id: 'headline',
		kind: 'headline',
		headline: [],
		context: [],
		wall: null,
		accent_hue: null,
		statement: [],
		figure: figure('Viewed', 1000, 'count', '1,000'),
		cover: null,
		rows: [],
		chart: null,
		calendar: null,
		figures: [],
		hidden_things: [],
		hidden: false,
		...over
	};
}

describe("a card's motion", () => {
	it('runs a frame a thirtieth of a second: the slide, then the count', () => {
		const steps: Moment[] = moments(320, 600, false);
		expect(steps).toHaveLength(28);
		expect(steps[0]).toEqual({ arrived: 0, counted: null });
		// The first beat has no number: the slide lands at 320 ms, and only then the count runs.
		expect(steps[9].arrived).toBeLessThan(1);
		expect(steps[9].counted).toBeNull();
		expect(steps[10].arrived).toBe(1);
		expect(steps[10].counted).toBeGreaterThan(0);
		expect(steps[10].counted).toBeLessThan(1);
		expect(steps[27].counted).toBeGreaterThan(0.99);
	});

	it('only fades with reduced motion, its figures standing still', () => {
		const steps = moments(150, 600, true);
		expect(steps).toHaveLength(5);
		expect(steps.every((one) => one.counted === 1)).toBe(true);
		expect(moments(0, 0, true)).toEqual([]);
	});
});

describe("a card's figures mid-count", () => {
	it('say where they have counted to, and land on the words the server said', () => {
		const at = countingOf(
			card({
				figures: [
					figure('Sessions', 200, 'count', '200'),
					figure('First at', 550, 'minute_of_day', '9:10 AM')
				]
			})
		);
		expect(at(0.5)('1,000')).toBe('500');
		expect(at(0.5)('200')).toBe('100');
		// A time of day is not an amount, and words that are no figure's are left as they are.
		expect(at(0.5)('9:10 AM')).toBe('9:10 AM');
		expect(at(0.5)('Viewed')).toBe('Viewed');
		expect(at(1)('1,000')).toBe('1,000');
		// Before the second beat a counting figure is not drawn at all; the rest are.
		expect(at(null)('1,000')).toBe('');
		expect(at(null)('9:10 AM')).toBe('9:10 AM');
		expect(at(null)('Viewed')).toBe('Viewed');
	});
});

describe('the film', () => {
	it('is each frame with its length and how long it is held, then its bytes', async () => {
		const film = filmOf([
			{ picture: new Blob([new Uint8Array([0xff, 0xd8, 1])]), held: 2 },
			{ picture: new Blob([new Uint8Array([0xff, 0xd8])]), held: 72 }
		]);
		const bytes = new Uint8Array(await film.arrayBuffer());
		expect([...bytes]).toEqual([0, 0, 0, 3, 0, 2, 0xff, 0xd8, 1, 0, 0, 0, 2, 0, 72, 0xff, 0xd8]);
	});

	it('is named for the recap, and offered for a year or a month', () => {
		expect(filmName('year:2025')).toBe('recap-year-2025.mp4');
		expect([filmed('year:2025'), filmed('month:2025-09'), filmed('week:2025-W39')]).toEqual([
			true,
			true,
			false
		]);
	});

	it('goes to the server as one file of a form, and comes back an MP4', async () => {
		postForFile.mockResolvedValue(new Blob(['mp4'], { type: 'video/mp4' }));
		const answer = await encodeFilm('r/1', new Blob(['frames']));
		expect(answer.type).toBe('video/mp4');
		const [path, form] = postForFile.mock.calls[0];
		expect(path).toBe('/insights/recaps/r%2F1/video');
		expect((form as FormData).get('frames')).toBeInstanceOf(Blob);
	});
});

describe('a card filmed', () => {
	let drawn: string[];

	beforeEach(() => {
		drawn = [];
		const canvas = () => ({ width: 1080, height: 1920 }) as HTMLCanvasElement;
		paintCard.mockImplementation(async (_element: HTMLElement, counting) => {
			drawn.push(counting ? counting('1,000') : 'none');
			return canvas();
		});
		vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((() => ({
			fillRect: vi.fn(),
			drawImage: vi.fn(),
			fillStyle: '',
			globalAlpha: 1
		})) as unknown as HTMLCanvasElement['getContext']);
		vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((done) =>
			done(new Blob(['frame'], { type: 'image/jpeg' }))
		);
	});

	afterEach(() => {
		vi.restoreAllMocks();
		paintCard.mockReset();
	});

	it('is its moving frames, then the last held for the rest of its three seconds', async () => {
		const frames = await filmCard(document.createElement('div'), card(), 'rgb(0, 0, 0)');
		expect(frames).not.toBeNull();
		const total = frames!.reduce((sum, one) => sum + one.held, 0);
		expect(total).toBe((CARD_MS * FPS) / 1000);
		expect(frames!.slice(0, -1).every((one) => one.held === 1)).toBe(true);
		// The card arrives with no number, then the figure counts up to the one it lands on.
		expect(drawn[0]).toBe('');
		const counted = drawn.slice(1).map((one) => Number(one.replace(/,/g, '')));
		expect(counted.at(-1)).toBe(1000);
		expect(counted).toEqual([...counted].sort((a, b) => a - b));
		// The slide is painted once, not a frame at a time.
		expect(drawn.filter((one) => one === '')).toHaveLength(1);
	});

	it('paints a card once when nothing on it counts', async () => {
		vi.spyOn(motion, 'reduced', 'get').mockReturnValue(true);
		await filmCard(document.createElement('div'), card({ figure: null }), 'rgb(0, 0, 0)');
		expect(drawn).toHaveLength(1);
	});

	it('is no film where a card cannot be painted or a frame written out', async () => {
		paintCard.mockResolvedValue(null);
		expect(await filmCard(document.createElement('div'), card(), 'black')).toBeNull();
		paintCard.mockResolvedValue({ width: 1080, height: 1920 });
		vi.mocked(HTMLCanvasElement.prototype.toBlob).mockImplementation(() => {
			throw new Error('tainted');
		});
		expect(await filmCard(document.createElement('div'), card(), 'black')).toBeNull();
	});
});
