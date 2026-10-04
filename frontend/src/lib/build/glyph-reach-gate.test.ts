/** What the glyph-reach gate refuses.
 *
 * `scripts/check_glyph_reach.js` holds every component's style rules to
 * `scripts/lib/glyph-reach.js`: a rule that sets a glyph's ink names the glyph by a child
 * combinator, so it cannot tint the glyph of a button inside the element it styles.
 */

import { describe, expect, it } from 'vitest';

import {
	againstKnownGlyphs,
	glyphReaches,
	reachesGlyph,
	unwrapGlobal
} from '../../../scripts/lib/glyph-reach.js';

const ROW = (rule: string, body = 'color: var(--sift-folder);') => ({
	file: 'lib/Row.svelte',
	text: `<ul class="list"><li><span class="icon"></span></li></ul>\n<style>\n\t${rule} {\n\t\t${body}\n\t}\n</style>\n`
});

describe('a rule that colours a glyph', () => {
	it('is refused when it reaches the glyph through a descendant combinator', () => {
		expect(glyphReaches([ROW('.list :global(.icon)')])).toEqual([
			{ file: 'lib/Row.svelte', selector: '.list .icon' }
		]);
	});

	it('passes when it names the glyph by a child combinator', () => {
		expect(glyphReaches([ROW('.list li > :global(.icon)')])).toEqual([]);
	});

	it('is read inside a selector list and inside a media block', () => {
		const text =
			'<ul class="list"></ul>\n<style>\n\t@media (width > 1px) {\n\t\t.a > .icon, .list :global(.icon) {\n\t\t\tfill: red;\n\t\t}\n\t}\n</style>\n';
		expect(glyphReaches([{ file: 'lib/Row.svelte', text }])).toEqual([
			{ file: 'lib/Row.svelte', selector: '.list .icon' }
		]);
	});

	it('is left alone when it sets no ink', () => {
		expect(glyphReaches([ROW('.list :global(.icon)', 'flex: none;')])).toEqual([]);
		expect(glyphReaches([ROW('.list :global(.icon)', 'background-color: red;')])).toEqual([]);
	});

	it('does not take a class that only starts with icon for the glyph', () => {
		expect(reachesGlyph(unwrapGlobal('.row :global(.icon-only)'))).toBe(false);
		expect(reachesGlyph(unwrapGlobal(':global(.icon.filled)'))).toBe(false);
	});
});

describe('the known list', () => {
	it('tolerates a listed reach and names one that no longer occurs', () => {
		const known = new Map([
			['lib/Row.svelte|.list .icon', 'listed'],
			['lib/Gone.svelte|.x .icon', 'gone']
		]);
		const { fresh, stale } = againstKnownGlyphs(glyphReaches([ROW('.list :global(.icon)')]), known);
		expect(fresh).toEqual([]);
		expect(stale).toEqual(['lib/Gone.svelte|.x .icon']);
	});
});
