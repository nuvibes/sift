#!/usr/bin/env node
/*
 * A style rule that matches nothing fails the build, including the ones the compiler cannot see.
 *
 * `check_no_dead_css.js` listens to the compiler's own `css_unused_selector` warning, and the
 * compiler is right to be careful: an element carrying a spread (`<div {...props}>`) could be
 * handed ANY class by whatever built the props, so a rule naming a class is kept alive by every
 * spread in the component. That is correct and it is a blind spot, and the library makes it a
 * common one: every bits-ui `{#snippet child({ props })}` renders its element with `{...props}`.
 * Rename a bar's inner class in a component with a spread-bearing track, and `.fill` stays "used":
 * the bar draws empty and the compiler says nothing.
 *
 * ## The pass
 *
 * Compile each component twice: as written, and with every spread taken out of its markup. A
 * selector the second compile calls unused and the first does not is kept alive ONLY by a spread.
 * That alone is not a verdict: a spread really does bring `data-state`, `aria-checked` and the
 * like, so an attribute selector on a bits-ui element is exactly this shape and is alive. So the
 * rule is narrower and cannot be argued with: such a selector is dead when a class it names is
 * WRITTEN NOWHERE in the file (in no quoted string and no `class:` directive, comments aside). A
 * spread built in this file carries only classes this file wrote; one built elsewhere is styled
 * where it was built, because a scoped rule here is this file's promise about this file's markup.
 *
 * Run: node scripts/check_no_dead_css_under_a_spread.js
 */
import { readFile } from 'node:fs/promises';

import { compile, parse } from 'svelte/compiler';

import { everySvelteFile, fromSource, SOURCE, withoutComments } from './lib/tree.js';

const CODE = 'css_unused_selector';
const STYLE = /<style\b[^>]*>[\s\S]*?<\/style>/g;

/** Every spread ATTRIBUTE's range in the markup, read off the parser rather than a pattern.
 *
 * A pattern cannot tell `<div {...props}>` from `values={{ ...record, name }}`: the second is an
 * object spread inside an expression, and cutting it out leaves a component that does not parse.
 */
function spreadRanges(node, found = []) {
	if (node === null || typeof node !== 'object') return found;
	if (Array.isArray(node)) {
		for (const one of node) spreadRanges(one, found);
		return found;
	}
	if (node.type === 'SpreadAttribute') found.push([node.start, node.end]);
	for (const [key, value] of Object.entries(node)) {
		if (key === 'instance' || key === 'module' || key === 'css') continue;
		spreadRanges(value, found);
	}
	return found;
}

/** The component with every spread attribute removed; everything else exactly as written. */
export function withoutSpreads(source) {
	const ranges = spreadRanges(parse(source, { modern: true }).fragment).sort((a, b) => b[0] - a[0]);
	let out = source;
	for (const [start, end] of ranges) out = out.slice(0, start) + out.slice(end);
	return out;
}

/** Every word this file WRITES outside its stylesheet: in a quoted string or a `class:` directive. */
export function writtenWords(source) {
	const text = withoutComments(source).replace(STYLE, '');
	const words = new Set();
	for (const [, quoted] of text
		.matchAll(/(["'`])((?:\\.|(?!\1)[^\\])*)\1/g)
		.map((m) => [m[0], m[2]]))
		for (const word of quoted.match(/[A-Za-z_][\w-]*/g) ?? []) words.add(word);
	for (const [, name] of text.matchAll(/\bclass:([A-Za-z_][\w-]*)/g)) words.add(name);
	return words;
}

/** The classes a selector names in THIS file's scope: `:global(...)` is somebody else's. */
export function scopedClasses(selector) {
	const scoped = selector
		.replace(/:global\((?:[^()]|\([^()]*\))*\)/g, '')
		.replace(/\[[^\]]*\]/g, '');
	return [...scoped.matchAll(/\.([A-Za-z_][\w-]*)/g)].map((m) => m[1]);
}

function unused(source, filename) {
	const { warnings } = compile(source, { filename, generate: false });
	return new Set(
		warnings
			.filter((one) => one.code === CODE)
			.map((one) =>
				one.message
					.split('\n')[0]
					.replace(/^Unused CSS selector /, '')
					.replace(/^"|"$/g, '')
			)
	);
}

/** The selectors in one component that only a spread keeps alive, and that no spread can. */
export function deadUnderASpread(source, filename) {
	if (
		!/<style\b/.test(source) ||
		spreadRanges(parse(source, { modern: true }).fragment).length === 0
	)
		return [];
	const asWritten = unused(source, filename);
	const bare = unused(withoutSpreads(source), filename);
	const written = writtenWords(source);
	return [...bare]
		.filter((selector) => !asWritten.has(selector))
		.filter((selector) => scopedClasses(selector).some((name) => !written.has(name)));
}

const isMain =
	process.argv[1] &&
	import.meta.url.endsWith(process.argv[1].replaceAll('\\', '/').split('/').pop());
if (isMain) {
	const found = [];
	let checked = 0;
	for (const path of await everySvelteFile(SOURCE)) {
		const source = await readFile(path, 'utf8');
		if (spreadRanges(parse(source, { modern: true }).fragment).length === 0) continue;
		checked += 1;
		for (const selector of deadUnderASpread(source, path))
			found.push(`${fromSource(path)}: ${selector}`);
	}
	/* A pass that looked at nothing would pass for ever. Forty-odd components spread props today. */
	if (checked < 20) {
		console.error(
			`check_no_dead_css_under_a_spread: only ${checked} components carry a spread; the walk is broken.`
		);
		process.exit(2);
	}
	if (found.length > 0) {
		console.error(
			'These style rules are kept alive only by a spread, and name a class this file never writes:\n'
		);
		for (const line of found) console.error(`  ${line}`);
		console.error(
			'\nA spread cannot bring a class the component never wrote, so nothing wears these.' +
				'\nFix the selector, or delete the rule if the thing it dressed is gone.\n'
		);
		process.exit(1);
	}
	console.log(`No dead style rules under a spread (${checked} components).`);
}
