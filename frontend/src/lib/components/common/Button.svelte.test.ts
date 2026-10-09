import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/* A glyph inside the button's words is centred against them. A source test, like pills.test.ts:
 * jsdom lays nothing out; e2e/page-alignment.spec.ts is the rendered half. */

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

		/* With the semicolon, so a longer value starting the same cannot pass (pills.test.ts). */
		expect(rule, 'the label is not a flex row').toContain('display: inline-flex;');
		expect(rule, 'the glyph is not centred against the words').toContain('align-items: center;');
	});

	it('is spaced from the words by the button own gap, not by the whitespace around it', () => {
		/* A flex row drops the space between mark and word, so a gap is declared, as `.btn`'s. */
		expect(ruleFor(SELECTOR)).toContain('gap: var(--space-2);');
		expect(ruleFor('.btn')).toContain('gap: var(--space-2);');
	});

	it('leaves a label of plain words alone', () => {
		/* Scoped with :has, or a sentence with an emphasis would gain a gap. */
		expect(SOURCE).toContain('\n\t.label {\n\t\tdisplay: inline-block;\n\t}');
	});
});

/* A link's underline does not reach into an inline-block label. */
describe('a link', () => {
	it('keeps its words inline, so its underline is drawn under them', () => {
		const rule = ruleFor('.link > .label:not(:has(> :global(.icon)))');
		expect(rule).toContain('display: inline;');
		expect(ruleFor('.link')).toContain('text-decoration: underline;');
	});
});

/* A strip's end arrows stand its whole height; only `tall` gives up the square. */
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
