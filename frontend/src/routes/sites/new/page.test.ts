/* The page's heading is its last crumb, and Site is a proper word on screen wherever it names the
   thing: "New site" read as a site in general. */
import { expect, it } from 'vitest';

import source from './+page.svelte?raw';

it('names what it makes a Site', () => {
	expect(source).toContain("{ label: 'New Site' }");
	expect(source).not.toContain("'New site'");
});

it("says Site on a Site's own page where it names the thing", async () => {
	const page = (await import('../[id]/+page.svelte?raw')).default;
	expect(page).toMatch(
		/empty=\{emptyWallSays\([^)]*'Nothing has come from this Site yet\.'\s*\)\}/
	);
});
