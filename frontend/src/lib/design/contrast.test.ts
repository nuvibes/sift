/**
 * Every colour pair the interface actually draws, measured, in every theme it can be drawn in.
 *
 * A contrast figure in a comment next to the value it describes is true when written and silently
 * false the moment anybody changes the value, moves the token to another surface, or adds a second
 * base for it to be measured against. So the rule is arithmetic instead of intention: this reads
 * `app.css`, resolves every custom property for each of the base-by-accent combinations
 * exactly as the cascade would, and refuses a pair that falls under its floor. The floors are the
 * design system's: body text 4.5:1, elements and meaningful graphics 3:1.
 *
 * WHY A DECLARED LIST OF PAIRS rather than every token against every other. Because the cross
 * product is not the truth: the app does not put the caption ink on a chip, and asserting that it
 * could would force the palette to satisfy a combination nobody draws. The list below is the set of
 * pairs the components genuinely produce (extracted by reading every rule block that sets both a
 * background and a colour) plus the ones the token layer itself composes. A pair that stops being
 * drawn should come off this list; a new one has to go on it, and that is the point.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { composite, contrastRatio, parseColour, type Rgba } from './colour';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP_CSS = resolve(HERE, '..', '..', 'app.css');

/** The bases and accents the app offers. Kept here as the gate's own idea of the truth: if a theme
 *  is added to the stylesheet and not to this list, the new one simply goes unmeasured, so the
 *  last test in this file checks the two lists against each other. */
const BASES = ['obsidian', 'midnight', 'graphite', 'chrome'] as const;
const ACCENTS = ['blue', 'magenta', 'red', 'gold', 'green', 'cyan'] as const;

// --- reading the stylesheet ---------------------------------------------------------------------

type Block = { selector: string; declarations: Map<string, string> };

/** The block that puts the card's and the page's light out for forced colours and for less
 *  transparency. Its selectors are the theme's own, so read as a plain block it would replace the
 *  light with the flat tones in every theme this gate measures; the flat tones are measured
 *  already, as the surfaces they are, and `the light on a card` below checks the block is there. */
