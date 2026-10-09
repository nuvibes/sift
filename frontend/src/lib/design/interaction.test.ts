/* The interaction layer's values exist, and the one number written down twice agrees with
 * itself. */

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

/* No gate pins a `--page-screens` token against `PAGE_SCREENS` in `lib/grid/justify.ts`: the
 * scrolling body is a `1fr` grid track in `PageFrame` and takes its height from the window, so
 * no rule would read such a token, and a gate on it could only fail when somebody changed the
 * live number and left the ornament behind. */

/* A COLLAPSING ROW HAS TO BE ABLE TO REACH ZERO, and the line that lets it is invisible. */
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

	/* Every declaration block in a file, as text. !! */
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
	/* Named one at a time rather than scraped, because the point of the list is that somebody
	 * deleting a token has to come here and say so. */
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
		// The layering rule, applied to the tokens this file added.
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

/* The fill red never carries words. */
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
