// What `check_code_shape.js` measures and how it compares, as functions a test can drive.
//
// The same ratchets as `scripts/check_code_shape.py` holds over the Python, read with the one
// comment stripper in `tree.js`. Prose is held as a pair, the share and the count of comment lines,
// and only both rising together is growth: deleting code raises the share without adding a word.
// A function's branches are counted from the TypeScript parser's tree.

import { posix } from 'node:path';

import ts from 'typescript';

import { commentsIn } from './tree.js';

export const MODULE_LINES = 1000;
export const TEST_FILE_LINES = 2000;
/** A linter's usual line for a function's cyclomatic complexity in JavaScript. */
export const FUNCTION_BRANCHES = 20;
export const COMMENT_SHARE = 25;
/** Below this many non-blank lines a single line moves the share by two points or more. */
export const COMMENT_FLOOR = 50;

/** The per-file maps, in the record's order. */
export const MAPS = [
	'module_lines',
	'function_branches',
	'test_file_lines',
	'comment_share',
	'comment_lines'
];

/** The maps held as plain counts; `function_branches` is keyed `path::function`. */
const COUNTS = ['module_lines', 'function_branches', 'test_file_lines'];

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

/** The kinds that add a path by being there: a branch, a loop, a `catch`, a `case` with a test. */
const ONE_MORE_PATH = new Set([
	ts.SyntaxKind.IfStatement,
	ts.SyntaxKind.ConditionalExpression,
	ts.SyntaxKind.ForStatement,
	ts.SyntaxKind.ForInStatement,
	ts.SyntaxKind.ForOfStatement,
	ts.SyntaxKind.WhileStatement,
	ts.SyntaxKind.DoStatement,
	ts.SyntaxKind.CatchClause,
	ts.SyntaxKind.CaseClause
]);

/** `&&`, `||` and `??` choose what is evaluated, and so do their assigning forms. */
const SHORT_CIRCUITS = new Set([
	ts.SyntaxKind.AmpersandAmpersandToken,
	ts.SyntaxKind.BarBarToken,
	ts.SyntaxKind.QuestionQuestionToken,
	ts.SyntaxKind.AmpersandAmpersandEqualsToken,
	ts.SyntaxKind.BarBarEqualsToken,
	ts.SyntaxKind.QuestionQuestionEqualsToken
]);

const ANONYMOUS = '(anonymous)';
const SCRIPT_BLOCK = /<script\b[^>]*>([\s\S]*?)<\/script>/g;

/** @param {ts.Node} node */
const addsPath = (node) =>
	ONE_MORE_PATH.has(node.kind) ||
	(ts.isBinaryExpression(node) && SHORT_CIRCUITS.has(node.operatorToken.kind));

/**
 * The variable, property or field a value is given to, if it is given to one.
 *
 * @param {ts.Node | undefined} holder
 * @returns {ts.Node | undefined}
 */
const givenTo = (holder) =>
	holder &&
	(ts.isVariableDeclaration(holder) ||
		ts.isPropertyAssignment(holder) ||
		ts.isPropertyDeclaration(holder))
		? holder.name
		: undefined;

/**
 * What a function is called where it is written: its own name, the variable, property or field
 * it is assigned to, or the one its caller's answer is assigned to (`const rows = derive(() => ...)`),
 * so a callback's key does not move when another is written above it.
 *
 * @param {ts.Node} node
 * @returns {string}
 */
function nameOf(node) {
	if (ts.isConstructorDeclaration(node)) return 'constructor';
	const holder = node.parent;
	const named =
		/** @type {{ name?: ts.Node }} */ (node).name ??
		givenTo(holder) ??
		(holder && ts.isCallExpression(holder) ? givenTo(holder.parent) : undefined);
	const text = named && /** @type {{ text?: string }} */ (named).text;
	return text || ANONYMOUS;
}

