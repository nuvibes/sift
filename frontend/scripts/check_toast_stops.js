#!/usr/bin/env node
/*
 * EVERY TOAST, CLIENT AND SERVER, READ FOR ITS PUNCTUATION AND ITS NAMES.
 *
 * One sentence carries no full stop; no semicolon, no comma splicing two sentences, no stray
 * space or double hyphen; and a thing a toast names is a piece with its kind and id (the shape a
 * History line is drawn from, `toast-pieces.ts`), never its name written into the words. The
 * reading is `lib/toast-words.js`. The server's half is every literal a router sets a `said` from.
 *
 * A TOAST OF ONE SENTENCE CARRIES NO FULL STOP.
 *
 * ## The rule, and why it is a gate as well as a runtime rule
 *
 * `Toasts.show` takes a lone terminal stop off a message that holds a single sentence, and it has
 * to: a refusal the server explains arrives as `detail` and goes through the same door, so a rule
 * applied only where the sentences are WRITTEN would be a rule the server's own words walked past.
 * That half is in `src/lib/shell/toasts.svelte.ts` and it is what people actually see.
 *
 * This is the other half: that the SOURCE reads as the screen. Without a gate a new call site is
 * written with a stop, looks exactly like its neighbours in review, and is silently corrected at
 * runtime. So the file says one thing and the application another, with nothing to notice it.
 *
 * ## What counts as one sentence
 *
 * The same test the runtime uses, deliberately spelled the same way: no `. ` / `? ` / `! ` boundary
 * INSIDE the message. A trailing `...` is not a full stop (it is a message saying work is still
 * going on), and a question mark or an exclamation is left alone, because those carry meaning a
 * stop does not.
 *
 * ## What it can and cannot read
 *
 * Only a message written as ONE literal at the call site: `toasts.show('...')`,
 * `toasts.show(`...`)` and the same as the second argument of `toasts.settle`. A message built out
 * of a ternary, a concatenation or a variable is not judged here (guessing at one would either
 * pass everything or produce failures nobody can act on), and every one of those still goes
 * through `show`, which applies the rule for real. This catches the shape that is nearly all of
 * them.
 */
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import {
	clientToasts,
	messagesOf,
	namesAsWords,
	punctuation,
	serverToasts,
	wouldBeCorrected
} from './lib/toast-words.js';
import { FRONTEND, SOURCE, everyFile, fromSource } from './lib/tree.js';

/*
 * A known positive for each rule before anything is read off the disk: a reader that matches
 * nothing reports every file clean.
 */
const KNOWN = [
	['Link copied.', 'a full stop on one sentence'],
	['Tagged ${count} files.', 'a full stop on one sentence'],
	['Exact matches are filled in; the rest waits', 'a semicolon'],
	['That was saved, it is in Activity', 'a comma splicing two sentences'],
	["${n} deleted, ${m} couldn't be", 'a comma splicing two sentences'],
	['Saved as "x" , in the panel', 'a stray space']
];
for (const [bad, rule] of KNOWN) {
	if (!punctuation(bad).includes(rule)) {
		console.error(`The toast reader no longer finds ${rule} in: ${bad}`);
		process.exit(1);
	}
}
for (const fine of [
	'Link copied',
	'Downloading...',
	'Downloaded. Drag it in from your downloads.',
	'Is that the right file?',
	"Kept local, so it isn't offered",
	''
]) {
	if (punctuation(fine).length > 0 || wouldBeCorrected(fine)) {
		console.error(`The toast reader fires on a message that is already right: ${fine}`);
		process.exit(1);
	}
}
if (
	namesAsWords('Already in ${collection?.name ?? "that"}').length !== 1 ||
	messagesOf("a ? 'One.' : `${n} more.`").length !== 2 ||
	messagesOf("['Added to ', thing('tag', id, name), '.']")[0] !== 'Added to ${}.'
) {
	console.error('The toast reader no longer reads a name, a ternary or a list of pieces.');
	process.exit(1);
}

