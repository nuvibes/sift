import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';

import type { ReferenceStrengths, Strength } from '$lib/people/faces.svelte';

import RecognitionStrength, { startersSay } from './RecognitionStrength.svelte';

/*
 * A number nothing else shows.
 *
 * Matching compares a new face against every reference somebody has, so a person with three of them
 * under-matches, correctly, quietly, and with nothing on any screen to say why they are never
 * recognized. After importing people from folders, the ones with two usable photos look exactly
 * like the ones with fifty.
 *
 * What is asserted here is the half this component owns. The BANDS are the server's (five, ten
 * and twenty, proved beside the numbers in `test_references.py`) and this file holds the sentence
 * each one comes to. The two absences are the point as well: a bar reading zero over a feature
 * nobody switched on reports a fault where there is none.
 */

vi.mock('$lib/people/faces.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	recognitionOf: vi.fn()
}));

const { recognitionOf } = await import('$lib/people/faces.svelte');
const asked = vi.mocked(recognitionOf);

function strength(over: Partial<Strength> = {}): Strength {
	return {
		floor: 3,
		fraction: 0.5,
		references: 6,
		strong: 12,
		target: 24,
		verdict: 'fair',
		...over
	} as Strength;
}

let host: HTMLElement;

beforeEach(() => {
	vi.clearAllMocks();
});

afterEach(() => {
	host?.remove();
});

async function draw(answer: Strength | null): Promise<HTMLElement> {
	asked.mockResolvedValue(answer);
	host = document.createElement('div');
	document.body.append(host);
	mount(RecognitionStrength, { target: host, props: { personId: 'p1', name: 'Ada Byron' } });
	// The read is a promise; the block is behind it.
	await vi.waitFor(() => {
		flushSync();
		if (asked.mock.calls.length === 0) throw new Error('not asked yet');
	});
	await Promise.resolve();
	flushSync();
	return host;
}

