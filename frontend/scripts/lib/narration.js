// The words a comment in this repository does not use, read from
// `tests/gates/data/narration.json`: the client half of the one list. The Python half is
// `tests/gates/test_no_narration_in_comments.py`; both compile every entry the same way and both
// prove each entry against its own example and its near miss.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { commentsIn } from './tree.js';

const HERE = dirname(fileURLToPath(import.meta.url));

/** The one file, from `frontend/scripts/lib`. */
export const NARRATION = join(HERE, '..', '..', '..', 'tests', 'gates', 'data', 'narration.json');

/**
 * @typedef {{ pattern: string, case?: boolean, example: string, clean: string }} RawEntry
 * @typedef {{ name: string, why: string, entries: RawEntry[] }} RawFamily
 * @typedef {{ family: string, source: string, pattern: RegExp, example: string, clean: string }} Entry
 * @typedef {{ line: number, family: string, found: string }} Finding
 */

/** @type {{ families: RawFamily[] } | null} */
let loaded = null;

/** The whole file, parsed once per process. */
export function loadNarration() {
	if (loaded === null) loaded = JSON.parse(readFileSync(NARRATION, 'utf8'));
	return /** @type {{ families: RawFamily[] }} */ (loaded);
}

/**
 * Every entry, compiled: case-insensitive unless it says `"case": true`, `g` so every match counts.
 *
 * @returns {Entry[]}
 */
export function narrationEntries() {
	return loadNarration().families.flatMap((family) =>
		family.entries.map((entry) => ({
			family: family.name,
			source: entry.pattern,
			pattern: new RegExp(entry.pattern, entry.case ? 'g' : 'gi'),
			example: entry.example,
			clean: entry.clean
		}))
	);
}

/**
 * Every offence in one file's comments, at the line it sits on.
 *
 * @param {string} text the whole file
 * @param {Entry[]} [entries]
 * @returns {Finding[]}
 */
export function narrationIn(text, entries = narrationEntries()) {
	/** @type {Finding[]} */
	const found = [];
	for (const comment of commentsIn(text)) {
		for (const entry of entries) {
			entry.pattern.lastIndex = 0;
			for (const match of comment.text.matchAll(entry.pattern)) {
				const before = comment.text.slice(0, match.index ?? 0).split('\n').length - 1;
				found.push({ line: comment.line + before, family: entry.family, found: match[0] });
			}
		}
	}
	return found;
}