const FLAT_FALLBACK =
	/@media[^{]*(?:forced-colors|prefers-reduced-transparency)[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g;

/** Every rule block in the file, in source order, with its custom properties. */
function blocks(): Block[] {
	const css = readFileSync(APP_CSS, 'utf8')
		.replace(/\/\*[\s\S]*?\*\//g, ' ')
		.replace(FLAT_FALLBACK, ' ');
	const found: Block[] = [];
	for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
		const declarations = new Map<string, string>();
		for (const declaration of match[2].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
			declarations.set(declaration[1], declaration[2].trim());
		}
		if (declarations.size > 0) found.push({ selector: match[1].trim(), declarations });
	}
	return found;
}

/** The custom properties in force for one base and one accent.
 *
 * Applied in source order, which is the cascade here: every selector involved is a single pseudo-
 * class or one or two attributes, so the later block wins among equals and the two-attribute block
 * outranks the one-attribute block it follows anyway. Selectors for another theme are skipped.
 */
function variablesFor(base: string, accent: string): Map<string, string> {
	// A grouped selector matches if ANY of its branches does, the same as in a browser. And the
	// defaults are written as `:root, [data-base='midnight']` so the appearance pane can paint a
	// swatch in a theme the page is not wearing. Reading only the whole string would make that block
	// look like it belonged to one base.
	const matches = (branch: string): boolean => {
		const one = branch.trim();
		if (one === ':root') return true;
		const bases = [...one.matchAll(/\[data-base='([^']+)'\]/g)].map((m) => m[1]);
		const accents = [...one.matchAll(/\[data-accent='([^']+)'\]/g)].map((m) => m[1]);
		if (bases.length === 0 && accents.length === 0) return false;
		return bases.every((b) => b === base) && accents.every((a) => a === accent);
	};
	const wanted = (selector: string): boolean => selector.split(',').some(matches);

	const merged = new Map<string, string>();
	for (const block of blocks()) {
		if (!wanted(block.selector)) continue;
		for (const [name, value] of block.declarations) merged.set(name, value);
	}
	return merged;
}

/** Resolve a value to a literal colour, following `var()` as far as it goes.
 *
 * Substitution rather than evaluation: `rgba(var(--p-veil), 0.6)` becomes `rgba(6, 7, 10, 0.6)`
 * first and is parsed second, which is what the browser does and is why the veil can be one token
 * three scrims are mixed from. */
function resolve_(name: string, variables: Map<string, string>): string {
	let value = variables.get(name);
	if (value === undefined) throw new Error(`no such token: ${name}`);
	for (let round = 0; round < 12 && value.includes('var('); round++) {
		value = value.replace(/var\(\s*(--[\w-]+)\s*\)/g, (_, inner: string) => {
			const next = variables.get(inner);
			if (next === undefined) throw new Error(`${name} reads ${inner}, which nothing defines`);
			return next;
		});
	}
	return value;
}

const colourOf = (name: string, variables: Map<string, string>): Rgba =>
	parseColour(resolve_(name, variables));

// --- the pairs ------------------------------------------------------------------------------------

type Pair = {
	front: string;
	back: string;
	floor: number;
	why: string;
	/** An opaque colour to composite a translucent `back` over, for the scrims that sit on media. */
	under?: string;
};

const TEXT = 4.5;
const ELEMENT = 3;

/** The label a filled button carries, and the brightest thing that can be behind a scrim.
 *
 * Both are the same colour and both are read from the token layer rather than written here. A
 * literal would be a second copy of a value the stylesheet already holds: the exact thing the
 * colour gate refuses in a component, and no more defensible in the gate that enforces it. */
const LABEL_ON_A_FILL = '--primary-foreground';
const BRIGHTEST_FRAME = '--primary-foreground';

const PAIRS: Pair[] = [
	// Primary ink, which goes everywhere.
	{ front: '--sift-ink', back: '--sift-bg', floor: TEXT, why: 'body text on the canvas' },
	{ front: '--sift-ink', back: '--sift-surface-1', floor: TEXT, why: 'text on the rail' },
	{ front: '--sift-ink', back: '--sift-surface-2', floor: TEXT, why: 'text on a card or sheet' },
	{ front: '--sift-ink', back: '--sift-surface-3', floor: TEXT, why: 'text in an input' },
	{ front: '--sift-ink', back: '--sift-surface-4', floor: TEXT, why: 'text on a chip' },
	{ front: '--sift-ink', back: '--sift-accent-bg', floor: TEXT, why: 'text on a selected row' },

	// Secondary ink. Used as far up as the chip surface.
	{ front: '--sift-ink-2', back: '--sift-bg', floor: TEXT, why: 'secondary text on the canvas' },
	{ front: '--sift-ink-2', back: '--sift-surface-2', floor: TEXT, why: 'secondary text on a card' },
	{
		front: '--sift-ink-2',
		back: '--sift-surface-3',
		floor: TEXT,
		why: 'secondary text in an input'
	},
	{ front: '--sift-ink-2', back: '--sift-surface-4', floor: TEXT, why: 'a badge label on a chip' },
	{
		front: '--sift-ink-2',
		back: '--sift-accent-bg',
		floor: TEXT,
		why: 'secondary text on a selected row'
	},

	// Caption ink. Deliberately NOT allowed as far as the chip surface: a badge there wears the
	// secondary ink. If it appears there again, put the pair back and the palette will refuse it.
	{ front: '--sift-ink-3', back: '--sift-bg', floor: TEXT, why: 'a caption on the canvas' },
	{ front: '--sift-ink-3', back: '--sift-surface-2', floor: TEXT, why: 'a caption on a card' },
	{
		front: '--sift-ink-3',
		back: '--sift-surface-3',
		floor: TEXT,
		why: 'placeholder text in an input'
	},

	// The accent, in each of its roles.
	{
		front: LABEL_ON_A_FILL,
		back: '--sift-accent',
		floor: TEXT,
		why: 'the label on a primary button'
	},
	{
		front: '--sift-accent',
		back: '--sift-bg',
		floor: ELEMENT,
		why: 'a selection ring on the canvas'
	},
	/* The PMV-creator mark is drawn in the ink it inherits, so the ink beside it is already held
	   to the text floor by the rows above: rows naming a token nothing reads would be a check
	   that can never fail. */
	{
		front: '--sift-accent-hover',
		back: '--sift-bg',
		floor: ELEMENT,
		why: 'the same, under the pointer'
	},
	{ front: '--sift-accent-text', back: '--sift-bg', floor: TEXT, why: 'accent text on the canvas' },
	{
		front: '--sift-accent-text',
		back: '--sift-surface-1',
		floor: TEXT,
		why: 'accent text on the rail'
	},
	{
		front: '--sift-accent-text',
		back: '--sift-surface-2',
		floor: TEXT,
		why: 'accent text on a card'
	},
	{
		front: '--sift-accent-text',
		back: '--sift-surface-3',
		floor: TEXT,
		why: 'accent text in an input'
	},
	{
		front: '--sift-accent-text',
		back: '--sift-accent-bg',
		floor: TEXT,
		why: 'a selected filter token'
	},
	/* The focus ring's own line, which has to be findable on every surface it can land on. It has a
	   role of its own rather than borrowing the text one: the text role is solved for 9:1 on the
	   canvas, which at these hues is a pastel, and as a hairline against an input it would read as
	   white instead of as the accent. This one is the most saturated version that still clears the floor. */
	{
		front: '--sift-accent-ring-line',
		back: '--sift-surface-4',
		floor: ELEMENT,
		why: 'the focus ring on a chip'
	},
	{
		front: '--sift-accent-ring-line',
		back: '--sift-surface-3',
		floor: ELEMENT,
		why: 'the focus ring on an input'
	},
	{
		front: '--sift-accent-ring-line',
		back: '--sift-surface-2',
		floor: ELEMENT,
		why: 'the focus ring on a card'
	},
	{
		front: '--sift-accent-ring-line',
		back: '--sift-bg',
		floor: ELEMENT,
		why: 'the focus ring on the canvas'
	},

	// The four states, each as a word on its own tint and as a word on a card. "In progress" has a
	// colour of its own rather than the accent.
	{
		front: '--sift-progress',
		back: '--sift-progress-bg',
		floor: TEXT,
		why: '"in progress" on its tint'
	},
	{
		front: '--sift-progress',
		back: '--sift-surface-2',
		floor: TEXT,
		why: '"in progress" on a card'
	},
	{ front: '--sift-ok', back: '--sift-ok-bg', floor: TEXT, why: '"done" on its tint' },
	{ front: '--sift-ok', back: '--sift-surface-2', floor: TEXT, why: '"done" on a card' },
	{ front: '--sift-warn', back: '--sift-warn-bg', floor: TEXT, why: 'a warning on its tint' },
	{ front: '--sift-warn', back: '--sift-surface-2', floor: TEXT, why: 'a warning on a card' },
	/* `Panel tone="caution"` stands on the warning's tint and carries the app's ORDINARY ink,
	   which is the whole of its design: the box says caution and the words stay words. So the
	   two inks a sentence in one is drawn in are pairs this list holds. */
	{
		front: '--sift-ink',
		back: '--sift-warn-bg',
		floor: TEXT,
		why: 'the sentence in a caution panel'
	},
	{
		front: '--sift-ink-2',
		back: '--sift-warn-bg',
		floor: TEXT,
		why: 'the quieter half of the same sentence'
	},
	{ front: '--sift-bad-text', back: '--sift-bad-bg', floor: TEXT, why: '"failed" on its tint' },
	{
		front: '--sift-bad-text',
		back: '--sift-surface-2',
		floor: TEXT,
		why: 'an error under a field'
	},
	{
		/* NOT `LABEL_ON_A_FILL`. Every other filled control carries white; this one carries the
		   canvas colour, because white on the red the palette was given is 3.29:1. Read from the
		   token the button actually uses, so that changing what it carries changes what is checked:
		   a literal here, or the wrong token, would keep passing while the button went dark. */
		front: '--destructive-foreground',
		back: '--sift-bad',
		floor: TEXT,
		why: 'the label on the delete button'
	},
	{
		front: '--sift-bad-text',
		back: '--sift-surface-3',
		floor: ELEMENT,
		why: 'the border of a field in error'
	},
	{
		front: '--sift-surface-1',
		back: '--sift-warn',
		floor: TEXT,
		why: 'dark text on a warning fill'
	},
	/* The three below come from sweeping the components for a semantic fill that carries a
	   colour, rather than from reading this list, which is the point. The list is
	   hand-written, so it says what somebody remembered: white on the red fill at 3.29:1 and
	   white on the green at 2.09:1 are the kind of pairing it can miss. */
	{
		front: '--sift-bg',
		back: '--sift-ok',
		floor: TEXT,
		why: 'the tick on a filled "learned" mark'
	},
	{
		/* A BORDER on the tint, at the element floor, and the floor is the whole of this
		   entry. No word can ever appear here: `interaction.test.ts` sweeps every component for
		   `color: var(--sift-bad)` and fails on one, saying in as many words that the fill "does
		   not clear the contrast floor for words on its own tint. Use --sift-bad-text". So
		   holding this pair at the TEXT floor would hold the palette to a floor for a pairing
		   another gate forbids; the two things it names (the excluded checkbox, the quiet
		   danger button on hover) are both `border-color`, checked at the floor a border
		   actually has. */
		front: '--sift-bad',
		back: '--sift-bad-bg',
		floor: ELEMENT,
		why: 'the red border of a checkbox out of range, and of a quiet danger button under the pointer'
	},

	// Judgement and the search wave: glyphs somebody reads as a state.
	{
		front: '--sift-heart',
		back: '--sift-surface-2',
		floor: ELEMENT,
		why: 'a filled heart on a card'
	},
	{
		front: '--sift-star',
		back: '--sift-surface-2',
		floor: ELEMENT,
		why: 'a filled star on a card'
	},
	{
		front: '--sift-folder',
		back: '--sift-surface-2',
		floor: ELEMENT,
		why: 'the folder glyph in the picker'
	},
	{
		front: '--sift-wave-1',
		back: '--sift-surface-3',
		floor: ELEMENT,
		why: 'the search wave, cold end'
	},
	{
		front: '--sift-wave-2',
		back: '--sift-surface-3',
		floor: ELEMENT,
		why: 'the search wave, warm end'
	},
	/* The same two ends again, on a CARD: the marks that say a file was enriched are drawn on a
	   hover card, on an entity header and down a facet column. A pairing checked only on the
	   ground it was first used on is a pairing that stops being measured the moment it moves. */
	{
		front: '--sift-wave-1',
		back: '--sift-surface-2',
		floor: ELEMENT,
		why: 'an enrichment mark on a card, cold end'
	},
	{
		front: '--sift-wave-2',
		back: '--sift-surface-2',
		floor: ELEMENT,
		why: 'an enrichment mark on a card, warm end'
	},
	/* The marks wear the accent and the box colours below, not the wave, and the two pairs
	   above stay, because the wave is still drawn: the spark on the search box is what it is
	   for.

	   EVERY STASH-BOX'S ONE COLOUR, on the chip surface, which is the highest ground
	   any of these marks can be drawn on. Measured rather than assumed, and it is not a
	   formality: StashDB's brown at 1.92:1 would fail it, and it is lifted in `app.css` until
	   it clears it, which is why an outline is not needed. A colour that stops clearing it is
	   either lifted again or the glyph gets one. */
	{
		front: '--sift-box-stashdb',
		back: '--sift-surface-4',
		floor: ELEMENT,
		why: "StashDB's brown on a chip"
	},
	{
		front: '--sift-box-pmvstash',
		back: '--sift-surface-4',
		floor: ELEMENT,
		why: "PMVStash's orange on a chip"
	},
	{
		front: '--sift-box-fansdb',
		back: '--sift-surface-4',
		floor: ELEMENT,
		why: "FansDB's blue on a chip"
	},

	// The scrims, over the worst picture there is. A control over media has no idea what is behind
	// it, so the only honest backdrop to measure against is white: the frame that takes the most
	// contrast away from a light glyph.
	{
		front: '--sift-ink',
		back: '--sift-scrim',
		under: BRIGHTEST_FRAME,
		floor: TEXT,
		why: 'a control over a bright frame'
	},
	{
		front: '--sift-ink',
		back: '--sift-scrim-strong',
		under: BRIGHTEST_FRAME,
		floor: TEXT,
		why: "the player's bar over a bright frame"
	},
	{
		front: '--sift-ink',
		back: '--sift-overlay',
		under: BRIGHTEST_FRAME,
		floor: TEXT,
		why: 'a dialog over a bright page'
	},

	/* An entity page's top band, which stands on its own cover blurred past recognition, so
	 * every word in it is a word over a picture. Same treatment as the scrims above and the same
	 * worst case: white, the frame that takes the most contrast away.
	 *
	 * The thin end of the gradient is what is measured, because it is the only end that can fail:
	 * the other end is the page's ground at full opacity and is already covered by the first rows
	 * of this list. And only the BRIGHT frame is measured, which is not an omission: every
	 * base's ink is light, so a dark picture under the same scrim raises the ratio rather than
	 * lowering it.
	 *
	 * TWO INKS, AND THE CAPTION GREY IS DELIBERATELY NOT ONE OF THEM. Solving the scrim for
	 * `--sift-ink-3` would leave the cover a tenth of its light and the band could not be seen at
	 * all. The frame lifts that token for every row standing on the picture (`PageFrame`'s
	 * `.frame.on-a-picture`), so the grey is not drawn on the scrim anywhere (not by this
	 * file's own rules, not by `Breadcrumbs`, `Tabs`, `RecordView`, `RecordSummary`,
	 * `TagChip`, `PageHeader` or `RecognitionStrength`), and a pair that stops being drawn comes
	 * off this list. The secondary ink is what sets the strength, and it sets it at 0.84.
	 *
	 * THE LIFT IS A RULE IN A COMPONENT AND THIS GATE READS ONLY `app.css`, so nothing here can
	 * see it and nothing here can catch its removal. That is the one soft edge in this block:
	 * delete the line in `PageFrame` and every caption over the picture silently returns to
	 * 3.77:1 on midnight with this file still green. It is why `--band-scrim` carries the same
	 * fact in its own comment, and why the ink is redefined in ONE place rather than component by
	 * component: one line to find is a line somebody can find.
	 */
	{
		front: '--sift-ink',
		back: '--band-scrim',
		under: BRIGHTEST_FRAME,
		floor: TEXT,
		why: "an entity's name over its own blurred cover"
	},
	{
		front: '--sift-ink-2',
		back: '--band-scrim',
		under: BRIGHTEST_FRAME,
		floor: TEXT,
		why: 'the summary beside it and every caption on the band, over the same'
	},
	/* A chart's three series, the accent's own hue at three lightness steps, drawn on the canvas
	   and on a card. Graphics, so the element floor: a bar is found by its shape and read by the
	   words in the table beside it. */
	{ front: '--sift-series-1', back: '--sift-bg', floor: ELEMENT, why: 'the lightest series bar' },
	{ front: '--sift-series-2', back: '--sift-bg', floor: ELEMENT, why: 'the middle series bar' },
	{ front: '--sift-series-3', back: '--sift-bg', floor: ELEMENT, why: 'the deepest series bar' },
	{
		front: '--sift-series-1',
		back: '--sift-surface-2',
		floor: ELEMENT,
		why: 'the lightest series bar on a card'
	},
	{
		front: '--sift-series-2',
		back: '--sift-surface-2',
		floor: ELEMENT,
		why: 'the middle series bar on a card'
	},
	{
		front: '--sift-series-3',
		back: '--sift-surface-2',
		floor: ELEMENT,
		why: 'the deepest series bar on a card'
	}
];

// --- the gate ---------------------------------------------------------------------------------

describe('the contrast of every pair the interface draws', () => {
	for (const base of BASES) {
		for (const accent of ACCENTS) {
			it(`holds up on the ${base} base with the ${accent} accent`, () => {
				const variables = variablesFor(base, accent);
				const failures: string[] = [];

				for (const pair of PAIRS) {
					const front = colourOf(pair.front, variables);
					let back = colourOf(pair.back, variables);
					if (pair.under) back = composite(back, colourOf(pair.under, variables));

					// No tolerance. The tightest pair in any of the twelve combinations clears its
					// floor by 0.070, so a rounding allowance would be slack a gate does not need:
					// slack that lets a real 4.496 through one day and cannot be argued about
					// afterwards.
					const ratio = contrastRatio(composite(front, back), back);
					if (ratio < pair.floor) {
						failures.push(
							`${pair.front} on ${pair.back} is ${ratio.toFixed(2)}:1, under ${pair.floor}:1: ${pair.why}`
						);
					}
				}

				expect(failures, `${base}/${accent}`).toEqual([]);
			});
		}
	}

	it('would notice a colour that stopped being legible', () => {
		// A gate that has only ever passed says nothing about whether it can fail. The primary ink,
		// moved to the canvas it is meant to contrast with, must be caught.
		const variables = variablesFor('midnight', 'blue');
		const canvas = colourOf('--sift-bg', variables);
		expect(contrastRatio(canvas, canvas)).toBeCloseTo(1, 5);
		expect(contrastRatio(canvas, canvas)).toBeLessThan(4.5);
	});

	it('gives every theme the same shape, not just the same names', () => {
		/* A theme changes VALUES. It must never change what a token MEANS, or the app becomes two
		 * apps. And the way that happens is not by renaming anything, it is by a second base simply
		 * FORGETTING one. A primitive a theme does not override silently keeps the default's value,
		 * which is a real colour, so nothing is undefined, nothing warns, and the token gate is happy.
		 * The result is one theme wearing a piece of another, in whichever corner nobody opened.
		 *
		 * So the sets are compared rather than the values. The one deliberate omission is named. */
		const named = new Map<string, Set<string>>();
		for (const block of blocks()) {
			named.set(block.selector.replace(/\s+/g, ' '), new Set(block.declarations.keys()));
		}
		const keysOf = (selector: string): Set<string> => {
			const found = named.get(selector);
			expect(found, `no block for ${selector}`).toBeDefined();
			return found!;
		};

		// The scrims are mixed from the veil and stay near-black in every base: they dim a
		// PHOTOGRAPH rather than the chrome, and what is under them is somebody's picture either way.
		const DELIBERATELY_SHARED = new Set(['--p-veil']);

		/* Every base against the default, not one: a base added later is the one most likely to
		 * forget a primitive, and checking only the first base written after the default would
		 * leave every later one free to wear a piece of midnight. */
		const midnight = keysOf(":root, [data-base='midnight']");
		for (const base of BASES.filter((one) => one !== 'midnight')) {
			const own = keysOf(`[data-base='${base}']`);
			expect(
				[...midnight].filter((name) => !own.has(name) && !DELIBERATELY_SHARED.has(name)),
				`${base} does not redefine these, so it quietly wears the midnight value`
			).toEqual([]);
			expect(
				[...own].filter((name) => !midnight.has(name)),
				`${base} invents a primitive`
			).toEqual([]);
			for (const accent of ACCENTS) {
				expect(
					[...keysOf(`[data-base='${base}'][data-accent='${accent}']`)].sort(),
					`${base} with ${accent} sets the two roles that follow the canvas`
				).toEqual(['--p-accent-bg', '--p-accent-text']);
			}
		}

		const blue = keysOf(":root, [data-accent='blue']");
		for (const accent of ACCENTS.slice(1)) {
			expect(
				[...blue].filter((n) => !keysOf(`[data-accent='${accent}']`).has(n)),
				accent
			).toEqual([]);
		}

		/* The faces are two attributes with one property each, so the shape question splits in
		 * two: every family has to declare the display half on the display attribute and the body
		 * half on the body one. A family that quietly set both on one side would leave the other
		 * attribute doing nothing, which looks exactly like a font choice that did not take.
		 */
		const display = keysOf(":root, [data-face-display='archivo']");
		const body = keysOf(":root, [data-face-body='instrument-sans']");
		for (const face of ['space-grotesk', 'geist-mono', 'manrope', 'jetbrains-mono']) {
			expect(
				[...display].filter((n) => !keysOf(`[data-face-display='${face}']`).has(n)),
				`${face} display`
			).toEqual([]);
		}
		for (const face of ['inter', 'geist', 'public-sans', 'sora', 'dm-sans']) {
			expect(
				[...body].filter((n) => !keysOf(`[data-face-body='${face}']`).has(n)),
				`${face} body`
			).toEqual([]);
		}
	});

	it('paints a running job in its own colour rather than in the brand', () => {
		/* A STATE is not a brand decision: the In progress chip must not follow the accent, or a
		 * library set to gold draws a running job in gold. Every other state on that badge reads
		 * its own semantic name.
		 *
		 * Read out of the stylesheet rather than measured, because what goes wrong is the MAPPING
		 * and not the colour: the accent blue and the progress blue are the same value, so a
		 * contrast reading of the two is identical and would pass throughout.
		 */
		const running = blocks().find((block) => block.selector.includes('.state-running'));
		expect(running, 'no .state-running block').toBeDefined();
		const reads = [...running!.declarations.values()].join(' ');
		expect(reads).toContain('--sift-progress');
		expect(reads).not.toContain('--sift-accent');
	});

	it('measures every theme the stylesheet actually offers', () => {
		// The list at the top of this file is the gate's own idea of what exists. If a base or an
		// accent is added to `app.css` and not here, it would go unmeasured. And a theme nobody
		// measured is exactly the thing this gate was written to make impossible.
		const css = readFileSync(APP_CSS, 'utf8');
		const declaredBases = new Set([...css.matchAll(/\[data-base='([^']+)'\]/g)].map((m) => m[1]));
		const declaredAccents = new Set(
			[...css.matchAll(/\[data-accent='([^']+)'\]/g)].map((m) => m[1])
		);

		// Every base and every accent is named in the file, including the defaults, which are
		// written as `:root, [data-base='midnight']` so a swatch can be painted in them. So the two
		// lists must match exactly: a theme in the stylesheet and not here is a theme nobody measures.
		expect([...declaredBases].sort()).toEqual([...BASES].sort());
		expect([...declaredAccents].sort()).toEqual([...ACCENTS].sort());
	});

	it('keeps the caption grey off the rows standing on a picture, which is what the scrim is solved for', () => {
		/* The one pair above that is true because of a rule in a COMPONENT rather than because of
		 * a value in the stylesheet, so it is the one pair this file could not otherwise notice
		 * losing.
		 *
		 * `--band-scrim` is 0.84: the least that holds `--sift-ink-2` at 4.5:1 over a white
		 * picture. At that strength `--sift-ink-3` reads 3.77:1 on midnight, so a caption drawn
		 * in the grey anywhere over the backdrop is under the floor. Nothing draws it there only
		 * because `PageFrame` redefines the token for the three rows above the body, and deleting
		 * that one line would take every caption on every entity page under the floor with this
		 * gate green.
		 *
		 * READ FROM `PageFrame`, because the picture reaches the breadcrumbs and the tab strip,
		 * and both of those are the frame's furniture drawn in the caption grey.
		 *
		 * Read as text rather than measured, deliberately: what would go wrong is the MAPPING,
		 * and the value it maps to is already measured by the pair above.
		 */
		const frame = readFileSync(
			resolve(HERE, '..', 'components', 'shell', 'PageFrame.svelte'),
			'utf8'
		);
		const lift = frame.match(/\n\t\.frame\.on-a-picture[^{]*\{([^}]*)\}/);
		expect(lift, 'no .on-a-picture block in PageFrame').not.toBeNull();
		expect(lift![1]).toContain('--sift-ink-3: var(--sift-ink-2);');

		// And on every row that stands on it, not merely on one of them: the trail is above the
		// header and the tab strip is below it, and a lift naming only the middle one would leave the
		// other two in the grey.
		const selector = frame.match(/\n(\t\.frame\.on-a-picture[^{]*)\{/)![1];
		for (const row of ['.frame-trail', '.frame-header', '.frame-tools'])
			expect(selector, `the lift does not reach ${row}`).toContain(row);
	});
});

// --- a chart's series stand apart from what a colour MEANS -------------------------------------

/** A colour in OKLab (L, a, b): the space the accents are solved in, where a distance is roughly
 *  a difference the eye sees, whatever the hue. */
function oklab({ r, g, b }: Rgba): [number, number, number] {
	const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
	const [R, G, B] = [lin(r), lin(g), lin(b)];
	const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
	const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
	const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
	return [
		0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
		1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
		0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
	];
}

const apart = (one: Rgba, other: Rgba): number => {
	const [x, y] = [oklab(one), oklab(other)];
	return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
};

/** THE DISTANCE A SERIES KEEPS FROM A MEANING. The failure red's own two values (the fill and the
 *  word) stand 0.057 apart and are one colour to anybody reading them, so a shade closer than
 *  0.08 to any of the four is inside that colour's range: a chart would say failed, done or
 *  wants attention about a bar of viewing. A red accent's deep shade or a green accent's middle
 *  one can land within 0.04 of the failure red's word or the done green. */
/** `color-mix(in oklch, <colour> N%, black | white)` as the browser works it out: the achromatic
 *  side has no hue, so the accent's hue is kept and lightness and chroma move by the share. */
function mixed(value: string, variables: Map<string, string>): Rgba {
	const found = value.match(
		/^color-mix\(\s*in\s+oklch\s*,\s*(.+?)\s+([\d.]+)%\s*,\s*(black|white)\s*\)$/
	);
	if (!found) throw new Error(`not a mix of the accent's run: ${value}`);
	const share = Number(found[2]) / 100;
	const [L, a, b] = oklab(
		parseColour(
			found[1].startsWith('var(') ? resolve_(found[1].slice(4, -1).trim(), variables) : found[1]
		)
	);
	const toward = found[3] === 'white' ? 1 : 0;
	const lightness = share * L + (1 - share) * toward;
	const [A, B] = [share * a, share * b];
	const l = (lightness + 0.3963377774 * A + 0.2158037573 * B) ** 3;
	const m = (lightness - 0.1055613458 * A - 0.0638541728 * B) ** 3;
	const s = (lightness - 0.0894841775 * A - 1.291485548 * B) ** 3;
	const encode = (c: number) => {
		const x = Math.min(1, Math.max(0, c));
		return x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055;
	};
	return {
		r: encode(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
		g: encode(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
		b: encode(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
		a: 1
	};
}

const SERIES_APART = 0.08;
const SERIES = ['--sift-series-1', '--sift-series-2', '--sift-series-3'] as const;
const MEANINGS = ['--sift-bad', '--sift-bad-text', '--sift-ok', '--sift-warn'] as const;

describe("a chart's series never wear a colour that means something", () => {
	for (const base of BASES) {
		for (const accent of ACCENTS) {
			it(`keeps the ${accent} accent's series apart from red, green and amber on ${base}`, () => {
				const variables = variablesFor(base, accent);
				const close: string[] = [];
				for (const series of SERIES) {
					for (const meaning of MEANINGS) {
						const distance = apart(colourOf(series, variables), colourOf(meaning, variables));
						if (distance < SERIES_APART)
							close.push(`${series} is ${distance.toFixed(3)} from ${meaning}`);
					}
				}
				expect(close, `${base}/${accent}`).toEqual([]);
			});
		}
	}

	it("draws the accent's run so the words on it read", () => {
		/* The run is declared as `color-mix(in oklch, var(--sift-accent) N%, black | white)`, which
		 * the pairs above cannot parse, so it is worked out here the way the browser does: in OKLCH
		 * the black and the white carry no hue, so the mix keeps the accent's and moves lightness
		 * and chroma by the share. Then the ink a story card draws on each shade is measured. */
		for (const base of BASES) {
			for (const accent of ACCENTS) {
				const variables = variablesFor(base, accent);
				const run = (name: string): Rgba => mixed(resolve_(name, variables), variables);
				const shades = ['--sift-accent-shade-2', '--sift-accent-shade-1'].map(run);
				const failures: string[] = [];
				for (const [ink, floor] of [
					['--sift-ink', TEXT],
					['--sift-ink-2', TEXT]
				] as const) {
					for (const shade of shades) {
						const ratio = contrastRatio(colourOf(ink, variables), shade);
						if (ratio < floor) failures.push(`${ink} on a shade is ${ratio.toFixed(2)}:1`);
					}
				}
				for (const shade of shades) {
					const ratio = contrastRatio(run('--sift-accent-tint-1'), shade);
					if (ratio < TEXT) failures.push(`tint-1 on a shade is ${ratio.toFixed(2)}:1`);
				}
				const onCard = contrastRatio(
					run('--sift-accent-tint-1'),
					colourOf('--sift-surface-2', variables)
				);
				if (onCard < ELEMENT) failures.push(`tint-1 on a card is ${onCard.toFixed(2)}:1`);
				expect(failures, `${base}/${accent}`).toEqual([]);
			}
		}
	});

	it('would notice a red series inside the failure red', () => {
		// A red shade that reads as the failure red, measured against the word it looks like.
		const variables = variablesFor('midnight', 'red');
		expect(
			apart(parseColour('rgb(236, 55, 68)'), colourOf('--sift-bad-text', variables))
		).toBeLessThan(SERIES_APART);
	});
});

/** The decoration-only ink stays decoration.
 *
 * `--sift-ink-4` is 3:1 on the canvas and less than that on every surface above it. It exists for
 * things that are not read (an unfilled star, a control that cannot be pressed), and the moment
 * it carries a sentence that sentence is under the floor with nothing to say so.
 *
 * Disabled text is the one exception, and it is the standard's, not a convenience: WCAG exempts a
 * control that cannot be operated, because the whole point of how it looks is that it is not
 * available. So the rule is narrow: the ink may be a colour only in a rule about being disabled.
 */
/** Every component in the tree, with its source. Two gates below read it, so it is here rather than
 *  copied into each: the copies are what come to disagree about which directories to skip. */
const componentSources = (): { path: string; text: string }[] => {
	const root = resolve(HERE, '..', '..');
	const found: { path: string; text: string }[] = [];
	const walk = (directory: string): void => {
		for (const entry of readdirSync(directory, { withFileTypes: true })) {
			if (entry.name === 'node_modules' || entry.name === '.svelte-kit') continue;
			const path = join(directory, entry.name);
			if (entry.isDirectory()) walk(path);
			else if (entry.name.endsWith('.svelte'))
				found.push({ path, text: readFileSync(path, 'utf8') });
		}
	};
	walk(root);
	return found;
};

/*
 * THE LIST ABOVE IS HAND-WRITTEN, AND THIS IS WHAT STOPS IT GOING ONE SHORT.
 *
 * Its header says the pairs were "extracted by reading every rule block that sets both a background
 * and a colour": a claim about the whole tree that nothing keeps true. A sweep of the same kind
 * finds what the list misses: white on the destructive fill at 3.29:1 on a share control, white on
 * the "learned" tick at 2.09:1 at 20px, the caption ink on a chip at 3.31:1 on a badge whose whole
 * job is to be read.
 *
 * So this measures rather than cross-references. A pair that is drawn is resolved through the token
 * layer and put against the floor directly, which means there is no second list to keep in step and
 * nothing to remember when a component is written.
 *
 * WHAT IT SKIPS, and why each one is a skip rather than a hole:
 *
 *   - a property the token layer does not define. `--state-ink`, `--hover-surface` and their like
 *     are set by a class on an ancestor, so what they resolve to depends on which class, and the
 *     values they take are already in the list above. Guessing here would be inventing a pair.
 *   - a translucent background. A scrim has to be composited over what is behind it, which a rule
 *     block does not say; the entries above express that with `under` and this cannot infer it.
 *
 * The floor is TEXT for everything, and that is deliberate friction rather than an oversight. A
 * rule setting `color` is colouring something somebody is meant to see; if a genuinely graphic-only
 * pair ever needs the 3:1 floor, that is a decision to make out loud in the list above rather than
 * a default this quietly grants.
 */
describe('every colour pair the components actually draw', () => {
	type Drawn = { front: string; back: string; where: string };

	/** Every rule block that sets both a background and a colour, from the components and from the
	 *  stylesheet, because `app.css` draws some of them itself. */
	const drawn = (): Drawn[] => {
		const sources = [...componentSources(), { path: APP_CSS, text: readFileSync(APP_CSS, 'utf8') }];
		const found = new Map<string, Drawn>();
		for (const { path, text } of sources) {
			for (const block of text
				.replace(/\/\*[\s\S]*?\*\//g, ' ')
				.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
				const back = /(?<![-\w])background(?:-color)?:\s*var\((--[\w-]+)\)\s*;/.exec(block[2]);
				const front = /(?<![-\w])color:\s*var\((--[\w-]+)\)\s*;/.exec(block[2]);
				if (!back || !front) continue;
				const where = `${path.slice(path.indexOf('/src/'))} ${block[1].trim().split('\n').pop()?.trim()}`;
				const key = `${front[1]}|${back[1]}`;
				if (!found.has(key)) found.set(key, { front: front[1], back: back[1], where });
			}
		}
		return [...found.values()];
	};

	it.each(BASES.flatMap((base) => ACCENTS.map((accent) => ({ base, accent }))))(
		'holds up on the $base base with the $accent accent',
		({ base, accent }) => {
			const variables = variablesFor(base, accent);
			const offences: string[] = [];
			for (const { front, back, where } of drawn()) {
				// Set by a class on an ancestor, so it has no one value here. See the note above.
				if (!variables.has(front) || !variables.has(back)) continue;
				// A card's or the page's light is two colours, and the words on it have to read at
				// both ends of it.
				const light = LIGHTS[back];
				for (const behind of light ? ends(light, variables) : [colourOf(back, variables)]) {
					// A scrim needs compositing this cannot infer. See the note above.
					if (behind.a < 1) continue;
					const ratio = contrastRatio(colourOf(front, variables), behind);
					if (ratio < TEXT) {
						offences.push(`${front} on ${back} is ${ratio.toFixed(2)}:1: ${where}`);
					}
				}
			}
			expect(offences, 'a pair the app draws is under the floor for something read').toEqual([]);
		}
	);

	/*
	 * A WASH IS READ AS TRANSLUCENT, AND THEREFORE SKIPPED.
	 *
	 * `--sift-accent-wash` is `color-mix(in srgb, <accent> 26%, transparent)`. A parser that knows
	 * only hexes and `rgb()` throws on anything else, on the principle that a colour it cannot read
	 * must not pass silently, which is right, and without the wash case the first component to
	 * put text on a wash would produce eighteen thrown cases, one per base and accent, rather than
	 * a skip.
	 *
	 * Asserted here rather than left to the sweep above, because the sweep is green either way: a
	 * pair it skips and a pair it never saw look exactly the same from the outside.
	 */
	it('reads a wash as the colour at its own alpha, so the sweep skips it', () => {
		const variables = variablesFor(BASES[0], ACCENTS[0]);
		const wash = colourOf('--sift-accent-wash', variables);
		const solid = colourOf('--sift-accent', variables);

		expect(wash.a, 'a wash the sweep cannot see through is a wash it will try to measure').toBe(
			0.26
		);
		expect([wash.r, wash.g, wash.b]).toEqual([solid.r, solid.g, solid.b]);
	});

	it('still refuses a colour it cannot work out', () => {
		// The other half of the same principle: reading one known form is not licence to guess.
		// Written in `rgb()` rather than as hexes: the token gate refuses a colour literal in the
		// tree, and it is right to: even in a string that exists to be rejected.
		expect(() => parseColour('color-mix(in srgb, rgb(255 255 255) 40%, rgb(0 0 0))')).toThrow(
			/not a colour/
		);
		expect(() => parseColour('hsl(200 50% 50%)')).toThrow(/not a colour/);
	});

	it('finds the pairs at all, so an empty sweep cannot pass as a clean one', () => {
		// A regex that matches nothing is silent, and silence and success are the same result here.
		const pairs = drawn();
		expect(pairs.length).toBeGreaterThan(20);
		expect(pairs.some(({ front }) => front === '--destructive-foreground')).toBe(true);
	});
});

describe('the decoration-only ink', () => {
	it('is never the colour of something a reader has to read', () => {
		const offences: string[] = [];
		for (const { path, text } of componentSources()) {
			for (const block of text
				.replace(/\/\*[\s\S]*?\*\//g, ' ')
				.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
				const usesIt = /(?<![-\w])color:\s*var\(--sift-ink-4\)/.test(block[2]);
				if (!usesIt) continue;
				if (/disabled/i.test(block[1])) continue;
				offences.push(`${path.slice(path.indexOf('/src/'))}  ${block[1].trim().split('\n').pop()}`);
			}
		}
		expect(
			offences,
			'the decoration-only ink is carrying text: it is under 3:1 on every surface above the canvas'
		).toEqual([]);
	});

	it('would notice text painted in it', () => {
		// The same test, run against a rule that certainly does put words in it.
		const planted = '.explains { color: var(--sift-ink-4); }';
		const block = [...planted.matchAll(/([^{}]+)\{([^{}]*)\}/g)][0];
		expect(/(?<![-\w])color:\s*var\(--sift-ink-4\)/.test(block[2])).toBe(true);
		expect(/disabled/i.test(block[1])).toBe(false);
	});
});

// --- the light on a card, and on the page ------------------------------------------------------

/** OKLab to an sRGB colour, the inverse of `oklab` above. */
function fromOklab([L, a, b]: [number, number, number]): Rgba {
	const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
	const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
	const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
	const encode = (c: number) => {
		const x = Math.min(1, Math.max(0, c));
		return x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055;
	};
	return {
		r: encode(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
		g: encode(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
		b: encode(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
		a: 1
	};
}

/** `color-mix(in oklab, <one> N%, <other>)` as the browser works it out, for two opaque colours:
 *  each of the three coordinates moved by the share.
 *
 *  OKLAB AND NOTHING ELSE, and a mix written in OKLCH is refused rather than worked out. Chromium
 *  reads a grey as faint as midnight's card (chroma under about 0.02) as having no hue, so the same
 *  mix in OKLCH comes out with chroma and no hue, which it paints at hue zero: the card would turn
 *  red on screen while arithmetic like this one said it was blue. */
function mixedTwo(value: string): Rgba {
	const found = value.match(/^color-mix\(\s*in\s+oklab\s*,\s*(\S+)\s+([\d.]+)%\s*,\s*(\S+)\s*\)$/);
	if (!found) throw new Error(`not a mix of two colours in oklab: ${value}`);
	const share = Number(found[2]) / 100;
	// Black is the one named colour a light is mixed with: the far end of the page's light.
	const [one, other] = [found[1], found[3]].map((colour): [number, number, number] =>
		colour === 'black' ? [0, 0, 0] : oklab(parseColour(colour))
	);
	return fromOklab(
		[0, 1, 2].map((at) => share * one[at] + (1 - share) * other[at]) as [number, number, number]
	);
}

/** The two ends of a light, read through the gradient that paints it: a gradient naming any other
 *  stop than these two would be painting colours this gate never measured. */
function ends(fill: string, variables: Map<string, string>): [Rgba, Rgba] {
	const declared = variables.get(fill);
	expect(declared, `no ${fill}`).toBeDefined();
	const stops = [...declared!.matchAll(/var\(\s*(--[\w-]+)\s*\)/g)].map((m) => m[1]);
	expect(
		declared!.startsWith('linear-gradient(to bottom right,'),
		`${fill} is lit from the top-left`
	).toBe(true);
	expect(stops, `${fill} runs between two measured ends`).toHaveLength(2);
	return [mixedTwo(resolve_(stops[0], variables)), mixedTwo(resolve_(stops[1], variables))];
}

const lightness = (colour: Rgba) => oklab(colour)[0];

/** The grounds that are a light rather than a colour, each with the gradient whose two ends are
 *  what a word on it is measured against. */
const LIGHTS: Record<string, string> = {
	'--sift-card': '--sift-card-fill',
	'--sift-card-fill': '--sift-card-fill',
	'--sift-page-fill': '--sift-page-fill'
};

describe('the light on a card, and on the page', () => {
	for (const base of BASES) {
		for (const accent of ACCENTS) {
			it(`holds every floor of the card tone at both ends of the card on ${base} with ${accent}`, () => {
				const variables = variablesFor(base, accent);
				const failures: string[] = [];
				const grounds: [string, string, Rgba[]][] = [
					['--sift-surface-2', '--sift-card-fill', ends('--sift-card-fill', variables)],
					['--sift-bg', '--sift-page-fill', ends('--sift-page-fill', variables)]
				];
				for (const [tone, fill, stops] of grounds) {
					for (const pair of PAIRS.filter((one) => one.back === tone && !one.under)) {
						const front = colourOf(pair.front, variables);
						for (const [at, back] of stops.entries()) {
							const ratio = contrastRatio(composite(front, back), back);
							if (ratio < pair.floor) {
								failures.push(
									`${pair.front} on the ${at === 0 ? 'lit' : 'shaded'} end of ${fill} is ${ratio.toFixed(2)}:1, under ${pair.floor}:1: ${pair.why}`
								);
							}
						}
					}
				}
				expect(failures, `${base}/${accent}`).toEqual([]);
			});
		}
	}

	for (const base of BASES) {
		it(`is visible but slight on ${base}, and the page's a third of the card's`, () => {
			const variables = variablesFor(base, 'blue');
			const tone = lightness(colourOf('--sift-surface-2', variables));
			const canvas = lightness(colourOf('--sift-bg', variables));
			const [lit, shade] = ends('--sift-card-fill', variables).map(lightness);
			const [pageLit, pageShade] = ends('--sift-page-fill', variables).map(lightness);

			// Lighter at the top-left and darker at the bottom-right of the card tone, so the middle
			// of a card is still the card tone and the surface steps keep their meaning.
			expect(lit).toBeGreaterThan(tone);
			expect(shade).toBeLessThan(tone);
			// The reference's spread: about 0.034 of OKLCH lightness end to end, seven or eight
			// levels of 255. Under 0.02 nobody sees it; past 0.05 it reads as a sheen.
			expect(lit - shade).toBeGreaterThan(0.02);
			expect(lit - shade).toBeLessThan(0.05);
			// The card's darkest corner still stands off the page.
			expect(shade - canvas).toBeGreaterThan(0.03);
			// The page's light about a third of the card's, either side of the canvas.
			expect(pageLit).toBeGreaterThan(canvas);
			expect(pageShade).toBeLessThan(canvas);
			const third = (pageLit - pageShade) / (lit - shade);
			expect(third).toBeGreaterThan(0.2);
			expect(third).toBeLessThan(0.5);
		});
	}

	it('catches the light along the edge, from the hairline into the shaded end', () => {
		const variables = variablesFor('midnight', 'blue');
		expect(variables.get('--sift-card-edge')).toBe(
			'linear-gradient(to bottom right, var(--sift-line), var(--sift-card-shade))'
		);
		expect(variables.get('--sift-card')).toBe(
			'var(--sift-card-fill) padding-box, var(--sift-card-edge) border-box'
		);
	});

	it('refuses a light mixed in OKLCH, which Chromium paints red on midnight', () => {
		const variables = variablesFor('midnight', 'blue');
		const card = resolve_('--sift-surface-2', variables);
		const canvas = resolve_('--sift-bg', variables);
		expect(() => mixedTwo(`color-mix(in oklch, ${card} 70%, ${canvas})`)).toThrow(/oklab/);
	});

	it('would notice a light too bright for a caption', () => {
		// A gate that has only ever passed says nothing about whether it can fail: the same arithmetic,
		// with the lit end moved all the way to the chip tone, takes the caption grey under its floor.
		const variables = variablesFor('midnight', 'blue');
		const chip = mixedTwo(
			`color-mix(in oklab, ${resolve_('--sift-surface-4', variables)} 100%, ${resolve_('--sift-surface-3', variables)})`
		);
		const caption = colourOf('--sift-ink-3', variables);
		expect(contrastRatio(caption, chip)).toBeLessThan(TEXT);
	});

	it('puts the light out for forced colours and for less transparency', () => {
		const css = readFileSync(APP_CSS, 'utf8').replace(/\/\*[\s\S]*?\*\//g, ' ');
		const fallback = css.match(FLAT_FALLBACK);
		expect(fallback, 'no block putting the light out').not.toBeNull();
		const block = fallback!.join('\n');
		expect(block).toContain('forced-colors: active');
		expect(block).toContain('prefers-reduced-transparency: reduce');
		expect(block).toMatch(
			/--sift-card-fill:\s*linear-gradient\(var\(--sift-surface-2\), var\(--sift-surface-2\)\);/
		);
		expect(block).toMatch(/--sift-page-fill:\s*var\(--sift-bg\);/);
		expect(block).toMatch(
			/--sift-card:\s*var\(--sift-card-fill\) padding-box, var\(--sift-line\);/
		);
	});
});

// --- a card under the pointer ------------------------------------------------------------------

/** The stops a hover or press layer paints, read through its gradient: each a surface step. */
function layerStops(name: string, variables: Map<string, string>): Rgba[] {
	const declared = variables.get(name);
	expect(declared, `no ${name}`).toBeDefined();
	expect(declared!.startsWith('linear-gradient('), `${name} is a picture over the light`).toBe(
		true
	);
	/* The gradient's arguments, split at its own commas (a mix inside one has commas of its own),
	   the direction left out. Each stop is a surface step or a mix of two, worked out in OKLab. */
	const inside = declared!.slice('linear-gradient('.length, -1);
	const parts: string[] = [];
	let depth = 0;
	let from = 0;
	for (let at = 0; at < inside.length; at++) {
		if (inside[at] === '(') depth += 1;
		else if (inside[at] === ')') depth -= 1;
		else if (inside[at] === ',' && depth === 0) {
			parts.push(inside.slice(from, at).trim());
			from = at + 1;
		}
	}
	parts.push(inside.slice(from).trim());
	const stops = parts.filter((part) => !part.startsWith('to '));
	expect(stops.length, `${name} paints measured stops`).toBeGreaterThan(0);
	return stops.map((stop) => {
		const one = stop.match(/^var\(\s*(--[\w-]+)\s*\)$/);
		if (one) return colourOf(one[1], variables);
		const mix = stop.replace(/var\(\s*(--[\w-]+)\s*\)/g, (_, inner: string) =>
			resolve_(inner, variables)
		);
		return mixedTwo(mix);
	});
}

const chroma = (colour: Rgba): number => Math.hypot(oklab(colour)[1], oklab(colour)[2]);

describe('a card under the pointer', () => {
	for (const base of BASES) {
		for (const accent of ACCENTS) {
			it(`lifts the card in ${base}'s own hue and holds the card's floors with ${accent}`, () => {
				const variables = variablesFor(base, accent);
				const [restLit, restShade] = ends('--sift-card-fill', variables);
				const [lit, shade] = layerStops('--sift-card-hover-layer', variables);
				const failures: string[] = [];

				// A lift: each end lighter than the same end at rest, lit from the same corner.
				if (lightness(lit) <= lightness(restLit)) failures.push('the lit end did not lift');
				if (lightness(shade) <= lightness(restShade)) failures.push('the shaded end did not lift');
				if (lightness(lit) <= lightness(shade)) failures.push('the light turned round');
				// The base's own hue, never washed toward a neutral grey: no less colour than at rest.
				if (chroma(lit) < chroma(restLit) - 0.001)
					failures.push(`the lit end lost its hue (${chroma(lit).toFixed(4)})`);
				if (chroma(shade) < chroma(restShade) - 0.001)
					failures.push(`the shaded end lost its hue (${chroma(shade).toFixed(4)})`);

				// Everything a card carries still reads on it, under the pointer and under a press. Save
				// one: a card that opens on a press holds no field, so no field's error stands on it,
				// and the error red is held to the element floor here, as it is on the input step.
				const grounds = [lit, shade, ...layerStops('--sift-card-press-layer', variables)];
				for (const pair of PAIRS.filter((one) => one.back === '--sift-surface-2' && !one.under)) {
					const front = colourOf(pair.front, variables);
					const floor = pair.front === '--sift-bad-text' ? ELEMENT : pair.floor;
					for (const back of grounds) {
						const ratio = contrastRatio(composite(front, back), back);
						if (ratio < floor) failures.push(`${pair.front} at ${ratio.toFixed(2)}:1: ${pair.why}`);
					}
				}

				// A hairline on it (a chip's outline) keeps at least the edge it has at rest.
				const line = colourOf('--sift-line', variables);
				const lifted = colourOf('--sift-card-hover-line', variables);
				for (const [over, rest] of [
					[lit, restLit],
					[shade, restShade]
				]) {
					if (contrastRatio(lifted, over) < contrastRatio(line, rest))
						failures.push(
							`a hairline on the lifted card is ${contrastRatio(lifted, over).toFixed(2)}:1 against ${contrastRatio(line, rest).toFixed(2)}:1 at rest`
						);
				}
				expect(failures, `${base}/${accent}`).toEqual([]);
			});
		}
	}

	it("would notice the ink's state layer, which greys midnight's card and swallows its hairline", () => {
		const variables = variablesFor('midnight', 'blue');
		const [restLit] = ends('--sift-card-fill', variables);
		const ink = { ...colourOf('--sift-ink', variables), a: 0.08 };
		const washed = composite(ink, restLit);
		expect(chroma(washed)).toBeLessThan(chroma(restLit) - 0.001);
		const line = colourOf('--sift-line', variables);
		expect(contrastRatio(line, washed)).toBeLessThan(contrastRatio(line, restLit));
	});

	it('steps the hairlines up wherever a card takes the hover', () => {
		const readers = componentSources().filter(({ text }) =>
			/var\(--sift-card-hover-layer\)/.test(text)
		);
		expect(readers.length, 'nothing reads the hover any more').toBeGreaterThan(0);
		for (const { path, text } of readers) {
			for (const block of text.matchAll(/\{([^{}]*var\(--sift-card-hover-layer\)[^{}]*)\}/g)) {
				expect(block[1], path).toMatch(/--sift-line:\s*var\(--sift-card-hover-line\);/);
			}
		}
	});
});
