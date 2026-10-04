#!/usr/bin/env node
/*
 * EVERY ANIMATION NAMES A MOTION THAT EXISTS.
 *
 * The motions a screen may run are declared once, in `app.css`, and a component names them. The
 * design fence holds a component's own `@keyframes` at zero, so a component can no longer bring a
 * motion with it: everything it animates comes from that one list.
 *
 * That makes a misspelling silent. An `animation` naming a keyframes rule that does not exist is not
 * an error in CSS: the declaration is kept, nothing moves, and every test is green. So this reads
 * every `animation` and `animation-name` in every stylesheet and refuses a name no `@keyframes`
 * declares, in `app.css` or in the same file.
 *
 * What it cannot read: a name built at runtime (`var(--x)` in the name position) is skipped, since
 * only the page knows what it resolves to.
 */

import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { everySvelteFile, fromSource, SOURCE, withoutComments } from './lib/tree.js';

const GLOBAL_CSS = join(SOURCE, 'app.css');

/** Words the `animation` shorthand accepts that are not a name. */
const KEYWORDS = new Set([
	'none',
	'initial',
	'inherit',
	'unset',
	'revert',
	'revert-layer',
	'linear',
	'ease',
	'ease-in',
	'ease-out',
	'ease-in-out',
	'step-start',
	'step-end',
	'infinite',
	'normal',
	'reverse',
	'alternate',
	'alternate-reverse',
	'forwards',
	'backwards',
	'both',
	'running',
	'paused',
	'auto'
]);

/**
 * Every stylesheet in a file: all of a component's `<style>` blocks, or the whole of a CSS file.
 *
 * @param {string} path
 * @param {string} code
 */
export function stylesOf(path, code) {
	if (!path.endsWith('.svelte')) return code;
	return [...code.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((found) => found[1]).join('\n');
}

/**
 * The names a stylesheet declares with `@keyframes`.
 *
 * @param {string} styles
 */
export function declared(styles) {
	return new Set([...styles.matchAll(/@keyframes\s+([\w-]+)/g)].map((found) => found[1]));
}

/**
 * The names a stylesheet runs: from `animation-name`, and from each comma-separated animation in the
 * `animation` shorthand. A function (`var()`, `calc()`, `cubic-bezier()`, `steps()`) is taken out
 * first, then numbers and times, then keywords; what is left is the name.
 *
 * @param {string} styles
 * @returns {string[]}
 */
export function named(styles) {
	const names = [];
	for (const found of styles.matchAll(/(?:^|[\s;{])animation(-name)?\s*:\s*([^;}]*)/g)) {
		const value = found[2].replace(/[\w-]*\((?:[^()]|\([^()]*\))*\)/g, ' ');
		for (const one of value.split(',')) {
			for (const word of one.trim().split(/\s+/)) {
				if (!word || KEYWORDS.has(word) || /^-?[\d.]/.test(word) || word.startsWith('!')) continue;
				names.push(word);
			}
		}
	}
	return names;
}

/**
 * Each animation that names a motion neither `global` nor its own file declares.
 *
 * @param {{ where: string, styles: string }[]} sheets every stylesheet, comments already blanked
 * @param {string} global the stylesheet whose motions every file may name
 * @returns {string[]}
 */
export function unnamedMotions(sheets, global) {
	const everywhere = declared(global);
	const complaints = [];
	for (const { where, styles } of sheets) {
		const own = declared(styles);
		for (const name of named(styles)) {
			if (everywhere.has(name) || own.has(name)) continue;
			complaints.push(`${where}: animates \`${name}\`, and no @keyframes declares it.`);
		}
	}
	return complaints;
}

async function main() {
	const global = withoutComments(await readFile(GLOBAL_CSS, 'utf8'));
	const paths = [GLOBAL_CSS, ...(await everySvelteFile(SOURCE))];
	const sheets = [];
	for (const path of paths) {
		const code = withoutComments(await readFile(path, 'utf8'));
		sheets.push({
			where: path === GLOBAL_CSS ? 'app.css' : fromSource(path),
			styles: stylesOf(path, code)
		});
	}
	const motions = declared(global).size;

	/* A scan that found nothing would pass, silently, forever. */
	if (motions < 3 || sheets.length < 200) {
		console.error(
			`\nnamed-motions: read ${motions} motions in app.css across ${sheets.length} files. That is too few to be right.\n`
		);
		process.exit(1);
	}

	const complaints = unnamedMotions(sheets, global);
	if (complaints.length > 0) {
		console.error(
			'\nAn animation naming a motion nobody declared runs nothing, and says nothing.\n'
		);
		for (const complaint of complaints) console.error(`  ${complaint}`);
		console.error(
			'\n  Name one of the motions app.css declares, or declare the new one there, beside the others.\n'
		);
		process.exit(1);
	}

	console.log(
		`named-motions: ${sheets.length} stylesheets; every animation names one of ${motions} motions`
	);
}

if (process.argv[1] && process.argv[1].endsWith('check_named_motions.js')) {
	await main();
}
