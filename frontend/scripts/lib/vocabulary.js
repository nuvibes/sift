// The screen's words, read from `tests/gates/data/vocabulary.json`: the client half of the one
// reader.
//
// ## Why one file for both sides
//
// Two copies of one word list drift, and a word banned in one of them comes back through a
// surface the other does not read. So the words
// live in ONE data file beside the other data the gates read, and each side has ONE loader: this
// module for the client, `tests/gates/vocabulary.py` for Python.
//
// Both compile every pattern the same way (case-insensitive unless an entry says `"case": true`)
// and both prove every entry's own `example` matches, so a pattern the two regular-expression
// engines read differently fails a test on one side instead of passing a tree.
//
// `banned_everywhere` is not compiled here: it is Python-only (one pattern uses a scoped flag
// JavaScript cannot parse) and `test_banned_words.py` is its one reader.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));

/**
 * One entry of a pattern list (`retired_words`, `insider_phrases`, `spelling`, `verbs.replaces`):
 * the expression, the sentence it must match, and what to write instead. `case: true` asks for a
 * case-sensitive match; the spelling list names its replacement `american` instead of `instead`.
 *
 * @typedef {{
 *   pattern: string,
 *   example: string,
 *   case?: boolean,
 *   instead?: string,
 *   american?: string
 * }} PatternEntry
 */

/** @typedef {'retired_words' | 'insider_phrases' | 'contractions' | 'phrasing' | 'spelling' | 'typography' | 'paths' | 'lower_case_names'} PatternList The lists of entries. */

/**
 * The parts of `vocabulary.json` this side reads. The Python-only parts (`banned_everywhere`,
 * `nouns`) and the `_note` / `_why` prose are left out, because nothing here
 * reads them. `scopes` does hold its own `_why` string beside the scopes; only a scope's name is
 * ever looked up in it.
 *
 * @typedef {{
 *   wrong_words: { word: string, instead: string, why?: string }[],
 *   scoped_wrong_words: { word: string, instead: string, scope: string }[],
 *   sign_in: { screens: string[], words: string[] },
 *   scopes: Record<string, string[]>,
 *   allowed_sentences: { phrases: string[] },
 *   retired_headings: { old: string, now: string }[],
 *   verbs: {
 *     allowed: { verb: string, use: string, scope?: string }[],
 *     also_allowed: { verb: string, why: string, scope?: string }[],
 *     replaces: (PatternEntry & { instead: string })[]
 *   },
 *   proper_names: { words: string[], phrases: string[] },
 *   retired_words: PatternEntry[],
 *   insider_phrases: PatternEntry[],
 *   contractions: PatternEntry[],
 *   phrasing: PatternEntry[],
 *   spelling: PatternEntry[],
 *   typography: PatternEntry[],
 *   paths: PatternEntry[],
 *   lower_case_names: PatternEntry[]
 * }} Vocabulary
 */

/** @typedef {{ pattern: RegExp, instead: string }} Rule One check's rule, compiled. */
/** @typedef {{ found: string, instead: string }} Offence What one rule found, and the word to use. */

/** The one file, from `frontend/scripts/lib`. */
export const VOCABULARY = join(HERE, '..', '..', '..', 'tests', 'gates', 'data', 'vocabulary.json');

/**
 * The ratcheted word lists, by the name each check is recorded under.
 *
 * `phrasing` is the shapes of a sentence rather than its words: a verb nobody says for the act
 * ("take it out", "goes out"), a stand-in for the plain word ("at once"), a subject left out
 * ("Made inside ..."), a rule said as a slogan ("Restricted means never."), one pronoun for two
 * things, and a sentence over 25 words. Its count is what is left to rewrite.
 *
 * @type {PatternList[]}
 */
export const RATCHETED = ['retired_words', 'insider_phrases', 'contractions', 'phrasing'];

