/* An empty Browse says what the person reading it can do about it. */
import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const source = readFileSync('src/routes/browse/+page.svelte', 'utf8');

it('tells a guest nothing has been shared with them, and only an admin to add files', () => {
	expect(source).toMatch(
		/:\s*session\.isAdmin\s*\?\s*'Nothing here yet\. Add some files and they will appear\.'\s*:\s*'Nothing has been shared with you yet\.'/
	);
});
