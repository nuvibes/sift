// What every gate that reads the client's source tree shares: the walk, the comment stripper, and
// the ratchet.
//
// ## Why one file
//
// Copies of one idea drift: separate walks, strippers in incompatible spellings, and ratchets that
// each record a fall differently, so a person meeting the ratchet meets a different one each time.
//
// So: one walk, one stripper, one ratchet, and ONE baseline file (`scripts/gate-baselines.json`)
// keyed by gate. A gate is then its rule and its message, which is the part that is genuinely its
// own.
//
// ## The ratchet, in full
//
// The recorded number may only fall. A rise fails. A fall ALSO fails unless the run was asked to
// record it (`--record`), because a ceiling sitting above the truth makes the next few offences
// free: the recorded number must always be the real one. The pre-commit hook (`gates.js`) runs
// with `--record`, so a commit that improves things records its own progress and the commit is
// refused once, for the changed baseline to be staged; a CI run never records, so a baseline that
// has fallen behind is a red run that says exactly what to do.

import { readdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));

/** `frontend/`, `frontend/src`, and where the recorded numbers live. */
export const FRONTEND = join(HERE, '..', '..');
export const SOURCE = join(FRONTEND, 'src');
export const BASELINES = join(FRONTEND, 'scripts', 'gate-baselines.json');

/**
 * Every file under `dir` with one of the extensions, depth first, in directory order.
 *
 * Nothing a package manager or the framework wrote: `node_modules`, and any dot-directory.
 * A `svelte-kit sync` run from the wrong place leaves a `.svelte-kit` under `src` whose error page
 * carries colours nobody here chose. Nothing under `lib/generated` either, for the same reason.
 *
 * @param {string} dir
 * @param {string[]} [extensions]
 * @returns {Promise<string[]>}
 */
export async function everyFile(dir, extensions = ['.svelte']) {
	/** @type {string[]} */
	const found = [];
	for (const entry of await readdir(dir, { withFileTypes: true })) {
		if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
		const full = join(dir, entry.name);
		if (entry.isDirectory()) {
			if (entry.name === 'generated' && full.includes(join('src', 'lib'))) continue;
			found.push(...(await everyFile(full, extensions)));
		} else if (extensions.some((one) => entry.name.endsWith(one))) found.push(full);
	}
	return found;
}

/**
 * Every `.svelte` under `dir`. The spelling most gates want.
 *
 * @param {string} dir
 */
export const everySvelteFile = (dir) => everyFile(dir, ['.svelte']);

/**
 * A path as the gates print it: relative to `src`, forward slashes on every site.
 *
 * @param {string} path
 * @returns {string}
 */
export const fromSource = (path) => relative(SOURCE, path).replaceAll('\\', '/');

/**
 * The text with its prose taken out and its line numbers kept.
 *
 * Markup comments in the markup; block and line comments in the script and style blocks. Each is
 * replaced by spaces of the same shape (newlines kept), so an offence reported from what is left
 * still points at the real line. A line comment is matched only where it is not inside a string,
 * which `//` in a URL would otherwise trip: the first character class refuses a colon or a quote
 * before the slashes.
 *
 * ## Why the markup is not searched for a block comment
 *
 * `accept="image/*"` is markup. A stripper that reads a block opener there blanks everything up to
 * the next closing pair: on any component with a file picker, every button after it. So a
 * `.svelte` file is cut at its `<script>` and `<style>` blocks: those get the code stripper, the
 * rest gets the markup one. A file that is not Svelte is all code.
 *
 * Read whole, a gate would count sentences, including the note explaining why the shared
 * component is used instead of a bare one.
 *
 * @param {string} text
 * @param {{ markup?: boolean, block?: boolean, line?: boolean }} [options] which kinds to take out:
 *   markup comments, block comments, line comments; all three unless one is turned off.
 * @returns {string}
 */
export function withoutComments(text, options = {}) {
	return strip(text, options, () => {});
}

/**
 * Every comment `withoutComments` takes out, with where it starts: the same reading, handed back
 * instead of blanked. `offset` is into `text`; `line` counts from 1.
 *
 * @param {string} text
 * @param {{ markup?: boolean, block?: boolean, line?: boolean }} [options]
 * @returns {{ offset: number, line: number, text: string }[]}
 */
export function commentsIn(text, options = {}) {
	/** @type {{ offset: number, line: number, text: string }[]} */
	const found = [];
	strip(text, options, (offset, comment) => {
		found.push({ offset, line: text.slice(0, offset).split('\n').length, text: comment });
	});
	return found.sort((a, b) => a.offset - b.offset);
}

/**
 * The one reading behind both: each comment is reported to `seen` and replaced by spaces.
 *
 * @param {string} text
 * @param {{ markup?: boolean, block?: boolean, line?: boolean }} options
 * @param {(offset: number, comment: string) => void} seen
 * @returns {string}
 */