/**
 * The word lists held at ZERO: one occurrence anywhere fails, and nothing is recorded for them.
 *
 * `spelling` is at zero on both sides, so a count above zero is a word that came back, not a
 * rewrite still in progress, and a ratchet would let `--record` write the regression down as the
 * new number.
 *
 * `typography` (three full stops where the ellipsis character belongs, and an arrow typed as
 * "->", "<-" or "=>") starts at zero. `tests/gates/vocabulary.py` holds the same list at zero
 * over the server's copy (its `HELD_AT_ZERO`). That is a hold on what the Python copy reader
 * reads, not a claim about the whole server.
 *
 * `paths` (a place named any way but its breadcrumb, `Settings > Importing > Folders`) starts at
 * zero too; `tests/gates/test_paths_are_breadcrumbs.py` reads the same list over the documents.
 *
 * `lower_case_names` (Site, Sites, Photo Set written without the capital) is zero here and a
 * ratchet over the server's copy.
 *
 * @type {PatternList[]}
 */
export const HELD_AT_ZERO = ['spelling', 'typography', 'paths', 'lower_case_names'];

/** Every ratcheted check. `wrong_words` is the hard map, counted here over the wider reading. */
export const CHECKS = ['wrong_words', ...RATCHETED];

/** @type {Vocabulary | null} */
let loaded = null;

/**
 * The whole file, parsed once per process.
 *
 * @returns {Vocabulary}
 */
export function loadVocabulary() {
	if (loaded === null) {
		loaded = /** @type {Vocabulary} */ (JSON.parse(readFileSync(VOCABULARY, 'utf8')));
	}
	return loaded;
}

/**
 * An entry's pattern, compiled the way the Python side compiles it. `g` so every match counts.
 *
 * @param {Pick<PatternEntry, 'pattern' | 'case'>} entry
 * @returns {RegExp}
 */
export function compilePattern(entry) {
	return new RegExp(entry.pattern, entry.case ? 'g' : 'gi');
}

/** @param {string} word */
const escape = (word) => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * A whole word or phrase, case-blind, the way the Python gate matches its wrong words
 * (`tests/gates/vocabulary.py` `word_pattern`): a word boundary on each edge that IS a word
 * character, because an entry ending in punctuation ("no. ") has no boundary after it to find.
 *
 * @param {string} word
 * @returns {RegExp}
 */
export const wordPattern = (word) => {
	const head = /^\w/.test(word) ? '\\b' : '';
	const tail = /\w$/.test(word) ? '\\b' : '';
	return new RegExp(`${head}${escape(word)}${tail}`, 'gi');
};

/**
 * One named part of the interface, as the path fragments that reach it.
 *
 * @param {string} name
 * @returns {string[]}
 */
export function scope(name) {
	return loadVocabulary().scopes[name];
}

/**
 * The wrong words that apply to one file under `src`: the global map, plus its area's own.
 *
 * @param {string} where
 * @returns {Map<string, string>} each wrong word, and the word to use instead
 */
export function wrongWordsFor(where) {
	const data = loadVocabulary();
	/** @type {Map<string, string>} */
	const words = new Map(data.wrong_words.map((entry) => [entry.word, entry.instead]));
	if (data.sign_in.screens.some((screen) => where.includes(screen))) {
		for (const word of data.sign_in.words) words.delete(word);
	}
	for (const entry of data.scoped_wrong_words) {
		if (scope(entry.scope).some((fragment) => where.includes(fragment))) {
			words.set(entry.word, entry.instead);
		}
	}
	return words;
}

/** @type {Map<string, Rule[]>} */
const cache = new Map();

/**
 * A check's rules as `{ pattern, instead }`, compiled once. `wrong_words` depends on the file.
 *
 * Any other `check` names a pattern list; a name that is not one is a caller's slip, and fails
 * here as it always has.
 *
 * @param {string} check
 * @param {string} where
 * @returns {Rule[]}
 */
function rulesFor(check, where) {
	if (check === 'wrong_words') {
		return [...wrongWordsFor(where)].map(([word, instead]) => ({
			pattern: wordPattern(word),
			instead
		}));
	}
	let rules = cache.get(check);
	if (rules === undefined) {
		rules = loadVocabulary()[/** @type {PatternList} */ (check)].map((entry) => ({
			pattern: compilePattern(entry),
			instead: entry.instead ?? entry.american ?? ''
		}));
		cache.set(check, rules);
	}
	return rules;
}

