/*
 * The interaction layer's values exist, and the one number written down twice agrees with itself.
 *
 * Two different failures, both silent, both here for a reason.
 *
 * A missing custom property is not an error and not a warning: the declaration using it is thrown
 * away and the element inherits instead. A hover rule reaching for a token that does not exist is
 * a surface that simply never changes, on one component, and everything downstream stays green.
 *
 * And the hold threshold is genuinely in two places: the stylesheet needs it to time the cue that
 * shows a hold is being counted, and the gesture needs it to count. Two numbers for one gesture is
 * a cue that finishes before the menu opens or long after it, which reads as a broken animation
 * rather than as the mismatch it is. Nobody would notice for months.
 */

import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { PRESS_HOLD_MS } from '$lib/components/common/tile-gesture.svelte';

const CSS = readFileSync(
	resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app.css'),
	'utf8'
);

function declared(name: string): string | null {
	const match = CSS.match(new RegExp(`^\\s*${name}:\\s*([^;]+);`, 'm'));
	return match ? match[1].trim() : null;
}

describe('the hold threshold', () => {
	it('is the same number in the stylesheet as in the gesture', () => {
		const css = declared('--press-hold');
		expect(css, '--press-hold is not declared in app.css').not.toBeNull();
		expect(
			css,
			`app.css says --press-hold: ${css}, tile-gesture says ${PRESS_HOLD_MS}ms. ` +
				'The cue and the count have to finish together.'
		).toBe(`${PRESS_HOLD_MS}ms`);
	});
});

/*
 * No gate pins a `--page-screens` token against `PAGE_SCREENS` in `lib/grid/justify.ts`: the
 * scrolling body is a `1fr` grid track in `PageFrame` and takes its height from the window, so no
 * rule would read such a token, and a gate on it could only fail when somebody changed the live
 * number and left the ornament behind. `PAGE_SCREENS` is the one place that number is written.
 */

/*
 * A COLLAPSING ROW HAS TO BE ABLE TO REACH ZERO, and the line that lets it is invisible.
 *
 * `grid-template-rows: 0fr -> 1fr` is how Sift opens and closes a band whose height it cannot know
 * in advance: the filter drawer and an entity page's identity band both do it, and it is the one
 * height-like property that interpolates to and from a content size in every current engine.
 *
 * A bare `<flex>` track is `minmax(auto, <flex>)` by definition. So `0fr` on its own gives the row
 * none of the free space and STILL FLOORS IT AT ITS CONTENT: the band never closes, and the
 * transition spends its duration animating a number that changes nothing. Two ways out, and a
 * stylesheet needs exactly one of them: `min-block-size: 0` on the clipped child (which is what
 * makes the track's `auto` minimum resolve to zero), or `minmax(0, 0fr)` on the track itself, which
 * is what the shell's rail row had to use because the item there could not be given a minimum.
 *
 * This exists because the fault SURVIVES ITS MUTATION. Deleting `min-block-size: 0` from the entity
 * band leaves all of that component's tests green: jsdom computes no layout, so nothing in the
 * unit suite can see a row that fails to collapse. A text check is blunt and it is the only kind
 * available.
 */
describe('a row that collapses to nothing', () => {
	const COMPONENTS = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

	function everySvelteFile(dir: string): string[] {
		const found: string[] = [];
		for (const entry of readdirSync(dir, { withFileTypes: true })) {
			if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
			const full = join(dir, entry.name);
			if (entry.isDirectory()) found.push(...everySvelteFile(full));
			else if (entry.name.endsWith('.svelte')) found.push(full);
		}
		return found;
	}

	const COLLAPSING = /grid-template-rows:\s*0fr\s*;/;

	/*
	 * Every declaration block in a file, as text.
	 *
	 * !! ASKED PER BLOCK: a per-file version passes the mutation it is written for.
	 * `min-block-size: 0` is a legitimate declaration for a second, unrelated reason (the entity
	 * band's record panel needs it so that a bounded row does not lay out at its content height),
	 * so a file can contain one and still have a collapsing row that cannot collapse. The pair has
	 * to be found in ONE rule: a box that both clips and may shrink is the clipped child; a box
	 * that only shrinks is something else.
	 */
	function blocks(source: string): string[] {
		return [...source.matchAll(/\{([^{}]*)\}/g)].map((match) => match[1]);
	}

	function clipsAndMayShrink(source: string): boolean {
		return blocks(source).some(
			(block) =>
				/overflow(-block)?:\s*hidden\s*;/.test(block) && /min-block-size:\s*0(px)?\s*;/.test(block)
		);
	}

	it('says how, in the rule that does the clipping', () => {
		const guilty: string[] = [];
		for (const path of everySvelteFile(COMPONENTS)) {
			const source = readFileSync(path, 'utf8');
			// A bare `0fr` track. `minmax(0, 0fr)` carries its own answer and is not the case here.
			if (!COLLAPSING.test(source)) continue;
			if (clipsAndMayShrink(source)) continue;
			guilty.push(relative(COMPONENTS, path));
		}
		expect(
			guilty,
			'These declare `grid-template-rows: 0fr` and no rule in them both clips and may shrink.\n' +
				'A bare fr track is `minmax(auto, fr)`, so it is floored at its content and the band\n' +
				'never closes. The clipped child needs `overflow: hidden` AND `min-block-size: 0` in\n' +
				'the same rule, or the track needs `minmax(0, 0fr)`.\n  ' +
				guilty.join('\n  ')
		).toEqual([]);
	});

	it('is reading a real list, not an empty room', () => {
		// A sweep that matches nothing passes for ever. Two surfaces collapse this way today.
		const collapsing = everySvelteFile(COMPONENTS).filter((path) =>
			COLLAPSING.test(readFileSync(path, 'utf8'))
		);
		expect(
			collapsing.length,
			'nothing declares a collapsing row. Has the pattern moved?'
		).toBeGreaterThan(1);
	});
});

