#!/usr/bin/env node
// The window's title strip is cleared with PADDING, never with a top margin.
//
// ## The fault
//
// The packaged desktop shell hides the operating system's title bar and draws its own strip over
// the page: `--window-chrome`, 36px, zero in a browser. A screen that claims the whole window has
// to start below it, and the obvious way to write that is:
//
//     margin-block-start: var(--window-chrome);
//     min-height: calc(100dvh - var(--window-chrome));
//
// A top margin on a child of `body` has nothing to collapse against (`body` carries no border and
// no padding), so it collapses THROUGH and moves `body` itself down by the strip's height while
// `body` stays a full window tall: the document is exactly 36px taller than the window.
//
// Neither symptom looks like a margin. The document grows a vertical scrollbar with no content for
// it; and `WindowBar` is `position: fixed` with `inset-inline: 0`, which resolves against the
// viewport MINUS that scrollbar, so the title strip and its hairline stop short of the right edge
// and read as cut off.
//
// ## Why a gate rather than a comment
//
// It is invisible in a browser. `--window-chrome` is `0px` there, so the margin is zero, nothing
// collapses, and every screen looks perfect: in `npm run dev`, in the e2e suite, in every
// screenshot. It appears only in the packaged application.
//
// ## The rule
//
// `--window-chrome` may be used in `padding`, in `inset`, in a `calc` for either, and as a
// `block-size`. It may not be the value of any `margin` property.

import { readFile } from 'node:fs/promises';

import { everyFile, fromSource, SOURCE, withoutComments } from './lib/tree.js';

/** A margin declaration (longhand, logical or shorthand) whose value names the strip. */
const MARGIN_WITH_CHROME =
	/(^|[\s;{])(margin(?:-[a-z-]+)?)\s*:\s*[^;}]*var\(\s*--window-chrome\s*\)[^;}]*/g;

/* Comments out first: the note beside the fix contains the word "margin", because it is about a
 * margin, and a rule matched on raw text matches prose as readily as code. Block comments and
 * markup comments both go (`withoutComments` from `lib/tree.js`, which blanks them line for
 * line), so the line numbers reported below are the real ones. A `//` is not a comment in CSS, so
 * the line form stays off.
 */
const stripComments = (text) => withoutComments(text, { line: false });

/** One rule body, so a declaration can be read against the ones that follow it in the same rule. */
const RULE = /[^{}]*\{([^{}]*)\}/g;

/** The clearing itself, as a longhand on the block-start side. */
const CLEARS = /padding(?:-block-start|-top)\s*:\s*[^;}]*var\(\s*--window-chrome\s*\)/;

/**
 * A `padding` SHORTHAND, which writes every side and therefore erases a longhand above it.
 *
 * A screen that clears the strip with `padding-block-start` and then says `padding: var(--space-4)`
 * two lines under it has overwritten the clearing, and starts under the strip. No margin is
 * involved, so the rule above sees nothing. Invisible in a browser for the same reason:
 * `--window-chrome` is `0px` there, so the overwritten value and the intended one are the same.
 */
const PADDING_SHORTHAND = /(^|[\s;{])padding\s*:/;

const offences = [];
const overwritten = [];
let looked = 0;

for (const file of await everyFile(SOURCE, ['.svelte', '.css'])) {
	const raw = await readFile(file, 'utf8');
	if (!raw.includes('--window-chrome')) continue;
	const text = stripComments(raw);
	if (!text.includes('--window-chrome')) continue;
	looked += 1;
	for (const found of text.matchAll(MARGIN_WITH_CHROME)) {
		const line = text.slice(0, found.index).split('\n').length;
		offences.push(`${fromSource(file)}:${line}  ${found[0].trim()}`);
	}
	for (const rule of text.matchAll(RULE)) {
		const body = rule[1];
		const clearing = body.match(CLEARS);
		if (!clearing) continue;
		if (!PADDING_SHORTHAND.test(body.slice(clearing.index + clearing[0].length))) continue;
		const line = text.slice(0, rule.index).split('\n').length;
		overwritten.push(`${fromSource(file)}:${line}  ${clearing[0].trim()}`);
	}
}

if (offences.length > 0) {
	console.error(
		'\nThe window title strip is cleared with a MARGIN here, and a margin collapses.\n\n' +
			offences.map((one) => `  ${one}`).join('\n') +
			'\n\nA top margin on a child of `body` collapses through it: `body` moves down by the ' +
			'strip\nheight and stays a full window tall, so the document scrolls by exactly that much ' +
			'and\ngrows a scrollbar. `WindowBar` is fixed with `inset-inline: 0`, which then resolves ' +
			'to the\nviewport minus the scrollbar, and its hairline stops short of the right edge.\n\n' +
			'Write it as padding instead, with the full window height:\n\n' +
			'    padding-block-start: var(--window-chrome);\n' +
			'    min-height: 100dvh;\n\n' +
			'This is invisible in a browser, where `--window-chrome` is 0px.\n'
	);
	process.exit(1);
}

if (overwritten.length > 0) {
	console.error(
		'\nThe window title strip is cleared here and then OVERWRITTEN in the same rule.\n\n' +
			overwritten.map((one) => `  ${one}`).join('\n') +
			'\n\nA `padding` shorthand writes every side, so a `padding: ...` after the clearing puts ' +
			'the\ntop back to whatever it names and the screen starts behind the strip. Invisible in a ' +
			'browser,\nwhere `--window-chrome` is `0px` and the two values are the same number.\n\n' +
			'Put the shorthand FIRST and fold the strip into what follows it:\n\n' +
			'    padding: var(--space-4);\n' +
			'    padding-block-start: calc(var(--window-chrome) + var(--space-4));\n'
	);
	process.exit(1);
}

console.log(
	`window-chrome: ${looked} file(s) clear the title strip; none with a margin, none overwritten`
);
