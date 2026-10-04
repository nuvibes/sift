/*
 * NOTHING READS THE SURFACE-STEP HOVERS: THEY ARE GONE.
 *
 * A flat thing under the pointer takes the state layer (`--layer-hover`, the ink mixed into its own
 * ground), which answers the same on the canvas, on a card and inside a translucent panel. Stepping
 * the ground to another surface is the wrong material wherever the ground is not the one the step
 * was spelled from, and invisible wherever the ground already is that step. Neither
 * `--hover-surface` nor `--hover-surface-raised` is declared any more, so a read of one resolves to
 * nothing and the hover silently disappears; this refuses it. `common/hover-surface.test.ts`
 * refuses a declaration.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, it } from 'vitest';

/** `src/`, which holds `app.css` and every component that could dress itself. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

/** A read of either retired name, spaced any way CSS allows. */
const READS = /var\(\s*--hover-surface(?:-raised)?\s*[,)]/;

function filesUnder(from: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(from)) {
		const path = join(from, entry);
		if (statSync(path).isDirectory()) {
			if (entry !== 'node_modules' && entry !== '.svelte-kit') found.push(...filesUnder(path));
		} else if (/\.(svelte|css|ts)$/.test(entry) && !entry.endsWith('.test.ts')) {
			found.push(path);
		}
	}
	return found;
}

const READING = filesUnder(SOURCE)
	.filter((path) => READS.test(readFileSync(path, 'utf8')))
	.map((path) => relative(SOURCE, path).replaceAll('\\', '/'))
	.sort();

it('is reading a real tree', () => {
	// A survey matching nothing would pass for ever.
	expect(filesUnder(SOURCE).length).toBeGreaterThan(100);
});

it('knows a read when it sees one', () => {
	expect(READS.test('background: var(--hover-surface);')).toBe(true);
	expect(READS.test('background: var( --hover-surface-raised, red);')).toBe(true);
	expect(READS.test('color: var(--hover-ink);')).toBe(false);
});

it('has no reader: a flat thing under the pointer takes the state layer', () => {
	expect(READING).toEqual([]);
});