/**
 * `{ found, instead }` for every occurrence of one check in one string, in the file `where`.
 *
 * A sentence argued for in `allowed_sentences` is never an offence, whatever it holds.
 *
 * @param {string} check
 * @param {string} text
 * @param {string} [where]
 * @returns {Offence[]}
 */
export function offences(check, text, where = '') {
	if (loadVocabulary().allowed_sentences.phrases.some((allowed) => text.includes(allowed))) {
		return [];
	}
	/** @type {Offence[]} */
	const out = [];
	for (const { pattern, instead } of rulesFor(check, where)) {
		pattern.lastIndex = 0;
		for (const match of text.matchAll(pattern)) out.push({ found: match[0], instead });
	}
	return out;
}

/**
 * Every word or phrase a button, a menu verb or a confirm label in the file `where` may start
 * with. A verb with a `scope` belongs to those screens only ("End" is the swap's, not a task's),
 * so a file outside it, or no file at all, does not get it.
 *
 * @param {string} [where]
 * @returns {string[]}
 */
export function verbs(where = '') {
	const table = loadVocabulary().verbs;
	return [...table.allowed, ...table.also_allowed]
		.filter((one) => !one.scope || scope(one.scope).some((fragment) => where.includes(fragment)))
		.map((one) => one.verb);
}

/**
 * Whether a control's label in the file `where` starts with an agreed verb, as a whole word or
 * phrase.
 *
 * Leading punctuation and an interpolation's gap are stepped over; case-blind, because sentence
 * case is a different rule with its own check.
 *
 * @param {string} label
 * @param {string} [where]
 * @returns {boolean}
 */
export function startsWithAVerb(label, where = '') {
	const words = label.replace(/^[^A-Za-z]+/, '').toLowerCase();
	return verbs(where).some((verb) => {
		const lowered = verb.toLowerCase();
		return words === lowered || words.startsWith(`${lowered} `) || words.startsWith(`${lowered},`);
	});
}

/**
 * Whether a control's text is its BUSY state ("Saving\u2026", "Checking\u2026") rather than
 * what it does. A button that is working says so in one word and an ellipsis while it is disabled,
 * and the verb rule, which is about the act a control names, has nothing to ask of it:
 * "Saving\u2026" is the Save button mid-press.
 *
 * @param {string} label
 */
export function isBusyLabel(label) {
	return /^\p{Lu}\p{Ll}+ing\u2026$/u.test(label.trim());
}

/**
 * What to write instead of a label's first word, when the table names it.
 *
 * @param {string} label
 * @returns {string}
 */
export function verbInstead(label) {
	for (const entry of loadVocabulary().verbs.replaces) {
		if (new RegExp(entry.pattern, 'i').test(label.trim())) return entry.instead;
	}
	return 'a verb from the table in tests/gates/data/vocabulary.json';
}

/**
 * `{ where, pattern, example }` for every pattern entry this side compiles.
 *
 * @returns {{ where: string, pattern: RegExp, example: string }[]}
 */
export function everyExample() {
	const data = loadVocabulary();
	return [
		...[...RATCHETED, ...HELD_AT_ZERO].flatMap((name) =>
			data[name].map((entry) => ({
				where: `${name}: ${entry.pattern}`,
				pattern: compilePattern(entry),
				example: entry.example
			}))
		),
		...data.verbs.replaces.map((entry) => ({
			where: `verbs.replaces: ${entry.pattern}`,
			pattern: compilePattern(entry),
			example: entry.example
		}))
	];
}

/*
 * SENTENCE CASE, the client's half of `tests/gates/vocabulary.py` (`is_a_name`,
 * `not_sentence_case`). The rule is the Python one word for word and the NAMES are the one list
 * both read (`proper_names` in `vocabulary.json`), so a name declared once is a name on both sides.
 * The Python gate reads only the registered settings' labels, so a badge or a pane heading in title
 * case is caught here or nowhere.
 */

