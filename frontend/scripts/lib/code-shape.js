// What `check_code_shape.js` measures and how it compares, as functions a test can drive.
//
// The same ratchets as `scripts/check_code_shape.py` holds over the Python, read with the one
// comment stripper in `tree.js`. Prose is held as a pair, the share and the count of comment lines,
// and only both rising together is growth: deleting code raises the share without adding a word.

import { posix } from 'node:path';

import { commentsIn } from './tree.js';

export const MODULE_LINES = 1000;
export const TEST_FILE_LINES = 2000;
export const COMMENT_SHARE = 25;
/** Below this many non-blank lines a single line moves the share by two points or more. */
export const COMMENT_FLOOR = 50;

/** The per-file maps, in the record's order. */
export const MAPS = ['module_lines', 'test_file_lines', 'comment_share', 'comment_lines'];

/**
 * @param {string} text
 * @returns {{ lines: number, nonblank: number, comments: number, share: number }}
 */
export function measure(text) {
	const all = text.split('\n');
	if (all.at(-1) === '') all.pop();
	/** @type {Set<number>} */
	const nonblank = new Set();
	all.forEach((line, index) => {
		if (line.trim()) nonblank.add(index + 1);
	});
	/** @type {Set<number>} */
	const prose = new Set();
	for (const comment of commentsIn(text)) {
		const spans = comment.text.split('\n').length;
		for (let at = 0; at < spans; at += 1) prose.add(comment.line + at);
	}
	const comments = [...prose].filter((line) => nonblank.has(line)).length;
	const share = nonblank.size ? Math.round((1000 * comments) / nonblank.size) / 10 : 0;
	return { lines: all.length, nonblank: nonblank.size, comments, share };
}

/**
 * One file into the shape: its length against its line, and its comments, a test file's as a
 * module's.
 *
 * @param {Record<string, Record<string, number>>} shape
 * @param {string} path
 * @param {string} text
 * @param {boolean} isTest
 */
export function addFile(shape, path, text, isTest) {
	const found = measure(text);
	if (isTest) {
		if (found.lines > TEST_FILE_LINES) shape.test_file_lines[path] = found.lines;
	} else if (found.lines > MODULE_LINES) shape.module_lines[path] = found.lines;
	if (found.nonblank >= COMMENT_FLOOR && found.share > COMMENT_SHARE) {
		shape.comment_share[path] = found.share;
		shape.comment_lines[path] = found.comments;
	}
}

/** A static, dynamic or type import's specifier. */
const SPECIFIER = /\b(?:from|import)\s*\(?\s*['"]([^'"\n]+)['"]/g;

/**
 * The imports of a file under `src/lib/components` that reach a file at the top of `src/lib`.
 *
 * Every module there has a folder named for its area, and the count of loose files may only fall;
 * this keeps a component from asking for one to come back.
 *
 * @param {string} where the file's path from `src/lib`, with forward slashes
 * @param {string} text
 * @param {Set<string>} folders the folders at the top of `src/lib`, which a bare name may be
 * @returns {string[]}
 */
export function looseImports(where, text, folders) {
	/** @type {string[]} */
	const found = [];
	for (const [, specifier] of text.matchAll(SPECIFIER)) {
		const bare = specifier.split('?')[0];
		let inLib;
		if (bare === '$lib') inLib = '';
		else if (bare.startsWith('$lib/')) inLib = bare.slice('$lib/'.length);
		else if (bare.startsWith('./') || bare.startsWith('../'))
			inLib = posix.join(posix.dirname(where), bare);
		else continue;
		if (inLib.includes('/') || inLib.startsWith('..') || folders.has(inLib)) continue;
		found.push(specifier);
	}
	return found;
}

/** @returns {Record<string, Record<string, number>>} */
export const emptyShape = () => Object.fromEntries(MAPS.map((name) => [name, {}]));

/**
 * What moved between the record and today: `rose` and `added` are refused, `fell` is recorded.
 *
 * @param {{ lib_top_level?: number } & Record<string, any>} recorded
 * @param {Record<string, Record<string, number>>} now
 * @param {number} libTopLevel
 */
export function compareShape(recorded, now, libTopLevel) {
	/** @type {string[]} */ const rose = [];
	/** @type {string[]} */ const added = [];
	/** @type {string[]} */ const fell = [];
	const top = recorded.lib_top_level ?? 0;
	if (libTopLevel > top) rose.push(`lib_top_level: ${libTopLevel} files, recorded ${top}`);
	else if (libTopLevel < top) fell.push(`lib_top_level: ${libTopLevel} files, recorded ${top}`);

	for (const name of ['module_lines', 'test_file_lines']) {
		const was = recorded[name] ?? {};
		for (const [path, value] of Object.entries(now[name])) {
			if (!(path in was)) added.push(`${name} ${path}: ${value}, over the line and not recorded`);
			else if (value > was[path]) rose.push(`${name} ${path}: ${value}, recorded ${was[path]}`);
			else if (value < was[path]) fell.push(`${name} ${path}: ${value}, recorded ${was[path]}`);
		}
		for (const path of Object.keys(was))
			if (!(path in now[name])) fell.push(`${name} ${path}: under the line, recorded ${was[path]}`);
	}

	const shares = recorded.comment_share ?? {};
	const counts = recorded.comment_lines ?? {};
	for (const [path, value] of Object.entries(now.comment_share)) {
		const lines = now.comment_lines[path];
		const said = `comments ${path}: ${value}% in ${lines} lines, recorded ${shares[path]}% in ${counts[path]}`;
		if (!(path in shares)) added.push(`comments ${path}: ${value}% over the line and not recorded`);
		else if (value > shares[path] && lines > (counts[path] ?? 0)) rose.push(said);
		else if (value !== shares[path] || lines !== counts[path]) fell.push(said);
	}
	for (const path of Object.keys(shares))
		if (!(path in now.comment_share))
			fell.push(`comments ${path}: under the line, recorded ${shares[path]}%`);

	return { rose, added, fell };
}

/**
 * The record as it is written: the top-level count, then each map sorted by path.
 *
 * @param {Record<string, Record<string, number>>} now
 * @param {number} libTopLevel
 */
export function asRecord(now, libTopLevel) {
	return {
		lib_top_level: libTopLevel,
		...Object.fromEntries(
			MAPS.map((name) => [
				name,
				Object.fromEntries(Object.entries(now[name]).sort(([a], [b]) => (a < b ? -1 : 1)))
			])
		)
	};
}
