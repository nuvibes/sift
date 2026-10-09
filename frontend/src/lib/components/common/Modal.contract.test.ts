/* One component owns the dialog primitives: the library's dialog parts are imported in
 * `Modal` alone. Static: it reads the source, proving one door, not how it is drawn. */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** All of `src`, resolved from this file, since the runner starts from more than one place. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

/** The one file allowed to import them, as a path from `src`. */
const THE_DOOR = 'lib/components/common/Modal.svelte';

/** Both primitives a modal sheet is built from. */
const PRIMITIVES = ['Dialog', 'AlertDialog'];

function everySourceFile(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules') continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...everySourceFile(path));
		else if (entry.endsWith('.svelte') || entry.endsWith('.ts')) found.push(path);
	}
	return found;
}

/** What a file takes out of the component library, by name. */
function importsFromLibrary(source: string): string[] {
	const taken: string[] = [];
	for (const match of source.matchAll(/import\s+\{([^}]*)\}\s+from\s+['"]bits-ui['"]/g)) {
		for (const name of match[1].split(',')) {
			const bare = name
				.replace(/\btype\b/, '')
				.trim()
				.split(/\s+as\s+/)[0];
			if (bare) taken.push(bare);
		}
	}
	return taken;
}

describe('the dialog primitives have one importer', () => {
	const files = everySourceFile(SOURCE).map((path) => ({
		where: relative(SOURCE, path).split('\\').join('/'),
		source: readFileSync(path, 'utf8')
	}));

	it('surveys the interface rather than an empty tree', () => {
		// Or the check passes by finding nothing.
		expect(files.length).toBeGreaterThan(100);
		expect(files.some((file) => file.where === THE_DOOR)).toBe(true);
	});

	it('is imported by Modal and by nothing else', () => {
		const reaching = files
			.filter((file) => file.where !== THE_DOOR)
			.filter((file) => importsFromLibrary(file.source).some((name) => PRIMITIVES.includes(name)))
			.map((file) => file.where);

		expect(reaching).toEqual([]);
	});

	it('is imported by Modal, so the rule above is not vacuous', () => {
		// The other way round, so a missing door is caught too.
		const door = files.find((file) => file.where === THE_DOOR);
		expect(importsFromLibrary(door?.source ?? '')).toEqual(expect.arrayContaining(PRIMITIVES));
	});
});
