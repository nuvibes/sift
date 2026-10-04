#!/usr/bin/env node
/*
 * How far a step through a clip goes is ONE number, and everything that states it agrees.
 *
 * Every surface that steps reads `SKIP_SECONDS`, so the behaviour cannot drift. What still can is
 * everything AROUND it: the button says "Back 5 seconds", the glyph is `replay_5` with the digit
 * drawn into it, and neither is derived from the constant. Change the step to ten and the picture,
 * the tooltip and the accessible name all keep saying five, with a green suite, because nothing
 * else compares what somebody is TOLD against what the control DOES.
 *
 * So this checks the two halves against each other, and refuses a private copy of the number.
 *
 * Not a general "find a magic number" gate. A step is a thing with a name, drawn on a control, and
 * that is answerable by looking rather than by guessing at intent.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = resolve(HERE, '..', 'src');

/** The one file that holds it. */
const HOME = resolve(SOURCE, 'lib', 'player', 'skip.ts');

/*
 * Tests are not a second copy. A test asserting a button reads "Back 5 seconds" is the evidence the
 * one definition reaches the screen, and refusing it would leave the gate satisfiable only by having
 * no test of the thing it protects.
 */
const A_TEST = /\.(test|spec)\.(ts|js)$/;

/** The generated glyph map names every icon in the font, including ones nothing draws. */
const GENERATED = resolve(SOURCE, 'lib', 'generated');

function* walk(where) {
	for (const entry of readdirSync(where)) {
		const path = join(where, entry);
		if (statSync(path).isDirectory()) {
			yield* walk(path);
			continue;
		}
		if (path.endsWith('.ts') || path.endsWith('.svelte')) yield path;
	}
}

function theStep() {
	const declared = readFileSync(HOME, 'utf8').match(/export const SKIP_SECONDS = (\d+);/);
	if (!declared) {
		console.error(`\n${relative(SOURCE, HOME)} no longer declares SKIP_SECONDS as a number.`);
		console.error(
			'That is the one place the step is written down; this gate reads it from there.\n'
		);
		process.exit(1);
	}
	return Number(declared[1]);
}

const STEP = theStep();

/*
 * What a surface can say about the step, and how to read the number out of it.
 *
 * `says` matches only where the step is being NAMED: a verb and a unit, or a glyph whose digit is
 * part of the picture. Prose that happens to contain a number is not a statement about this control
 * and is not this gate's business.
 */
const SAYS = [
	{
		what: 'the words on a step control',
		pattern: /\b(?:Back|Forward) (\d+) seconds\b/g,
		instead: `say ${STEP}, or change src/lib/player/skip.ts`
	},
	{
		what: 'the glyph drawn on a step control',
		pattern: /\b(?:replay|forward)_(\d+)\b/g,
		instead: `use the ${STEP}-second glyph, or change src/lib/player/skip.ts`
	}
];

/*
 * A second declaration of the step. `const SEEK_SECONDS = SKIP_SECONDS` is a local name for the one
 * number and is fine; `const SEEK_SECONDS = 5` is the fault this module refuses.
 */
const A_PRIVATE_COPY =
	/\b(?:const|let|var)\s+(SKIP_SECONDS|SEEK_SECONDS|STEP_SECONDS)\s*(?::\s*number\s*)?=\s*\d/;

const disagreeing = [];
const copies = [];

for (const path of walk(SOURCE)) {
	if (path === HOME || A_TEST.test(path) || path.startsWith(GENERATED)) continue;
	const lines = readFileSync(path, 'utf8').split('\n');
	lines.forEach((line, index) => {
		const where = `${relative(SOURCE, path)}:${index + 1}`;
		if (A_PRIVATE_COPY.test(line)) copies.push({ where, line: line.trim() });
		for (const surface of SAYS) {
			surface.pattern.lastIndex = 0;
			let hit;
			while ((hit = surface.pattern.exec(line)) !== null) {
				if (Number(hit[1]) !== STEP) {
					disagreeing.push({ where, line: line.trim(), said: Number(hit[1]), surface });
				}
			}
		}
	});
}

if (disagreeing.length > 0 || copies.length > 0) {
	console.error(`\nThe step through a clip is ${STEP} seconds and something disagrees:\n`);
	for (const one of disagreeing) {
		console.error(`  ${one.where}`);
		console.error(`    ${one.line}`);
		console.error(`    ^ ${one.surface.what} says ${one.said}. ${one.surface.instead}.\n`);
	}
	for (const one of copies) {
		console.error(`  ${one.where}`);
		console.error(`    ${one.line}`);
		console.error(
			`    ^ a second declaration of the step. Import SKIP_SECONDS from $lib/player/skip.\n`
		);
	}
	console.error(
		'One number decides how far a step goes; the words and the picture on the control have to\n' +
			'say the same thing. Nothing else compares them: both sides pass their own tests while\n' +
			'a button that moves ten seconds is labelled five.\n'
	);
	process.exit(1);
}

console.log(`one step: clean (${STEP} seconds, said the same way everywhere)`);
