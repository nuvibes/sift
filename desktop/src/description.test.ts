/* The words Windows shows beside Sift. The installer writes the package description onto the Start
 * menu and desktop shortcuts, where it is the tooltip over the app, and into the uninstall entry as
 * its comment, so it names the app and explains nothing. */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';

const HERE = dirname(fileURLToPath(import.meta.url));

it('describes the app in the words its tooltip shows', () => {
	const manifest = JSON.parse(readFileSync(join(HERE, '..', 'package.json'), 'utf8')) as {
		description?: unknown;
	};
	expect(manifest.description).toBe('The Sift desktop app.');
});
