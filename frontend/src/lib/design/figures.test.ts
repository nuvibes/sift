/** Every face Sift offers can actually draw a column of numbers that does not move. */

import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import * as fontkit from 'fontkit';
import { describe, expect, it } from 'vitest';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP_CSS = resolve(HERE, '..', '..', 'app.css');
const FONT_DIR = resolve(HERE, '..', '..', '..', 'static', 'fonts');

const GENERATED_CSS = resolve(HERE, '..', 'generated', 'fonts.css');

/** The token that names the Main face's figures alias, read first in a text face's stack. */
const FIGURES_FIRST = /^var\(--font-figures\)\s*,/;

type Offered = { family: string; stack: string; role: 'display' | 'sans' };

/** Every face a `--font-display` or `--font-sans` anywhere in the stylesheet offers, with its
 * stack. */
function offeredFaces(): Offered[] {
	const css = readFileSync(APP_CSS, 'utf8').replace(/\/\*[\s\S]*?\*\//g, ' ');
	const found = new Map<string, Offered>();
	for (const match of css.matchAll(/--font-(display|sans)\s*:\s*([^;]+);/g)) {
		const stack = match[2].trim();
		const family = /'([^']+)'/.exec(stack)?.[1];
		if (!family) throw new Error(`--font-${match[1]}: ${stack} names no family to measure`);
		found.set(`${match[1]} ${family}`, {
			family,
			stack,
			role: match[1] as Offered['role']
		});
	}
	return [...found.values()];
}

/** Every `--font-figures` alias a display face declares, with the display face beside it. */
function figuresAliases(): { display: string; alias: string }[] {
	const css = readFileSync(APP_CSS, 'utf8').replace(/\/\*[\s\S]*?\*\//g, ' ');
	const found: { display: string; alias: string }[] = [];
	for (const block of css.matchAll(/\{([^{}]*)\}/g)) {
		const display = /--font-display\s*:\s*'([^']+)'/.exec(block[1])?.[1];
		const alias = /--font-figures\s*:\s*'([^']+)'/.exec(block[1])?.[1];
		if (display || alias) found.push({ display: display ?? '', alias: alias ?? '' });
	}
	return found;
}

/** The file a family is served from. `Space Grotesk Variable` -> `space-grotesk-latin-wght-...`. */
function fileFor(family: string): string {
	const prefix = family
		.replace(/ Variable$/, '')
		.toLowerCase()
		.replace(/\s+/g, '-');
	const files = readdirSync(FONT_DIR);
	const wanted = `${prefix}-latin-wght-normal.woff2`;
	if (!files.includes(wanted)) {
		throw new Error(
			`${family} is offered by app.css but ${wanted} is not in static/fonts.\n` +
				'Add the package to TEXT_FACES in scripts/prepare_fonts.js. A family the stylesheet ' +
				'names and the build does not serve falls back to the system face, silently.'
		);
	}
	return join(FONT_DIR, wanted);
}

type Verdict = { tabular: boolean; how: string };

/** The third way, for a text face only: a face that cannot hold a column still passes when its
 *  numerals are never drawn from it. A Main face is not offered this way: it IS the figures. */
function judged(face: Offered, measured: Verdict): Verdict {
	if (measured.tabular) return measured;
	if (face.role === 'sans' && FIGURES_FIRST.test(face.stack)) {
		return { tabular: true, how: "its numerals are the Main face's figures" };
	}
	return measured;
}

function figuresOf(family: string): Verdict {
	const font = fontkit.create(readFileSync(fileFor(family))) as fontkit.Font;

	if ((font.availableFeatures ?? []).includes('tnum')) {
		return { tabular: true, how: 'carries the tnum feature' };
	}

	const widths = [...'0123456789'].map(
		(digit) => font.glyphForCodePoint(digit.codePointAt(0)!).advanceWidth
	);
	if (widths.every((width) => width === widths[0])) {
		return { tabular: true, how: 'its digits are already one width' };
	}

	return { tabular: false, how: `digit widths differ: ${[...new Set(widths)].join(', ')}` };
}

