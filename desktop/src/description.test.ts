/* The words Windows shows beside Sift. */
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