describe('how reliably one person can be recognized', () => {
	it('says nothing at all where recognition was never switched on', async () => {
		/* Absent entirely rather than drawn at zero: most installs never turn faces on, and a bar
		   reading nought over a feature nobody asked for reports a fault where there is none. */
		await draw(null);

		expect(host.querySelector('[role="meter"]')).toBeNull();
	});

	it('says nothing where nobody here has ever been recognized', async () => {
		await draw(strength({ verdict: 'none', references: 0, fraction: 0 }));

		expect(host.querySelector('[role="meter"]')).toBeNull();
	});

	it('draws a meter full at the dependable number the SERVER sent, never the target', async () => {
		/* Full where matching becomes dependable. The target only steers learning and group naming,
		   so it reaches neither the bar nor anything a screen reader says. */
		await draw(strength({ references: 6, floor: 4, strong: 12, target: 31 }));

		const meter = host.querySelector('[role="meter"]');
		expect(meter?.getAttribute('aria-valuenow')).toBe('6');
		expect(meter?.getAttribute('aria-valuemax')).toBe('12');
		expect(meter?.getAttribute('aria-valuemin')).toBe('0');
		expect(host.textContent).not.toContain('31');
		for (const { value } of meter?.attributes ?? []) expect(value).not.toBe('31');
	});

	it("says what the server's numbers mean for this person, one picture in the singular", async () => {
		await draw(strength({ verdict: 'weak', references: 2, floor: 5, strong: 10, target: 20 }));
		expect(host.querySelector('.basis')?.textContent?.trim()).toBe(
			'Recognized from 2 pictures. Matching is weak under 5 and dependable from 10.'
		);

		host.remove();
		await draw(strength({ verdict: 'weak', references: 1, floor: 3, strong: 7, target: 20 }));
		expect(host.querySelector('.basis')?.textContent?.trim()).toBe(
			'Recognized from 1 picture. Matching is weak under 3 and dependable from 7.'
		);
	});

	it('says the verdict in words, at every boundary the server bands on', async () => {
		/*
		 * The four sentences and the counts they turn over at. The BANDS are the server's (five,
		 * ten and twenty, proved in `test_references.py`) and what is asserted here is that each
		 * token becomes the right sentence, at the count on either side of each edge.
		 *
		 * Nobody outside this feature knows what a reference is, so the line under the bar is not
		 * a count of them. The number a person came for is said plainly one line above this,
		 * in the words of the act that produced it.
		 */
		for (const [verdict, references, words] of [
			['weak', 4, "Sift can't identify Ada yet"],
			['fair', 5, 'Sift can now identify Ada reasonably well'],
			['fair', 9, 'Sift can now identify Ada reasonably well'],
			['good', 10, 'Sift identifies Ada reliably'],
			['good', 19, 'Sift identifies Ada reliably'],
			['strong', 20, 'Sift identifies Ada reliably']
		] as const) {
			host?.remove();
			await draw(strength({ verdict, references, target: 20, floor: 5, strong: 10 }));
			const meter = host.querySelector('[role="meter"]');
			// Full at dependable: past it the meter holds at its top.
			expect(meter?.getAttribute('aria-valuenow')).toBe(String(Math.min(references, 10)));
			expect(host.querySelector('.count .words')?.textContent?.trim()).toBe(words);
		}
	});

	it('says nothing about references, and no advice sentence', async () => {
		/* The band is furniture above a wall of files: the bar, the verdict and its numbers, with
		   no advice line and no word a person outside this feature would not know. */
		await draw(strength({ verdict: 'good', references: 13, target: 20, floor: 5, strong: 10 }));

		expect(host.textContent).not.toMatch(/reference/i);
		expect(host.textContent).not.toMatch(/of about/);
		expect(host.textContent, 'there is no advice sentence, on purpose').not.toMatch(/\bmore\b/);
	});

	it('paints the fill with the band the SERVER named', async () => {
		/* The colour is the verdict, so it comes down as the verdict. A band worked out here from
		   the count would be a second opinion about a number the server has already banded, and the
		   day the curve moves the bar and the sentence under it say different things. */
		for (const verdict of ['none', 'weak', 'fair', 'good', 'strong'] as const) {
			host?.remove();
			await draw(strength({ verdict, references: 6, target: 20, floor: 5, strong: 10 }));
			expect(host.querySelector('.spectrum')?.getAttribute('data-band')).toBe(verdict);
		}
	});

	it('gives each band its own gradient, and four of them are four colours', async () => {
		/*
		 * Read off the stylesheet rather than the element: jsdom computes no cascade and resolves
		 * no `color-mix`, so an element in the right band says nothing about what it is painted.
		 *
		 * What is pinned is the shape: red running into orange under the floor, orange into yellow,
		 * yellow into green, and green once it is strong, four distinct runs, one per band. The
		 * rule with no attribute is the floor band, also the fallback for a token this client has
		 * never heard of.
		 */
		const css = readFileSync(resolve('src/lib/components/RecognitionStrength.svelte'), 'utf8');
		const painted: Record<string, string> = {};
		for (const [, band, body] of css.matchAll(
			/\.spectrum(?:\[data-band='(\w+)'\])?\s*\{([^}]*)\}/g
		)) {
			const background = body.match(/background:\s*([^;]+);/);
			if (background) painted[band ?? 'floor'] = background[1].replace(/\s+/g, ' ').trim();
		}

		expect(Object.keys(painted).sort()).toEqual(['fair', 'floor', 'good', 'strong']);
		expect(new Set(Object.values(painted)).size, 'two bands are the same colour').toBe(4);
		/*
		 * Three semantic tokens and nothing else named: orange is mixed from the two it sits
		 * between. The red is `--sift-bad-text`, that token solved to be read on a surface; the
		 * plain `--sift-bad` comes to 2.41:1 on this bar's track in the lightest base, under the
		 * 3:1 floor for a meaningful graphic. See the component.
		 */
		expect(painted.floor).toContain('var(--sift-bad-text)');
		expect(painted.fair).toContain('var(--sift-warn)');
		expect(painted.good).toContain('var(--sift-ok)');
		expect(painted.strong).not.toContain('var(--sift-warn)');
		/*
		 * The quiet end of "strong" is a darker green, never the track: the track is a blue-grey
		 * and OKLCH carries its hue into the mix, which would turn the start teal. `--sift-ok-bg`
		 * is the same green solved as a dark tint, within four degrees of the token's hue in every
		 * base. See the component.
		 */
		expect(painted.strong).toContain('var(--sift-ok-bg)');
		expect(
			Object.values(painted).join(' '),
			'a band mixes towards the blue-grey track'
		).not.toContain('var(--sift-surface');
		// No colour of its own anywhere in the four: tokens, and mixes of tokens.
		expect(Object.values(painted).join(' ')).not.toMatch(/#[0-9a-fA-F]{3}|rgb\(|hsl\(|oklch\(/);
	});

	it('falls back to the plainest of the four rather than drawing a blank line', async () => {
		/* A client one release behind a server that grows a sixth band. An empty line under a bar
		   reads as something having failed; "not enough yet" is the safe direction: it asks for
		   more of the thing that can only help. */
		await draw(strength({ verdict: 'unheard-of', references: 6, target: 20 }));

		expect(host.querySelector('.count .words')?.textContent?.trim()).toBe(
			"Sift can't identify Ada yet"
		);
	});
});