describe('the interaction layer', () => {
	/*
	 * Named one at a time rather than scraped, because the point of the list is that somebody
	 * deleting a token has to come here and say so. A test that discovers whatever happens to be
	 * declared would pass just as happily after the deletion.
	 */
	const REQUIRED = [
		// The registers. The light one is the state layer (`--layer-hover`), mixed into each
		// control's own ground, so it needs no surface of its own here.
		'--layer-hover',
		'--hover-ink',
		'--press-scale',
		'--lift-y',
		'--lift-y-sm',
		'--lift-scale',
		'--disabled-opacity',
		// Controls.
		'--control-height',
		'--control-height-sm',
		// Chip geometry.
		'--chip-height-sm',
		'--chip-height',
		'--chip-height-lg',
		'--chip-pad',
		'--chip-pad-lg',
		'--chip-gap',
		'--chip-icon',
		// The page frame.
		'--page-pad',
		'--page-gap',
		'--page-footer-height'
	];

	for (const token of REQUIRED) {
		it(`declares ${token}`, () => {
			expect(
				declared(token),
				`${token} is used by the contract and missing from app.css`
			).not.toBeNull();
		});
	}

	it('expresses every colour it carries in terms of a semantic, never a primitive', () => {
		// The layering rule, applied to the tokens this file added. A hover surface defined as a raw
		// primitive would be a hover state that does not follow a theme.
		for (const token of ['--hover-ink']) {
			const value = declared(token);
			expect(value, `${token} is missing`).not.toBeNull();
			expect(value, `${token} reads a primitive; it must read a --sift-* semantic`).not.toMatch(
				/var\(--p-/
			);
			expect(value, `${token} should be defined as a --sift-* token`).toMatch(/var\(--sift-/);
		}
	});
});

/*
 * The fill red never carries words.
 *
 * `--sift-bad` and `--sift-bad-text` exist as two tokens because no single lightness satisfies both
 * jobs: the fill has to hold white text at 4.5:1, which holds it down, and the word "failed" on a
 * dark tint has to be legible, which holds it up. The fill measures about 3.8:1 on its own tint:
 * fine for a 1px border, which answers to the 3:1 bar every UI element does, and under the floor
 * for anything a person reads.
 *
 * The colour gate cannot see this. It reads `app.css`, where both tokens are declared correctly;
 * the mistake happens in a component, one `color:` at a time: on the shared button, it would draw
 * every quiet destructive action's label in the wrong red.
 *
 * `border-color` is deliberately left alone. A line is not a word.
 */
describe('the fill red', () => {
	const COMPONENTS = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

	function everySvelteFile(dir: string): string[] {
		const found: string[] = [];
		for (const entry of readdirSync(dir, { withFileTypes: true })) {
			if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
			const full = join(dir, entry.name);
			if (entry.isDirectory()) found.push(...everySvelteFile(full));
			else if (entry.name.endsWith('.svelte')) found.push(full);
		}
		return found;
	}

	it('is never a text colour', () => {
		const guilty: string[] = [];
		for (const path of everySvelteFile(COMPONENTS)) {
			const source = readFileSync(path, 'utf8');
			// `color:` but not `border-color:`, `outline-color:`, `caret-color:` and friends.
			for (const line of source.split('\n')) {
				if (/(^|[^-\w])color:\s*var\(--sift-bad\)/.test(line)) {
					guilty.push(`${relative(COMPONENTS, path)}: ${line.trim()}`);
				}
			}
		}
		expect(
			guilty,
			'--sift-bad is the FILL, and it does not clear the contrast floor for words on its own\n' +
				'tint. Use --sift-bad-text for anything a person reads.\n  ' +
				guilty.join('\n  ')
		).toEqual([]);
	});
});