/*
 * NAMES SAID AS WORDS ON PURPOSE, by file and by what is read, each with its reason: a thing
 * with no page, one that has gone, or one the person is looking at as the toast is said.
 */
const PLAIN = {
	// The existing row a 409 names; the answer carries no id.
	'lib/components/entity/wall-verbs.svelte.ts': [],
	// A thing that was not made has no page.
	'lib/components/common/PickDialog.svelte': ['name'],
	// Saved filters have no page; they are opened from their own panel.
	'lib/components/shell/SavedFilters.svelte': ['kept.name'],
	// A user has no page; the toast is said on that user's own row.
	'lib/settings-ui/Users.svelte': ['user.username'],
	// A folder that was just deleted, and a file saved out of Sift to the device.
	'lib/library/FolderExplorer.svelte': ['folder.name'],
	'lib/library/library.svelte.ts': ['root.name'],
	'lib/player/snapshot.ts': ['name'],
	// A name a folder may be, proposed: not a thing until it is answered.
	'lib/components/suggestions/FolderSuggestions.svelte': ['named(row)'],
	// A stash-box is named, not a thing with a page in the library.
	'lib/components/organize/FileMatches.svelte': ['one.box_name']
};

const complaints = [];
let read = 0;

for (const full of await everyFile(SOURCE, ['.svelte', '.ts'])) {
	const where = fromSource(full);
	// A test writes whatever sentence it needs in order to prove what `show` does with one.
	if (where.includes('.test.') || where.startsWith('routes/design/')) continue;
	const text = await readFile(full, 'utf8');
	if (!text.includes('toasts.') && !text.includes('decided(')) continue;
	for (const { line, messages } of clientToasts(text)) {
		for (const message of messages) {
			read += 1;
			for (const rule of punctuation(message))
				complaints.push(`src/${where}:${line}  ${rule}: ${message}`);
			for (const name of namesAsWords(message)) {
				if ((PLAIN[where] ?? []).includes(name)) continue;
				complaints.push(
					`src/${where}:${line}  a name said as words (\${${name}}): make it a piece`
				);
			}
		}
	}
}

const SLICES = join(FRONTEND, '..', 'src', 'sift', 'slices');
let server = 0;
for (const full of (await everyFile(SLICES, ['.py'])).filter((one) =>
	/(^|[\\/])[a-z_]*router[a-z_]*\.py$/.test(one)
)) {
	const where = full.slice(SLICES.length + 1).replaceAll('\\', '/');
	for (const { line, message, pieces } of serverToasts(await readFile(full, 'utf8'))) {
		server += 1;
		// A server sentence is built in parts and its stop taken off by the toast, so only the
		// rules that hold for a part are read here.
		for (const rule of punctuation(message, { stops: false }))
			complaints.push(`src/sift/slices/${where}:${line}  ${rule}: ${message}`);
		if (!pieces && namesAsWords(message).length > 0)
			complaints.push(
				`src/sift/slices/${where}:${line}  a name said as words with no pieces beside it: ${message}`
			);
	}
}

if (complaints.length > 0) {
	console.error(
		"These toasts read wrong, or say a thing's name as words where its piece would carry its id:\n"
	);
	for (const one of complaints) console.error('  ' + one);
	console.error(
		'\nOne sentence takes no full stop; two sentences, not a semicolon or a comma, carry two ideas.\n' +
			'A thing a toast names is a piece (`thing(kind, id, name)` in `toast-pieces.ts`), or on the\n' +
			'server a `pieces=` line beside `said`; a name with no page goes in PLAIN with its reason.'
	);
	process.exit(1);
}

/* Floors far below the real counts: they catch a walk or a reader that stopped matching. */
if (read < 300 || server < 5) {
	console.error(
		`Only ${read} client toasts and ${server} server sentences were read; the reader has stopped matching.`
	);
	process.exit(1);
}

console.log(
	`every toast reads right and names its things as pieces (${read} client, ${server} server)`
);