function strip(text, { markup = true, block = true, line = true }, seen) {
	/** @param {string} found */
	const blank = (found) => found.replace(/[^\n]/g, ' ');
	/**
	 * @param {string} part
	 * @param {number} base where `part` starts in `text`
	 */
	const code = (part, base) => {
		let out = part;
		if (block)
			out = out.replace(
				/\/\*[\s\S]*?\*\//g,
				(/** @type {string} */ found, /** @type {number} */ offset) => {
					seen(base + offset, found);
					return blank(found);
				}
			);
		if (line)
			out = out.replace(
				/(^|[^:'"`\\])\/\/[^\n]*/g,
				(/** @type {string} */ found, /** @type {string} */ lead, /** @type {number} */ offset) => {
					seen(base + offset + lead.length, found.slice(lead.length));
					return lead + blank(found.slice(lead.length));
				}
			);
		return out;
	};
	/**
	 * @param {string} part
	 * @param {number} base
	 */
	const prose = (part, base) =>
		markup
			? part.replace(
					/<!--[\s\S]*?-->/g,
					(/** @type {string} */ found, /** @type {number} */ offset) => {
						seen(base + offset, found);
						return blank(found);
					}
				)
			: part;

	// Script and style blocks are code; what is between them is markup. Anything that is not a
	// component (a .ts, a .css) has no markup and is all code.
	const BLOCK = /<(script|style)\b[^>]*>[\s\S]*?<\/\1>/g;
	if (!BLOCK.test(text)) return code(text, 0);
	BLOCK.lastIndex = 0;
	let out = '';
	let at = 0;
	for (const found of text.matchAll(BLOCK)) {
		out += prose(text.slice(at, found.index), at) + code(found[0], found.index);
		at = found.index + found[0].length;
	}
	return out + prose(text.slice(at), at);
}

/** Whether this run was asked to record a fall. */
export const recording = () => process.argv.includes('--record');

/** The marker a fallen ratchet prints, which `gates.js` reads to know a re-run with --record is the fix. */
export const RATCHET_FELL = 'RATCHET FELL';

/**
 * Compare `now` against the recorded number for `gate`.`key`, and record a fall when asked.
 *
 * Returns the complaint to print, or null when the number stands. `what` says what was counted,
 * `instead` what to do about a rise, and `offenders` are the lines listed under it. `script` is the
 * gate's own filename, for the one gate whose file is not named after its key (`check_hover_answers.js`
 * is the `hover` gate): a printed command that does not exist is worse than no command at all.
 *
 * @param {string} gate
 * @param {string} key
 * @param {number} now
 * @param {{ what: string, instead?: string, offenders?: string[], script?: string }} options
 * @returns {Promise<string | null>}
 */
export async function ratchet(
	gate,
	key,
	now,
	{ what, instead = '', offenders = [], script = `check_${gate.replaceAll('-', '_')}.js` }
) {
	const all = JSON.parse(await readFile(BASELINES, 'utf8'));
	const mine = all[gate] ?? {};
	const allowed = mine[key];

	if (allowed === undefined) {
		return `${gate}.${key}: nothing recorded in scripts/gate-baselines.json. Add ${now}.`;
	}

	if (now > allowed) {
		const list =
			offenders.length > 0
				? `\n    Where they are now:\n${offenders.map((one) => `      ${one}`).join('\n')}`
				: '';
		return `${gate}.${key}: ${now} of ${what}, and the recorded number is ${allowed}.\n    ${instead}${list}`;
	}

	if (now < allowed) {
		if (recording()) {
			all[gate] = { ...mine, [key]: now };
			await writeFile(BASELINES, `${JSON.stringify(all, null, '\t')}\n`, 'utf8');
			console.log(
				`${gate}.${key}: down to ${now} from ${allowed}, recorded in scripts/gate-baselines.json.`
			);
			return null;
		}
		return (
			`${RATCHET_FELL} ${gate}.${key}: down to ${now} from ${allowed}. Record it, so the progress\n` +
			`    cannot be given back: node scripts/${script} --record\n` +
			`    (the pre-commit hook does this for you; stage scripts/gate-baselines.json and commit again).`
		);
	}
	return null;
}

/**
 * The recorded numbers for one gate, for the gates that need to read them without comparing.
 *
 * Every gate that reads its record this way records numbers. `vocabulary` keeps a map per file
 * instead, and reads the file itself.
 *
 * @param {string} gate
 * @returns {Promise<Record<string, number>>}
 */
export async function recorded(gate) {
	return JSON.parse(await readFile(BASELINES, 'utf8'))[gate] ?? {};
}