/** A word, for judging its capital: a letter or digit, then letters, digits and inner marks. */
const A_WORD = /[A-Za-z0-9][A-Za-z0-9'\u2019./+-]*/g;

/** @param {string} word */
const shouting = (word) => word === word.toUpperCase() && word !== word.toLowerCase();

/**
 * Whether a capitalised word may stand after the first one in a sentence-case label: a name on the
 * list, an acronym in capitals, or the plural of either (`GIFs`). The stem must be two letters or
 * more, which keeps `As` and `Is` out.
 *
 * @param {string} word
 */
export function isAName(word) {
	const names = loadVocabulary().proper_names.words;
	if (names.includes(word) || shouting(word)) return true;
	// A name's possessive is the name: "the Site's name". The same rule as `is_a_name`.
	if (/['\u2019]s$/.test(word) && word.length > 2) return isAName(word.slice(0, -2));
	const stem = word.slice(0, -1);
	return word.endsWith('s') && stem.length >= 2 && (names.includes(stem) || shouting(stem));
}

/**
 * Every word after the first that is capitalised without being a name: empty when the label is
 * sentence case. Sift's names written as more than one word (`Photo Set`) are taken out first.
 *
 * @param {string} label
 * @returns {string[]}
 */
export function notSentenceCase(label) {
	let judged = label;
	for (const phrase of loadVocabulary().proper_names.phrases)
		judged = judged.replaceAll(phrase, '');
	const words = judged.match(A_WORD) ?? [];
	return words.slice(1).filter((word) => /^[A-Z]/.test(word) && !isAName(word));
}

/**
 * Whether a string reads as a LABEL (a heading, a badge, a button, a menu row), which is what
 * sentence case is judged on. Six words or fewer (the settings gate's `LABEL_WORDS`), no sentence
 * ending at the end or inside it (two sentences are prose, and the second starts with a capital),
 * and no `: ` inside, because what follows a colon is a value ("Hair color: Blonde") and may be a
 * name. A sentence is not judged: a capital in one is a name the list does not know yet.
 *
 * @param {string} text
 */
export function labelShaped(text) {
	const said = text.trim();
	return (
		said !== '' &&
		said.split(/\s+/).length <= 6 &&
		!/[.?!:]$/.test(said) &&
		!/[.?!] /.test(said) &&
		!said.includes(': ')
	);
}

/*
 * A COUNT WRITTEN RAW: a number before the word it counts, not passed through `counted()`
 * (`$lib/entity/entity-counts`), the one formatter that groups it: "12500 files" beside the walls'
 * "12,500 files". Read off the SOURCE, because the copy reader turns every substitution into a gap
 * and the question is what fills it. Two shapes: a bare substitution (`${n} files` in script,
 * `{row.count} files` in markup) whose expression is a name, a property or simple sums of them
 * rather than a call; and a run of four or more digits typed into the words ("4000 files").
 */

/** The words a number before them counts. A unit is not one of them: `px`, `%`, `MB`, `s`. */
const COUNT_WORDS =
	'files?|pictures?|photos?|videos?|clips?|people|persons?|faces?|folders?|tags?|Sites?|usernames?|' +
	'items?|tasks?|jobs?|matches|results?|loops?|collections?|Photo Sets?|downloads?|links?|guests?|' +
	'users?|rows?|shoots?|frames?|cookies?|copies|groups?|more|queued|selected';

/* Not followed by `=`: in markup `{x} links={y}` is an attribute, not a count of links. */
const RAW_GAP = new RegExp(
	String.raw`\$?\{\s*([^{}]*?)\s*\}[ \t]+(?:${COUNT_WORDS})\b(?!\s*=)`,
	'g'
);
const RAW_DIGITS = new RegExp(String.raw`(?<![\w.,/:#+-])(\d{4,})[ \t]+(?:${COUNT_WORDS})\b`, 'g');

/** A name, a property chain, or sums of them: what a substitution holds when it is the number. */
const BARE =
	/^[A-Za-z_$][\w$]*(?:\??\.[A-Za-z_$][\w$]*|\[[\w$]+\])*(?:\s*[-+]\s*(?:\d+|[A-Za-z_$][\w$.]*))*$/;

/**
 * Names that hold something a count word follows without being counted by it: a number that is
 * not a count of things (a version, a port, a pixel size, a percentage, a year), and the words a
 * Site's or a row's NAME stands in ("{site} files" is "Instagram files"). Checked against the last
 * name in the chain.
 */
const NOT_A_COUNT =
	/(?:version|port|px|pixels?|percent|year|width|height|site|label|name|href|note)$/i;

/** Words a bare substitution can be that are not a number at all. */
const NOT_A_NUMBER = new Set(['else', 'each', 'if', 'await', 'then', 'catch', 'true', 'false']);

/*
 * Two more shapes, for counts drawn without a following word ("12500" in a header, "Done4000" on
 * a tab):
 *
 * ALONE, a number drawn on its own, beside a label rather than before a word: an element whose
 * whole content is one bare substitution, which says it is a count by its class (`count`, as a
 * token) or by its name (`length`, `total`, `n`). A class is how every such pill in the app is
 * drawn, and `length` is never anything but a number.
 *
 * PAIRED, a count whose word is itself a substitution: `${count} ${many}`, `${n} ${n === 1 ?
 * 'file' : 'files'}`. The first is a count when its name says so or when the second asks whether it
 * is one; a unit (stars, minutes) is a measure, not a count of things. In markup the pair is also
 * how attributes are written (`{size} {tone}`), so there it counts only when the second is a choice
 * (`?`).
 */
const ALONE = /<([a-z][\w-]*)\b([^<>]*?)>\s*\{\s*([^{}]*?)\s*\}\s*<\/\1\s*>/g;
const PAIRED = /(\$?)\{\s*([^{}]*?)\s*\}[ \t]+\$?\{([^{}]*)\}/g;

/** Names that are only ever a number of things. */
const NUMBER_NAMES = /^(?:n|count|total|length|skipped|changed|offered)$/;

/** What a number before it measures rather than counts. */
const UNITS = /\b(?:stars?|seconds?|minutes?|hours?|days?)\b/;

/**
 * The last name in a chain: `row.match.count` is `count`.
 *
 * @param {string} expression
 */
const lastName = (expression) =>
	expression
		.split(/[.\s+-]/)
		.filter(Boolean)
		.pop() ?? '';

/**
 * A bare substitution that could be a number: not copy (`COPY.x`), not a name or a size.
 *
 * @param {string} expression
 */
const couldBeANumber = (expression) =>
	BARE.test(expression) &&
	!NOT_A_NUMBER.has(expression) &&
	!/^COPY\b/.test(expression) &&
	!NOT_A_COUNT.test(lastName(expression));

/**
 * `{ offset, found }` for every count written raw in one file's source, comments already taken out.
 *
 * @param {string} source
 * @returns {{ offset: number, found: string }[]}
 */
export function rawCounts(source) {
	/** @type {{ offset: number, found: string }[]} */
	const out = [];
	for (const match of source.matchAll(RAW_GAP)) {
		const expression = match[1] ?? '';
		const last =
			expression
				.split(/[.\s+-]/)
				.filter(Boolean)
				.pop() ?? '';
		if (!BARE.test(expression) || NOT_A_NUMBER.has(expression) || NOT_A_COUNT.test(last)) continue;
		out.push({ offset: match.index ?? 0, found: match[0] });
	}
	for (const match of source.matchAll(RAW_DIGITS)) {
		out.push({ offset: match.index ?? 0, found: match[0] });
	}
	for (const match of source.matchAll(ALONE)) {
		const expression = match[3] ?? '';
		const last = lastName(expression);
		if (!couldBeANumber(expression) || /^(?:value|rating)$/.test(last)) continue;
		const classes = (/\bclass="([^"]*)"/.exec(match[2] ?? '')?.[1] ?? '').split(/\s+/);
		if (!classes.includes('count') && !/^(?:n|total|length)$/.test(last)) continue;
		out.push({ offset: match.index ?? 0, found: match[0] });
	}
	for (const match of source.matchAll(PAIRED)) {
		const [, dollar, expression = '', second = ''] = match;
		if (!couldBeANumber(expression) || UNITS.test(second)) continue;
		if (!dollar && !second.includes('?')) continue;
		const asked = new RegExp(`^\\s*${expression.replace(/[.$?[\]]/g, '\\$&')}\\s*[=!<>]`);
		if (!NUMBER_NAMES.test(lastName(expression)) && !asked.test(second)) continue;
		out.push({ offset: match.index ?? 0, found: match[0] });
	}
	return out;
}
