/*
 * An empty Browse says what the person reading it can do about it. An admin adds files; a guest
 * adds nothing and sees what has been shared with them, so "Add some files" would tell a guest to
 * do something Sift gives them no way to do.
 *
 * Read from the source, because mounting Browse is mounting the whole wall (see first-read.test).
 */
import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const source = readFileSync('src/routes/browse/+page.svelte', 'utf8');

it('tells a guest nothing has been shared with them, and only an admin to add files', () => {
	expect(source).toMatch(
		/:\s*session\.isAdmin\s*\?\s*'Nothing here yet\. Add some files and they will appear\.'\s*:\s*'Nothing has been shared with you yet\.'/
	);
});
