// Every date and time on screen is written by one file, `src/lib/shell/when.ts`.
//
// ## Why
//
// Files that write their own disagree: "September 25, 2026" beside "Sep 25, 2026", the browser's
// bare default ("9/25/2026, 3:04:05 PM", which is a different day for half the world and ignores
// the clock the reader chose under Appearance), a date with no year, and separate ladders of
// relative words ("10 minutes ago", "10m ago"). Each is right on its own terms, and a person moving
// between screens reads one moment several ways.
//
// The rule it holds is one sentence: a RECORD says when with the day ("Today 3:04:36 PM", "Sep 12,
// 2026, 11:37:05 PM"), a LIVE list says how far away ("4 minutes ago") with the moment on the
// hover. Every time carries its seconds, on the twelve-hour or twenty-four-hour clock the reader
// chose, and a Log line its milliseconds. `when.ts` explains it and is the only place it is written.
//
// ## What this counts, in code with its comments taken out (`lib/time-format.js`, which the unit
// suite drives with planted lines: `src/lib/build/time-format-gate.test.ts`)
//
// - `toLocaleDateString` / `toLocaleTimeString` anywhere, and `toDateString` / `toTimeString` /
//   `toUTCString`, which are the same thing in a fixed English nobody chose.
// - `Intl.DateTimeFormat` and `Intl.RelativeTimeFormat`.
// - `toLocaleString(` called on a Date: straight off `new Date(...)`, on a name the file bound to
//   `new Date(...)`, or with a date or time option in its arguments. A NUMBER's `toLocaleString()`
//   is the application's own way of writing a count, and is none of this gate's business.
// - A date built by hand: a template that interpolates `getFullYear()`, `getMonth()`, `getDate()`,
//   `getHours()`, `getMinutes()` or `getSeconds()`.
// - A second ladder of relative words: a number followed by "ago", or "in" and a number followed
//   by a unit ("starts in 2h"). `sayWhen` is the one ladder.
//
// ## What it cannot see, said rather than hidden
//
// A name bound to `new Date(...)` in the file, or a parameter typed `: Date`, is caught. A Date
// that arrives any other way (a property, `row.when.toLocaleString()`) is indistinguishable
// here from a number doing the same, and a gate that guessed would refuse every count in the
// application. Sift passes moments as unix SECONDS, never as Date objects, which is what keeps that
// case rare; review catches the rest.
//
// ## A ratchet
//
// The count may fall and never rise (`lib/tree.js` explains the ratchet). Every one left is listed
// with its file and line when the number is exceeded, and each is a one-line change: the moment
// goes to `onRecord`, `exactly`, `dayOf`, `timeOfDay`, `logTime` or `sayWhen`, and a file name's
// stamp to `stampForAFileName`.

import { readFile } from 'node:fs/promises';

import { datesWrittenIn } from './lib/time-format.js';
import { everyFile, fromSource, ratchet, SOURCE, withoutComments } from './lib/tree.js';

/** The one file allowed to write a date. */
const THE_FORMATTER = 'lib/shell/when.ts';

/** A test states what the formatter should say, often by asking the browser the same question. */
const A_TEST = /\.(test|spec)\.ts$/;

let count = 0;
const offenders = [];

for (const path of await everyFile(SOURCE, ['.ts', '.svelte', '.js'])) {
	const where = fromSource(path);
	if (where === THE_FORMATTER || A_TEST.test(where) || where.endsWith('.d.ts')) continue;

	for (const { line, what } of datesWrittenIn(withoutComments(await readFile(path, 'utf8')))) {
		count += 1;
		offenders.push(`${where}:${line}  ${what}`);
	}
}

const complaint = await ratchet('one-time-format', 'written', count, {
	what: 'date(s) or time(s) written outside lib/shell/when.ts',
	instead:
		'Instead: import the moment\'s form from $lib/shell/when: `onRecord` for a record ("Today\n' +
		'    3:04:36 PM"), `sayWhen` with `exactly` on the hover for a live list, `dayOf` /\n' +
		'    `calendarDay` for a day, `timeOfDay` for a time under its day, `logTime` for a Log line.\n' +
		'    when.ts says which surface gets which.',
	offenders
});

if (complaint) {
	console.error(`\n  ${complaint}\n`);
	process.exit(1);
}

console.log(`One time format: ${count} date(s) still written outside lib/when.ts.`);
