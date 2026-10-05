/* An entity page hands its header a verb the server keeps for an admin only when an admin is looking. */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const PAGES = ['people', 'tags', 'sites', 'collections', 'photo-sets', 'songs'].map(
	(kind) => `src/routes/${kind}/[id]/+page.svelte`
);

/* Tagging and deleting are admin-only routes on every kind. */
const ADMIN_ONLY = ['ontag', 'onuntag', 'ondelete'];

describe("the entity pages' header verbs", () => {
	for (const page of PAGES)
		it(`${page} offers tagging and deleting to an admin alone`, () => {
			const source = readFileSync(page, 'utf8');
			for (const verb of ADMIN_ONLY)
				for (const handed of source.matchAll(new RegExp(`\\s${verb}=\\{([^\\n]*)\\}\\n`, 'g')))
					expect(handed[1], `${page} ${verb}`).toMatch(/^session\.isAdmin \?/);
		});
});

const WALLS = ['people', 'tags', 'sites', 'collections', 'photo-sets', 'songs'].map(
	(kind) => `src/routes/${kind}/+page.svelte`
);

describe('a link dropped on a card or a sidebar row', () => {
	for (const wall of WALLS)
		it(`${wall} takes it for an admin alone`, () => {
			const taken = [...readFileSync(wall, 'utf8').matchAll(/\sonlink: ([^\n]*)\n/g)];
			expect(taken.length, wall).toBeGreaterThan(0);
			for (const handed of taken) expect(handed[1], wall).toMatch(/^session\.isAdmin\b/);
		});

	it('the sidebar takes it for an admin alone', () => {
		const rail = readFileSync('src/lib/components/shell/Rail.svelte', 'utf8');
		expect(rail).toContain(
			'return session.isAdmin && TAKES_A_LINK.has(item.id) && carriesALink(event);'
		);
	});
});
