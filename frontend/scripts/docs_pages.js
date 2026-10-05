// Writes the docs site's pages and screens into src/lib/generated/docs as plain-text data for
// Settings > Documentation. A page, link or menu entry it cannot place stops the build.

import { copyFileSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { DocRefused } from '../src/lib/docs/read.ts';
import { moduleOf, readSite } from '../src/lib/docs/site.ts';

const here = dirname(fileURLToPath(import.meta.url));
const OUT = join(here, '..', 'src', 'lib', 'generated', 'docs');

try {
	const { book, screens } = readSite(join(here, '..', '..', 'docs-site'));
	rmSync(OUT, { recursive: true, force: true });
	mkdirSync(join(OUT, 'screens'), { recursive: true });
	const names = [...screens.keys()].sort();
	for (const name of names)
		copyFileSync(/** @type {string} */ (screens.get(name)), join(OUT, 'screens', name));
	writeFileSync(join(OUT, 'pages.ts'), moduleOf(book, names));
	console.log(`docs: ${Object.keys(book.pages).length} pages and ${names.length} screens written`);
} catch (error) {
	if (!(error instanceof DocRefused)) throw error;
	console.error(`docs: ${error.message}`);
	process.exit(1);
}
