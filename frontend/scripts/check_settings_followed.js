// Preferences read from the server and then held, without ever being told they moved.
//
// ## The failure this is for
//
// Sift's settings are per ACCOUNT, so one person's look, their tile marks and their locking rules
// follow them between the desktop app, a browser and a second window. The server announces a change
// on the live connection, and `$lib/settings-ui/settings` re-reads and hands the values to whoever asked to be
// told.
//
// A reader that reads its preference once, at page load, and keeps it for the life of the page
// defeats that. On a browser tab it is nearly invisible, because a page load is a keystroke away.
// On the desktop app the page never reloads at all, so the app sits in one theme while a browser
// signed in to the same account sits in another, and the vault's idle and blur locks, a security
// control, would silently not apply until the next restart.
//
// It is invisible by construction: every one of those reads is correct, the value it got was right
// when it got it, and nothing fails. The only tell is two screens disagreeing.
//
// ## What is required
//
// A file that reads settings from the server must also be told when they move: by importing
// `onSettingsSaved` (for a store that outlives every screen) or by naming `settingChanges` (for a
// screen, which uses `whenChanged`). Which of the two is right depends on how long the thing lives,
// so this gate asks for either and leaves the choice to the file.
//
// ## The escape hatch, and why it is a marker rather than a list
//
// Some readers genuinely cannot subscribe: a plain function that holds nothing, or a class built
// once per screen whose SCREEN does the subscribing. A separate allow-list would put the reason in
// a different file from the code, where it goes stale unread. A marker in the file says why at the
// place somebody is looking, and this prints every one of them on each run so they stay visible
// rather than becoming furniture.
//
// ## What is deliberately not checked
//
// Nothing. Every reader follows, or says in its own text why something else follows for it,
// settings panes included: every control on a pane writes on the press and holds nothing unsaved,
// so a re-read can only put the same value back, while not following leaves an open pane showing a
// value that another window or another admin has changed.

import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = join(HERE, '..', 'src');

/** How a file reads preferences off the server. Either of these is a read that can go stale. */
const READS = ['fetchSettingValues', 'fetchSettings'];

/** How it asks to be told they moved: a store subscribes, a screen watches the signal. */
const FOLLOWS = ['onSettingsSaved', 'settingChanges'];

/** What a file says instead, when the thing that follows for it is somewhere else. */
const EXCUSE = 'WHY NOT FOLLOWED:';

/** The one place that does the reading and the announcing for everybody else. */
const THE_SOURCE = 'lib/settings-ui/settings.ts';

/** Every name a file imports, from anywhere. The braces of an `import {...} from '...'`, which is
 *  the only form anything here is reached by: `$lib/settings-ui/settings` and `$lib/library/changes.svelte`
 *  both export plain named functions and neither has a default. */
function imported(text) {
	const names = new Set();
	for (const [, inside] of text.matchAll(
		/import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+['"][^'"]+['"]/g
	)) {
		for (const one of inside.split(',')) {
			const name = one
				.trim()
				.split(/\s+as\s+/)[0]
				.replace(/^type\s+/, '')
				.trim();
			if (name) names.add(name);
		}
	}
	return names;
}

async function* walk(dir) {
	for (const entry of await readdir(dir, { withFileTypes: true })) {
		const full = join(dir, entry.name);
		if (entry.isDirectory()) yield* walk(full);
		else if (/\.(ts|svelte)$/.test(entry.name)) yield full;
	}
}

const offenders = [];
const excused = [];

for await (const full of walk(SOURCE)) {
	const where = relative(SOURCE, full).split('\\').join('/');
	if (where === THE_SOURCE) continue;
	if (/\.(test|spec)\.ts$/.test(where)) continue;
	const text = await readFile(full, 'utf8');
	/* WHAT IS IMPORTED, never merely what is mentioned. The root layout explains in a comment that
	   the stores it starts settle `fetchSettingValues` between them, and a gate reading the whole
	   file would call that a read. A file failing for describing the mechanism correctly is the kind
	   of false positive that gets a gate switched off. */
	const brought = imported(text);
	if (!READS.some((name) => brought.has(name))) continue;
	if (FOLLOWS.some((name) => brought.has(name))) continue;

	const said = text.indexOf(EXCUSE);
	if (said !== -1) {
		const line = text
			.slice(said + EXCUSE.length)
			.split('\n')[0]
			.trim();
		excused.push(`${where}  ${line}`);
		continue;
	}
	offenders.push(where);
}

if (offenders.length > 0) {
	console.error(
		`\n${offenders.length} file(s) read a setting and are never told it moved.\n\n` +
			`  Instead: import \`onSettingsSaved\` if it outlives every screen, or watch\n` +
			`  \`settingChanges\` with \`whenChanged\` if it is a screen. If the thing that\n` +
			`  follows for it lives somewhere else, say so in the file:\n\n` +
			`      /* ${EXCUSE} <which file subscribes on its behalf, and why here cannot> */\n`
	);
	for (const line of offenders) console.error(`    ${line}`);
	process.exit(1);
}

if (excused.length > 0) {
	console.log(`\n${excused.length} file(s) say ${EXCUSE}`);
	for (const line of excused) console.log(`  ${line}`);
}

console.log(`Settings followed: every reader is told, ${excused.length} by something else.`);
