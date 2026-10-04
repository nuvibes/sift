/*
 * Nothing declares the surface-step hovers, so nothing can quietly bring them back.
 *
 * Every hover is the state layer over its own ground (`--layer-hover`), which follows a redefined
 * surface by itself: `BarPanel` turns every grey in the filter panel into the scrim while the
 * screen is filled, and the hovers inside it follow with no line of their own. A declaration of
 * `--hover-surface` or `--hover-surface-raised` anywhere would be a second answer to the same
 * question, and one a `var()` substitutes where it is declared rather than where it is used, so
 * it would not follow. `design/hover-surface.test.ts` refuses a read.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** `src/`, which holds `app.css` and every component that could dress itself. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

/** A declaration of either retired name. */
const DECLARES = /--hover-surface(?:-raised)?\s*:/;

function filesUnder(from: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(from)) {
		const path = join(from, entry);
		if (statSync(path).isDirectory()) {
			if (entry !== 'node_modules') found.push(...filesUnder(path));
		} else if (entry.endsWith('.svelte') || entry.endsWith('.css')) {
			found.push(path);
		}
	}
	return found;
}

/* Comments come out first: prose naming the old tokens is not a declaration of them. */
function withoutComments(text: string): string {
	return text.replace(/\/\*[\s\S]*?\*\//g, '');
}

describe('the surface-step hovers', () => {
	const files = filesUnder(SOURCE);

	it('are looked for in a real tree, the stylesheet included', () => {
		expect(files.length).toBeGreaterThan(100);
		expect(files.some((path) => path.endsWith('app.css'))).toBe(true);
	});

	it('are recognized where they are declared', () => {
		expect(DECLARES.test('--hover-surface: var(--sift-scrim-strong);')).toBe(true);
		expect(DECLARES.test('--hover-surface-raised : var(--sift-surface-4);')).toBe(true);
		expect(DECLARES.test('--hover-ink: var(--sift-ink);')).toBe(false);
	});

	it('are declared nowhere', () => {
		const declaring = files
			.filter((path) => DECLARES.test(withoutComments(readFileSync(path, 'utf8'))))
			.map((path) => relative(SOURCE, path).replaceAll('\\', '/'));
		expect(declaring).toEqual([]);
	});
});