describe('the faces Sift offers', () => {
	it('are staged where the build puts them', () => {
		// A gate that read an empty directory would pass, silently, forever. Refuse to be that.
		const files = readdirSync(FONT_DIR).filter((name) => name.endsWith('.woff2'));
		expect(files.length, 'no fonts staged: run `npm run fonts`').toBeGreaterThan(0);
		expect(offeredFaces().length, 'app.css names no font families').toBeGreaterThan(1);
	});

	for (const face of offeredFaces()) {
		it(`${face.family} can hold a column of numbers still`, () => {
			const verdict = judged(face, figuresOf(face.family));
			expect(
				verdict.tabular,
				`${face.family} has no tabular figures (${verdict.how}). ` +
					'`font-variant-numeric: tabular-nums` would do nothing on it and the machine-fact ' +
					'columns would jitter with no error anywhere. Do not offer it, or set its ' +
					"numerals in the Main face's figures by naming `var(--font-figures)` first."
			).toBe(true);
		});
	}

	it('gives every Main face a figures alias of its own digits, from its own file', () => {
		/* The third way above is only as good as the alias. */
		const generated = readFileSync(GENERATED_CSS, 'utf8');
		const faces = new Map<string, { url: string; range: string }>();
		for (const block of generated.split('@font-face').slice(1)) {
			const family = /font-family:\s*'([^']+)'/.exec(block)?.[1];
			const url = /url\(([^)]+)\)/.exec(block)?.[1];
			const range = /unicode-range:\s*([^;]+);/.exec(block)?.[1] ?? '';
			// A face's latin file is the one holding the digits, so it is the one compared.
			if (family && url && (family.endsWith(' Figures') || /-latin-wght-/.test(url))) {
				faces.set(family, { url, range: range.trim() });
			}
		}
		const aliases = figuresAliases();
		expect(aliases.length, 'no display face declares its figures').toBeGreaterThan(1);
		for (const { display, alias } of aliases) {
			expect(display, `${alias} is declared beside no Main face`).not.toBe('');
			expect(alias, `${display} declares no --font-figures`).toBe(
				display.replace(/ Variable$/, ' Figures')
			);
			const declared = faces.get(alias);
			expect(
				declared,
				`${alias} is not in the generated fonts: run \`npm run fonts\``
			).toBeDefined();
			expect(declared!.range, `${alias} covers more than the ten digits`).toBe('U+30-39');
			expect(declared!.url).toBe(faces.get(display)?.url);
		}
	});

	it('still refuses a text face with no figures that names no alias first', () => {
		// The negative of the third way: DM Sans as it ships, measured, and given a stack that does
		// not put the Main face's figures first.
		const measured = figuresOf('DM Sans Variable');
		expect(measured.tabular, 'DM Sans now carries tabular figures: drop its alias').toBe(false);
		for (const stack of [
			"'DM Sans Variable', system-ui, sans-serif",
			"'DM Sans Variable', var(--font-figures), system-ui"
		]) {
			const face: Offered = { family: 'DM Sans Variable', stack, role: 'sans' };
			expect(judged(face, measured).tabular, stack).toBe(false);
		}
		// And a Main face may not pass by an alias: it is the figures.
		const main: Offered = {
			family: 'DM Sans Variable',
			stack: "var(--font-figures), 'DM Sans Variable'",
			role: 'display'
		};
		expect(judged(main, measured).tabular).toBe(false);
	});

	it('would notice a face that could not do it', () => {
		// The check above passing on eight faces says nothing about whether it can fail.
		const icons = fontkit.create(
			readFileSync(join(FONT_DIR, 'material-symbols-rounded-subset.woff2'))
		) as fontkit.Font;
		expect((icons.availableFeatures ?? []).includes('tnum')).toBe(false);
	});
});