/**
 * Every function with a body in one script, with its cyclomatic complexity: one, and one more for
 * each branch, loop, `catch`, `case` with a test and short-circuit inside it. A function written
 * inside another is counted by itself, as a linter's complexity rule counts it, because a callback
 * is not a path through the function that passes it.
 *
 * @param {string} text a module's source, or a component's script
 * @returns {[string, number][]} `[qualified name, complexity]`, a repeated name numbered from its second
 */
function functionsIn(text) {
	const source = ts.createSourceFile('x.ts', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
	/** @type {[string, { count: number }][]} */
	const found = [];
	/**
	 * @param {ts.Node} node
	 * @param {string} scope
	 * @param {{ count: number } | null} tally
	 */
	const visit = (node, scope, tally) => {
		let inner = scope;
		let mine = tally;
		if (ts.isClassLike(node) && node.name) inner = `${scope}${node.name.text}.`;
		else if (ts.isFunctionLike(node) && /** @type {{ body?: ts.Node }} */ (node).body) {
			const name = nameOf(node);
			mine = { count: 1 };
			found.push([`${scope}${name}`, mine]);
			if (name !== ANONYMOUS) inner = `${scope}${name}.`;
		}
		if (mine && addsPath(node)) mine.count += 1;
		ts.forEachChild(node, (child) => visit(child, inner, mine));
	};
	visit(source, '', null);
	return found.map(([name, tally]) => [name, tally.count]);
}

/**
 * `{ qualified name: complexity }` for every function of a module or of a component's scripts.
 *
 * @param {string} path
 * @param {string} text
 * @returns {Record<string, number>}
 */
export function branchesIn(path, text) {
	const scripts = path.endsWith('.svelte')
		? [...text.matchAll(SCRIPT_BLOCK)].map(([, script]) => script)
		: [text];
	/** @type {Record<string, number>} */
	const found = {};
	for (const script of scripts)
		for (const [name, count] of functionsIn(script)) {
			let key = name;
			for (let seen = 2; key in found; seen += 1) key = `${name}#${seen}`;
			found[key] = count;
		}
	return found;
}

/**
 * One file into the shape: its length and its functions' branches against their lines, and its
 * comments, a test file's as a module's.
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
	} else {
		if (found.lines > MODULE_LINES) shape.module_lines[path] = found.lines;
		for (const [name, count] of Object.entries(branchesIn(path, text)))
			if (count > FUNCTION_BRANCHES) shape.function_branches[`${path}::${name}`] = count;
	}
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

/** A `path::Class.function#2` key's own function name. */
const baseName = (/** @type {string} */ key) =>
	(key.split('::')[1] ?? '').split('#')[0].split('.').pop();

/** Whether two keys name the same function in two files. */
const sameFunction = (/** @type {string} */ a, /** @type {string} */ b) =>
	baseName(a) === baseName(b);

/** @returns {Record<string, Record<string, number>>} */
export const emptyShape = () => Object.fromEntries(MAPS.map((name) => [name, {}]));

/**
 * What moved between the record and today: `rose` and `added` are refused, `fell` is recorded.
 * A function that leaves one module and arrives in another no more branched than it left keeps
 * its number: splitting a module moves functions, and a move is not a new entry.
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

	for (const name of COUNTS) {
		const was = recorded[name] ?? {};
		const gone = Object.keys(was).filter((path) => !(path in now[name]));
		for (const [path, value] of Object.entries(now[name])) {
			if (path in was) {
				if (value > was[path]) rose.push(`${name} ${path}: ${value}, recorded ${was[path]}`);
				else if (value < was[path]) fell.push(`${name} ${path}: ${value}, recorded ${was[path]}`);
				continue;
			}
			const from = gone.findIndex(
				(old) => name === 'function_branches' && sameFunction(old, path) && was[old] >= value
			);
			if (from < 0) added.push(`${name} ${path}: ${value}, over the line and not recorded`);
			else fell.push(`${name} ${path}: moved from ${gone.splice(from, 1)[0]}`);
		}
		for (const path of gone) fell.push(`${name} ${path}: under the line, recorded ${was[path]}`);
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
