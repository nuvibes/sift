import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/*
 * A glyph inside the button's words is centred against them.
 *
 * There are two legitimate ways to put an icon on this button. `icon={...}` makes the glyph a
 * sibling of the label in the button's own flex row. Writing `<Icon />` before the words inside the
 * children puts it inside the label span, which is how many call sites write an icon whose name or
 * `filled` depends on the caller; there it must be centred against the text rather than sitting on
 * its baseline.
 *
 * A source test rather than a rendered one, like `pills.test.ts`: the failure is a missing
 * declaration, and jsdom lays nothing out, so a mounted button reports the same geometry with the
 * rule and without it. The rendered half is `e2e/page-alignment.spec.ts`, which photographs
 * controls and counts the rows holding ink.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(join(HERE, 'Button.svelte'), 'utf8');

/** The rule that catches a glyph handed in as children, by its selector. */
const SELECTOR = '.label:has(> :global(.icon))';

function ruleFor(selector: string): string {
	const start = SOURCE.indexOf(`\n\t${selector} {`);
	expect(start, `no "${selector}" rule to read`).toBeGreaterThan(-1);
	const end = SOURCE.indexOf('\n\t}', start);
	return SOURCE.slice(start, end);
}

describe('a glyph handed in as children', () => {
	it('is laid out in a row and centred, rather than sitting on the text baseline', () => {
		const rule = ruleFor(SELECTOR);

		/* Written WITH the semicolon, for the reason `pills.test.ts` gives: `align-items: center;`
		   contains the characters of a check for `align-items: cent`, and a check for the text alone
		   passes against a value it exists to refuse. */
		expect(rule, 'the label is not a flex row').toContain('display: inline-flex;');
		expect(rule, 'the glyph is not centred against the words').toContain('align-items: center;');
	});

	it('is spaced from the words by the button own gap, not by the whitespace around it', () => {
		/*
		 * A whitespace-only text node is not a flex item, so once the label is a flex row the space
		 * between the mark and the word vanishes. A gap is declared here, the same one `.btn` uses,
		 * so a glyph handed in this way sits at the same distance from its word as one through
		 * `icon=`.
		 */
		expect(ruleFor(SELECTOR)).toContain('gap: var(--space-2);');
		expect(ruleFor('.btn')).toContain('gap: var(--space-2);');
	});

	it('leaves a label of plain words alone', () => {
		/* Scoped with `:has`, deliberately. Every label as a flex row would make each run of inline
		   content its own flex item, so a sentence with an emphasis in it would gain a gap in the
		   middle of itself. */
		expect(SOURCE).toContain('\n\t.label {\n\t\tdisplay: inline-block;\n\t}');
	});
});

/* The link tone's underline is its mark of being pressable, and a decoration does not reach into an
   inline-block box: were the label one, no link button anywhere would draw its underline. */
describe('a link', () => {
	it('keeps its words inline, so its underline is drawn under them', () => {
		const rule = ruleFor('.link > .label:not(:has(> :global(.icon)))');
		expect(rule).toContain('display: inline;');
		expect(ruleFor('.link')).toContain('text-decoration: underline;');
	});
});

/* A strip's end arrows stand the strip's whole height: square, they would be a small box beside a row
   three times their height. The square gives way only on `tall`, never on an ordinary glyph button. */
describe('a tall glyph button', () => {
	it('takes the height of what holds it, and keeps the square width', () => {
		const rule = ruleFor('.btn.icon-only.tall');
		expect(rule).toContain('block-size: auto;');
		expect(rule).toContain('align-self: stretch;');
		expect(rule).toContain('aspect-ratio: auto;');
		expect(rule).not.toContain('inline-size');
		expect(SOURCE).toContain('class:tall');
	});

	it('is what every strip arrow wears, inside a strip that stretches', () => {
		for (const strip of ['LooksLikeThis', 'FacesInThis', 'SameMusic']) {
			const source = readFileSync(join(HERE, '..', `${strip}.svelte`), 'utf8');
			const start = source.indexOf('{#snippet arrow(');
			const arrow = source.slice(start, source.indexOf('{/snippet}', start));
			expect(arrow, strip).toMatch(/\n\t\t\ttall\n/);
			expect(source, strip).toMatch(/\.strip \{[^}]*align-items: stretch;/);
		}
	});
});
