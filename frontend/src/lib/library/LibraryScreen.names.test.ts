/* A library folder's name and path at a phone's width. */
import { expect, it } from 'vitest';

import source from './LibraryScreen.svelte?raw';

const style = source.slice(source.indexOf('<style>'));

it("ends a folder's name and its path in an ellipsis rather than cutting a letter", () => {
	expect(source).toMatch(/<span class="name-words">\{root\.name\}<\/span>/);
	const rule = style.slice(style.indexOf('.name-words,\n\t.kind {'));
	expect(rule).toMatch(
		/^[^}]*overflow: hidden;[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;/
	);
	expect(style).toMatch(/\.name \{[^}]*min-inline-size: 0;/);
});
