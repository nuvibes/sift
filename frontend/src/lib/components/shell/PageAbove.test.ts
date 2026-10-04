// SPDX-License-Identifier: AGPL-3.0-or-later
/* The tab strip on an entity page stands at one height whichever tab is open.
 *
 * Two placements cannot agree on it: identity in the frame's header and the strip in its tools
 * row sit `--page-gap` (12px) apart, a band above the heading puts them 16px apart, so a tab drawn
 * the first way stands 4px higher than its neighbours; a drop target's transparent border round
 * the band moves it 1px lower again.
 *
 * Layout is not computed here, so the rule is pinned at its source: ONE placement draws the band
 * (`PageAbove`, with `--above-gap` under it), every tab of every entity page goes through it with
 * the strip as the heading row, and nothing else writes the box or the gap.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const PAGES = ['people', 'tags', 'sites', 'collections', 'photo-sets', 'songs'] as const;

function read(path: string): string {
	return readFileSync(path, 'utf8');
}

function page(route: string): string {
	return read(`src/routes/${route}/[id]/+page.svelte`);
}

/** Every Svelte file under a folder, by path. */
function svelteFiles(folder: string): string[] {
	return readdirSync(folder, { recursive: true, withFileTypes: true })
		.filter((one) => one.isFile() && one.name.endsWith('.svelte'))
		.map((one) => join(one.parentPath, one.name));
}

describe('the band above an entity page heading row', () => {
	it.each(PAGES)('draws the History tab of %s in the shape every other tab has', (route) => {
		const text = page(route);
		// The strip in the frame's tools row would be the 4px-higher shape.
		expect(text).not.toMatch(/tools=\{tabStrip\}/);
		expect(text).toMatch(
			/<PageAbove>\{@render identity\(\)\}<\/PageAbove>\s*<PageHeader[^>]*beside=\{tabStrip\}/
		);
	});

	it('is written once: no other file draws its own band box or gap', () => {
		const offenders = [...svelteFiles('src/lib'), ...svelteFiles('src/routes')].filter(
			(path) =>
				!path.endsWith('PageAbove.svelte') &&
				!path.includes(`${join('routes', 'design')}`) &&
				/class="above"|\.above\s*\{/.test(read(path))
		);
		expect(offenders).toEqual([]);
	});

	it('puts the one token under it, and the token is defined', () => {
		expect(read('src/lib/components/shell/PageAbove.svelte')).toMatch(
			/margin-block-end:\s*var\(--above-gap\)/
		);
		expect(read('src/app.css')).toMatch(/--above-gap:\s*var\(--space-4\)/);
	});

	it("keeps a Collection's drop edge out of the layout", () => {
		// A border takes a pixel on every side; the drop edge is an outline drawn over the box.
		const style = page('collections').split('<style>')[1] ?? '';
		const drop = (style.match(/\.drop\s*\{[^}]*\}/)?.[0] ?? '').replace(/\/\*[\s\S]*?\*\//g, '');
		expect(drop).not.toMatch(/\bborder\s*:/);
		expect(drop).toMatch(/outline:/);
	});
});
