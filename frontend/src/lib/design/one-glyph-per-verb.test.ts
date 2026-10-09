import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/* One verb, one glyph, wherever the verb is offered. */

/** The whole of `src`, resolved from this file: the runner is started from more than one place. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

/** A verb literal: an id, the word on it, and the glyph beside that word. */
const VERB =
	/id:\s*'[^']+'\s*,\s*label:\s*'([^']{1,40})'\s*(?:as\s+const\s*)?,\s*icon:\s*'([a-z0-9_]+)'/g;

function filesUnder(directory: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(directory)) {
		const path = join(directory, entry);
		if (statSync(path).isDirectory()) {
			// The gallery is a separate repository nested in the tree and absent from a clone, so a
			// rule that depended on it would mean one thing here and another there.
			if (entry !== 'node_modules' && path !== join(SOURCE, 'routes', 'design')) {
				found.push(...filesUnder(path));
			}
			continue;
		}
		// A test double is not a menu: one of them names "Scan now" with a glyph of its own.
		if (entry.includes('.test.')) continue;
		if (entry.endsWith('.svelte') || entry.endsWith('.ts')) found.push(path);
	}
	return found;
}

/** Every verb declared in one file: its word, its glyph, and the line it is on. */
function verbsIn(source: string): { label: string; icon: string; line: number }[] {
	return [...source.matchAll(VERB)].map((match) => ({
		label: match[1],
		icon: match[2],
		line: source.slice(0, match.index).split('\n').length
	}));
}

describe('a verb wears the same glyph everywhere it is offered', () => {
	const files = filesUnder(SOURCE);

	it('finds files to read, so a broken walk cannot pass as a clean tree', () => {
		expect(files.length).toBeGreaterThan(100);
	});

	it('reads the shape it claims to read, proved against a known pair', () => {
		/* A known positive, because silence and success are the same thing to a sweep. */
		expect(
			verbsIn(`{ id: 'enrich', label: 'Enrich', icon: 'search' as const, run: open }`)
		).toEqual([{ label: 'Enrich', icon: 'search', line: 1 }]);
	});

	it('finds every verb in the tree, not a handful', () => {
		const all = files.flatMap((file) => verbsIn(readFileSync(file, 'utf8')));
		expect(all.length).toBeGreaterThan(80);
	});

	it('draws Enrich as the backlight on every menu that offers it', () => {
		/* Named rather than left to the sweep below, because this is the one most likely to come
		   back: the two entity pages declare their Options menu by hand, so the shared list
		   cannot keep them honest. */
		const drawn = new Map<string, string>();
		for (const file of files) {
			for (const { label, icon } of verbsIn(readFileSync(file, 'utf8'))) {
				if (label === 'Enrich') drawn.set(relative(SOURCE, file), icon);
			}
		}
		expect(drawn.size, 'nothing in the tree declares Enrich any more').toBeGreaterThan(2);
		for (const [where, icon] of drawn) {
			expect(icon, `${where} draws Enrich as ${icon}`).toBe('backlight_low');
		}
	});

	it('gives no verb two glyphs', () => {
		const drawn = new Map<string, Map<string, string[]>>();
		for (const file of files) {
			for (const { label, icon, line } of verbsIn(readFileSync(file, 'utf8'))) {
				const byIcon = drawn.get(label) ?? new Map<string, string[]>();
				byIcon.set(icon, [...(byIcon.get(icon) ?? []), `${relative(SOURCE, file)}:${line}`]);
				drawn.set(label, byIcon);
			}
		}

		const split: string[] = [];
		for (const [label, byIcon] of drawn) {
			if (byIcon.size < 2) continue;
			const spread = [...byIcon].map(([icon, at]) => `${icon} (${at.join(', ')})`).join(' vs ');
			split.push(`"${label}" is drawn ${byIcon.size} ways: ${spread}`);
		}
		expect(split, `\n${split.join('\n')}\n`).toEqual([]);
	});
});