describe('a wall of people', () => {
	it("draws each card against the wall's dependable number, never its target", () => {
		/* The wall hands every count over in one reading, so the card makes no request of its own. */
		const strengths: ReferenceStrengths = {
			floor: 5,
			strong: 10,
			target: 20,
			people: { p1: 5 },
			verdicts: { p1: 'fair' }
		};
		host = document.createElement('div');
		document.body.append(host);
		mount(RecognitionStrength, {
			target: host,
			props: { personId: 'p1', name: 'Ada Byron', strengths }
		});
		flushSync();

		expect(asked).not.toHaveBeenCalled();
		const meter = host.querySelector('[role="meter"]');
		expect(meter?.getAttribute('aria-valuenow')).toBe('5');
		expect(meter?.getAttribute('aria-valuemax')).toBe('10');
		// Half of ten, where half of the target would leave three quarters of the track empty.
		expect(host.querySelector<HTMLElement>('.spectrum')?.style.clipPath).toBe('inset(0 50% 0 0)');
		expect(host.querySelector('.basis')?.textContent?.trim()).toBe(
			'Recognized from 5 pictures. Matching is weak under 5 and dependable from 10.'
		);
	});
});

describe('starter pictures', () => {
	it('are told to the page and never drawn under the bar', async () => {
		/* The bar measures what Sift knows them by, and a starter adds nothing to that. The page
		   says where the waiting faces came from, in one line under them. */
		const told: (readonly string[] | null)[] = [];
		asked.mockResolvedValue(
			strength({ starters: 4, starters_from: ['StashDB', 'FansDB'] } as Partial<Strength>)
		);
		host = document.createElement('div');
		document.body.append(host);
		mount(RecognitionStrength, {
			target: host,
			props: { personId: 'p1', onstarters: (boxes) => told.push(boxes) }
		});
		await vi.waitFor(() => {
			flushSync();
			if (!told.some((boxes) => boxes !== null)) throw new Error('not told yet');
		});

		expect(told.at(-1)).toEqual(['StashDB', 'FansDB']);
		expect(host.textContent).not.toMatch(/starter/i);
		expect(host.querySelectorAll('.count')).toHaveLength(1);
	});

	it('tell the page there are none once Sift knows them by a confirmed face', async () => {
		const told: (readonly string[] | null)[] = [];
		asked.mockResolvedValue(strength({ starters: 0 } as Partial<Strength>));
		host = document.createElement('div');
		document.body.append(host);
		mount(RecognitionStrength, {
			target: host,
			props: { personId: 'p1', onstarters: (boxes) => told.push(boxes) }
		});
		await vi.waitFor(() => {
			flushSync();
			if (asked.mock.calls.length === 0) throw new Error('not asked yet');
		});
		await Promise.resolve();
		flushSync();

		expect(told.every((boxes) => boxes === null)).toBe(true);
	});

	it('are said as one plain line naming where they came from', () => {
		expect(startersSay(['StashDB'])).toBe('Found with the starter pictures from StashDB.');
		expect(startersSay(['StashDB', 'FansDB'])).toBe(
			'Found with the starter pictures from StashDB and FansDB.'
		);
		expect(startersSay([])).toBe('Found with the starter pictures from a stash-box.');
	});
});

describe('the person page', () => {
	it('hands what the bar is told about starters to the row of faces they found', () => {
		const page = readFileSync(resolve('src/routes/people/[id]/+page.svelte'), 'utf8');
		expect(page).toContain('onstarters={(boxes) => (starterBoxes = boxes)}');
		expect(page).toContain('help={starterBoxes ? startersSay(starterBoxes) : undefined}');
	});
});
