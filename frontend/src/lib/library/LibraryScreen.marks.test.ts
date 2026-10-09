/* A library row wears the red mark its top folder wears in the tree and in Browse. */
import { expect, it } from 'vitest';

import source from './LibraryScreen.svelte?raw';

const markup = source.slice(source.indexOf('</script>'));

it("draws a library row's Don't swap or Don't enrich mark from its top folder", () => {
	const row = markup.slice(markup.indexOf('<span class="name-words">{root.name}</span>'));
	const mark = row.slice(
		row.indexOf('<SharingMark'),
		row.indexOf('/>', row.indexOf('<SharingMark'))
	);
	expect(mark).toMatch(
		/refused=\{topFolders\.has\(root\.id\)\s*\?\s*refusedOf\(topFolders\.get\(root\.id\)!\)/
	);
});
