import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { copyIn } from '../../../scripts/lib/copy.js';
import {
	compilePattern,
	everyExample,
	isBusyLabel,
	labelShaped,
	loadVocabulary,
	notSentenceCase,
	offences,
	rawCounts,
	scope,
	startsWithAVerb,
	wordPattern
} from '../../../scripts/lib/vocabulary.js';
import { compare, heldAtZero } from '../../../scripts/check_vocabulary.js';

/* One name per thing, and the name for where media came from is "Site". */

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

/** The word the app uses, and the words that must not stand in for it. */
const CORRECT = 'Site';
const SITE_WORDS = loadVocabulary()
	.wrong_words.filter((entry: { instead: string }) => /^Sites?$/.test(entry.instead))
	.map((entry: { word: string }) => entry.word);
const FORBIDDEN = new RegExp(`^(?:${SITE_WORDS.join('|')})$`, 'i');

/** Props whose value IS a name shown to somebody. */
const NAMING_PROPS = ['label', 'noun', 'title', 'heading', 'deleteWord', 'what'];

/** `label: 'Platforms'` and `noun="platforms"` alike: literal or attribute, either quote. */
const NAMED = new RegExp(`\\b(${NAMING_PROPS.join('|')})\\s*[:=]\\s*['"]([^'"]*)['"]`, 'g');

/** A heading whose entire contents are one bare word. */
const BARE_HEADING = /<h[1-6][^>]*>\s*([A-Za-z]+)\s*<\/h[1-6]>/g;

function filesUnder(directory: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(directory)) {
		const path = join(directory, entry);
		if (statSync(path).isDirectory()) {
			if (entry !== 'node_modules') found.push(...filesUnder(path));
			continue;
		}
		// The generated client description is not written by anybody, and a test double is not a
		// screen.
		if (entry === 'schema.d.ts' || entry.includes('.test.')) continue;
		if (entry.endsWith('.svelte') || entry.endsWith('.ts')) found.push(path);
	}
	return found;
}

/** Every `prop: 'value'` naming pair in one file, with the line it is on. */
function namesIn(source: string): { prop: string; value: string; line: number }[] {
	const found: { prop: string; value: string; line: number }[] = [];
	for (const match of source.matchAll(NAMED)) {
		found.push({
			prop: match[1],
			value: match[2],
			line: source.slice(0, match.index).split('\n').length
		});
	}
	for (const match of source.matchAll(BARE_HEADING)) {
		found.push({
			prop: 'heading',
			value: match[1],
			line: source.slice(0, match.index).split('\n').length
		});
	}
	return found;
}

describe('where media came from is called a Site, everywhere it is named', () => {
	const files = filesUnder(SOURCE);

	it('finds files to read, so a broken walk cannot pass as a clean tree', () => {
		expect(files.length).toBeGreaterThan(100);
	});

	it('reads the props it claims to read, proved against a known bad string', () => {
		const found = namesIn(`const KINDS = [{ kind: 'site', label: 'Platforms' }];`);
		expect(found).toEqual([{ prop: 'label', value: 'Platforms', line: 1 }]);
		expect(FORBIDDEN.test(found[0].value)).toBe(true);
	});

	it('names no on-screen thing "Platform" or "Platforms"', () => {
		const wrong: string[] = [];
		for (const file of files) {
			for (const { prop, value, line } of namesIn(readFileSync(file, 'utf8'))) {
				if (!FORBIDDEN.test(value)) continue;
				wrong.push(`${relative(SOURCE, file)}:${line} — ${prop}="${value}", want "${CORRECT}"`);
			}
		}
		expect(wrong, `\n${wrong.join('\n')}\n`).toEqual([]);
	});
});

/* AND THE THING A SITE WANTS BEFORE IT WILL SHOW ITS FILES IS CALLED COOKIES. */

/** The screens whose whole subject is what a Site wants before it hands over a file, read from
 *  the vocabulary file's `scopes`, the same list the Python gate reads. */
const COOKIE_SCREENS: string[] = scope('cookie_screens');

/** The words that may not name anything on them: the vocabulary's cookie-screen words that are
 * one word long (the phrases are the Python gate's, which reads sentences). */
const COOKIE_WORDS: string[] = loadVocabulary()
	.scoped_wrong_words.filter(
		(entry: { scope: string; word: string }) =>
			entry.scope === 'cookie_screens' && !entry.word.includes(' ')
	)
	.map((entry: { word: string }) => entry.word);
const NOT_A_COOKIE = new RegExp(`\\b(?:${COOKIE_WORDS.join('|')})\\b`, 'i');

function onACookieScreen(path: string): boolean {
	const where = relative(SOURCE, path).split('\\').join('/');
	return COOKIE_SCREENS.some((screen) => where.includes(screen));
}

describe('what a Site wants before it shows its files is called cookies', () => {
	const screens = filesUnder(SOURCE).filter(onACookieScreen);

	it('finds the screens, so a walk that matched nothing cannot pass as a clean tree', () => {
		expect(screens.length).toBeGreaterThan(2);
	});

	it('reads a known bad string, so the rule is shown to bite', () => {
		const found = namesIn(`<Field title="Add a login" />`);
		expect(found).toEqual([{ prop: 'title', value: 'Add a login', line: 1 }]);
		expect(NOT_A_COOKIE.test(found[0].value)).toBe(true);
	});

	it('leaves the words those screens are being renamed to alone', () => {
		for (const value of ['Cookies', 'Add cookies', 'Waiting for cookies', 'Cookies expired']) {
			expect(NOT_A_COOKIE.test(value), value).toBe(false);
		}
	});

	it('names nothing on them a login', () => {
		const wrong: string[] = [];
		for (const file of screens) {
			for (const { prop, value, line } of namesIn(readFileSync(file, 'utf8'))) {
				if (!NOT_A_COOKIE.test(value)) continue;
				wrong.push(`${relative(SOURCE, file)}:${line} — ${prop}="${value}", want "cookies"`);
			}
		}
		expect(wrong, `\n${wrong.join('\n')}\n`).toEqual([]);
	});
});

/* A HEADING THAT WAS RENAMED STAYS RENAMED. The strip of files a model finds alike under a
 * file's record is headed "Similar to this", not "Looks like this". */
const RETIRED_HEADINGS: { old: string; now: string }[] = loadVocabulary().retired_headings;

/** Every place `heading` stands alone as a quoted string or as an element's whole text. */
function wholeStringsIn(source: string, heading: string): number[] {
	const exact = new RegExp(`(['"\`>])\\s*${heading}\\s*(['"\`<])`, 'g');
	return [...source.matchAll(exact)].map(
		(match) => source.slice(0, match.index).split('\n').length
	);
}

describe('a renamed heading is not written again', () => {
	const files = filesUnder(SOURCE);

	it('reads a known bad string, so the rule is shown to bite', () => {
		const shipped = `const heading = $derived(tier === 'looks' ? 'Looks like this' : 'Similar to this');`;
		expect(wholeStringsIn(shipped, 'Looks like this')).toEqual([1]);
		expect(wholeStringsIn('<h3>Looks like this</h3>', 'Looks like this')).toEqual([1]);
	});

	it('leaves the phrase alone inside a sentence', () => {
		expect(wholeStringsIn('/* What else looks like this file. */', 'Looks like this')).toEqual([]);
		expect(wholeStringsIn(`'Looks like this file, roughly'`, 'Looks like this')).toEqual([]);
	});

	for (const { old, now } of RETIRED_HEADINGS) {
		it(`writes "${now}", never "${old}"`, () => {
			const wrong: string[] = [];
			for (const file of files) {
				for (const line of wholeStringsIn(readFileSync(file, 'utf8'), old)) {
					wrong.push(`${relative(SOURCE, file)}:${line} — "${old}", want "${now}"`);
				}
			}
			expect(wrong, `\n${wrong.join('\n')}\n`).toEqual([]);
		});
	}
});

/* THE ONE LIST, READ THE SAME WAY ON BOTH SIDES. */
describe('the vocabulary file is read the same way here as in Python', () => {
	it('matches every entry to its own example', () => {
		const missed = everyExample()
			.filter(({ pattern, example }) => {
				pattern.lastIndex = 0;
				return !pattern.test(example);
			})
			.map(({ where }) => where);
		expect(missed).toEqual([]);
	});

	it('compiles case-blind unless an entry asks otherwise', () => {
		expect(compilePattern({ pattern: '\\bus\\b', case: true }).test('US')).toBe(false);
		expect(compilePattern({ pattern: '\\bjobs?\\b' }).test('The Jobs screen')).toBe(true);
	});

	it('counts each check on a known sentence, and passes the agreed words', () => {
		expect(offences('wrong_words', 'Sift fetched this file from the web')).toHaveLength(1);
		expect(offences('retired_words', 'Share of the machine to use')).toHaveLength(1);
		expect(offences('insider_phrases', 'Settled at the workbench')).toHaveLength(2);
		expect(offences('spelling', 'Accent colour')).toHaveLength(1);
		for (const check of ['wrong_words', 'retired_words', 'insider_phrases', 'spelling']) {
			expect(offences(check, 'Share of this device to use. Accent color.'), check).toEqual([]);
		}
	});

	it('names the contraction for each uncontracted form, and never reads a query or a sentence end', () => {
		expect(offences('contractions', 'Sift cannot reach the folder')).toEqual([
			{ found: 'cannot', instead: "can't" }
		]);
		expect(offences('contractions', 'Could not load the log.')).toEqual([
			{ found: 'Could not', instead: "couldn't" }
		]);
		// "It is not" is one offence, the "is not", not two.
		expect(offences('contractions', 'It is not saved').map((one) => one.found)).toEqual(['is not']);
		expect(offences('contractions', 'WHEN seen IS NOT NULL THEN 1')).toEqual([]);
		expect(offences('contractions', 'Leave it where it is.')).toEqual([]);
		expect(offences('contractions', "Sift can't reach the folder")).toEqual([]);
	});

	it('applies a scoped word only on its screens, and the sign-in exception only on its own', () => {
		expect(
			offences('wrong_words', 'Remove this face', 'lib/components/faces/X.svelte')
		).toHaveLength(1);
		expect(offences('wrong_words', 'Remove this tag', 'lib/components/common/Chip.svelte')).toEqual(
			[]
		);
		expect(offences('wrong_words', 'Your account', 'routes/login/+page.svelte')).toEqual([]);
		expect(offences('wrong_words', 'Your account', 'routes/browse/+page.svelte')).toHaveLength(1);
		expect(wordPattern('set aside').test('Nothing has been set aside.')).toBe(true);
	});

	it('holds the filter, GIF and benchmark words on screen and leaves the layout and the stored kind alone', () => {
		const wrong: [string, string][] = [
			['Nothing to narrow by here', 'narrow'],
			['The files are narrowed to all of: Jane Else', 'narrowed'],
			['A shoot is a run of photos from one sitting', 'sitting'],
			['Save animations as', 'animations'],
			['Turn part of a video into an animation', 'animation'],
			['Leave nothing', 'Leave nothing'],
			['Measuring this device', 'Measuring this device'],
			['Your usual browser', 'usual browser']
		];
		for (const [sentence, word] of wrong) {
			expect(
				offences('wrong_words', sentence).map((one) => one.found),
				sentence
			).toEqual([word]);
		}
		for (const right of [
			'Nothing to filter by here',
			'Save GIFs as',
			'Show nothing',
			'Benchmarking this device',
			'Default browser'
		]) {
			expect(offences('wrong_words', right), right).toEqual([]);
		}
		/* A class, a stored kind compared in code and a function name are not copy; the words
		   between the tags are. */
		const svelte = `<script lang="ts">
	let { kind }: { kind: string } = $props();
	const still = kind === 'animation';
	function narrow() {}
</script>
<div class="narrow" data-kind="animation" onclick={narrow}>{still ? 'Photo' : 'GIF'}</div>`;
		const copy = copyIn(svelte, 'lib/X.svelte').map((one: { text: string }) => one.text);
		expect(copy).toContain('GIF');
		expect(copy.flatMap((text: string) => offences('wrong_words', text))).toEqual([]);
	});

	it('holds every form of arrive on screen, where a file is imported', () => {
		const wrong: [string, string][] = [
			['New questions appear here as files arrive', 'arrive'],
			['It arrives with the next Sift update', 'arrives'],
			['142 files arrived this month', 'arrived'],
			['A file arriving, or a task running', 'arriving'],
			['The day of its arrival', 'arrival'],
			['Arrivals', 'Arrivals']
		];
		for (const [sentence, word] of wrong) {
			expect(
				offences('wrong_words', sentence).map((one) => one.found),
				sentence
			).toEqual([word]);
		}
		for (const right of [
			'New questions appear here as files are imported',
			'5 files were imported yesterday',
			'newerArrived'
		]) {
			expect(offences('wrong_words', right), right).toEqual([]);
		}
	});

	it('knows a verb from a noun at the start of a control', () => {
		for (const label of [
			'Delete permanently',
			'Try again',
			'Turn on previews',
			'Keep all 3 files',
			'Save',
			// Take is accepting what another source offers (Take theirs, Take these); the wrong
			// word is taking something OFF, which `verbs.replaces` sends to Remove.
			'Take theirs'
		]) {
			expect(startsWithAVerb(label, 'lib/components/organize/ReconcilePanel.svelte'), label).toBe(
				true
			);
		}
		for (const label of ['Settle it', 'Put it back', 'Rebuild them', 'OK', 'Trying']) {
			expect(startsWithAVerb(label), label).toBe(false);
		}
	});
});

/* THE WIDENED READER SEES WHAT A NARROWER ONE CANNOT, AND NOTHING THAT IS NOT COPY. */
describe('the copy reader reads every string a person sees, and only those', () => {
	const read = (source: string, where = 'lib/X.svelte') =>
		copyIn(source, where).map((one: { text: string }) => one.text);

	it("reads a control's label through its copy module, so the verb check sees it", () => {
		const svelte = `<script lang="ts">
	import { COPY } from './X.search';
</script>
<Button onclick={go}>{COPY.scan.action}</Button>
<ConfirmDialog confirmLabel={COPY.confirm} />
<p>{COPY.scan.help}</p>`;
		const module = `export const COPY = { scan: { action: 'Settle it', help: 'Reads folders.' }, confirm: 'Take it' };`;
		const found = copyIn(svelte, 'lib/settings-ui/X.svelte', () => module) as {
			text: string;
			control: boolean;
		}[];
		expect(found.filter((one) => one.control).map((one) => one.text)).toEqual([
			'Settle it',
			'Take it'
		]);
		// The same markup with no way to read the module falls back to reading the expression as data.
		expect(copyIn(svelte, 'lib/settings-ui/X.svelte').some((one) => one.control)).toBe(false);
	});

	it('reads the shapes the word gates missed', () => {
		const svelte = `<script lang="ts">
	const LABELS = { canceled: 'Canceled', running: 'In progress' };
	toasts.show(ok ? 'Link copied' : 'Could not copy the link');
</script>
<ConfirmDialog consequence="What has been fetched so far is thrown away." confirmLabel="Stop it" />
<p>{busy ? 'Saving the file' : ''}</p>`;
		expect(read(svelte)).toEqual([
			'Canceled',
			'In progress',
			'Link copied',
			'Could not copy the link',
			'What has been fetched so far is thrown away.',
			'Stop it',
			'Saving the file'
		]);
		expect(read(`export function sayWhen() { return 'just now'; }`, 'lib/shell/when.ts')).toEqual([
			'just now'
		]);
	});

	it('leaves code alone: classes, comparisons, imports, keys, search keywords, logs and comments', () => {
		const svelte = `<script lang="ts">
	import X from '$lib/some thing';
	const cls = 'grid wide';
	if (state === 'Waiting for you') go();
	console.log('a debug line for the log');
	const entry = { keywords: 'cookies login account', icon: 'close' };
</script>
<!-- a comment with words in it -->
<div class={wide ? 'big wide' : 'small'} data-testid="the row"></div>`;
		expect(read(svelte)).toEqual([]);
	});

	it('marks only what names a control, and only its first words', () => {
		const svelte = `<Button onclick={go}>Show {count} more</Button>
<Button onclick={go}>Delete <strong>everything</strong></Button>
<Button onclick={go}>{count} more</Button>
<ConfirmDialog confirmLabel="Cancel task" />
<script lang="ts">const verbs = [{ label: 'Remove tag', run: () => go() }, { label: 'Tags' }];</script>`;
		const controls = copyIn(svelte, 'lib/X.svelte')
			.filter((one: { control: boolean }) => one.control)
			.map((one: { text: string }) => one.text);
		expect(controls).toEqual(['Remove tag', 'Show', 'Delete', 'Cancel task']);
	});

	it('reads a value of words and substitutions as one string, never its last word alone', () => {
		/* "Move {entry} up" read as "Move" and a bare "up" would have the verb check refuse the
		   "up": the value is one label with a gap where the substitution is. */
		const svelte = `<Button aria-label="Move {entry} up" onclick={go} />
<Button aria-label="{count} more" onclick={go} />`;
		const controls = copyIn(svelte, 'lib/X.svelte')
			.filter((one: { control: boolean }) => one.control)
			.map((one: { text: string }) => one.text);
		expect(controls).toEqual(['Move   up', ' more']);
	});

	it('reads every string in a copy module, however short, and nothing beside it', () => {
		/* A pane's words moved into its copy module must not leave the word checks: 'Tonight'
		   under a key that names no words, and one word long, would be read by nobody. */
		const module = `export const COPY = { title: 'Tasks', run: { action: 'Generate', short: 'Tonight' } } as const;
const other = { mode: 'Tonight' };`;
		expect(read(module, 'lib/settings-ui/X.search.ts')).toEqual(['Tasks', 'Generate', 'Tonight']);
	});

	it('reports a file it cannot parse, rather than reading it as a file with nothing to say', () => {
		expect(copyIn('<p>{unclosed</p>', 'lib/Broken.svelte')[0].kind).toBe('unreadable');
	});
});

describe('the vocabulary ratchet sees a rise and a fall, per file', () => {
	it('compares file by file, holding a file with no entry at zero', () => {
		expect(compare({ 'a.svelte': 3, 'b.ts': 1 }, { 'a.svelte': 2, 'c.ts': 4 })).toEqual({
			rose: ['a.svelte: 3, recorded 2', 'b.ts: 1, recorded 0'],
			fell: ['c.ts: 0, recorded 4']
		});
		expect(compare({ 'a.svelte': 2 }, { 'a.svelte': 2 })).toEqual({ rose: [], fell: [] });
	});
});

describe('three full stops are held at zero, not ratcheted', () => {
	it('finds "Merge into..." and passes the ellipsis character and a spread', () => {
		expect(offences('typography', 'Merge into...')).toHaveLength(1);
		expect(offences('typography', 'Selecting... 40 files')).toHaveLength(1);
		expect(offences('typography', 'Merge into\u2026')).toEqual([]);
		expect(offences('typography', '...rest')).toEqual([]);
	});

	it('finds an arrow typed as "->", "<-" or "=>" and passes the arrow character', () => {
		expect(offences('typography', '12 -> 12')).toHaveLength(1);
		expect(offences('typography', 'Back <- here')).toHaveLength(1);
		expect(offences('typography', 'Tags => Sites')).toHaveLength(1);
		expect(offences('typography', '12 \u2192 12')).toEqual([]);
		expect(offences('typography', 'A \u2014 B, and <!-- nothing -->')).toEqual([]);
	});

	it('reads a run of markup text that is only numbers and an arrow', () => {
		// "12 -> 12" has no word in it, and a reader that wanted two letters never met the arrow.
		const texts = copyIn('<p>{a} -&gt; {b}</p>', 'lib/X.svelte').map((one) => one.text);
		expect(texts.some((one) => one.includes('->'))).toBe(true);
	});

	it('refuses one whatever the baselines allow', () => {
		const dots = {
			check: 'typography',
			file: 'lib/X.svelte',
			line: 3,
			found: '...',
			instead: 'the ellipsis character',
			text: 'Merge into...'
		};
		expect(heldAtZero([dots], {})).toHaveLength(1);
	});
});

describe('British spelling is held at zero, not ratcheted', () => {
	const colour = {
		check: 'spelling',
		file: 'lib/X.svelte',
		line: 3,
		found: 'colour',
		instead: 'color',
		text: 'Accent colour'
	};

	it('refuses one British word, whatever the baselines allow', () => {
		const said = heldAtZero([colour], { spelling: { 'lib/X.svelte': 5 } });
		expect(said.some((line) => line.includes('lib/X.svelte:3'))).toBe(true);
		expect(heldAtZero([colour], {})).toHaveLength(1);
	});

	it('refuses a baseline written for it, so a number cannot read as an allowance', () => {
		expect(heldAtZero([], { spelling: {} })).toHaveLength(1);
	});

	it('says nothing about a clean tree, or about a ratcheted check', () => {
		expect(heldAtZero([], { retired_words: { 'lib/X.svelte': 2 } })).toEqual([]);
		expect(heldAtZero([{ ...colour, check: 'retired_words' }], {})).toEqual([]);
	});
});

describe('a label is sentence case, on the client as in the settings gate', () => {
	it('finds the capital that is not a name, and passes the ones that are', () => {
		expect(notSentenceCase('In Progress')).toEqual(['Progress']);
		expect(notSentenceCase('Database Switcher')).toEqual(['Switcher']);
		expect(notSentenceCase('In progress')).toEqual([]);
		expect(notSentenceCase('Add to Photo Sets')).toEqual([]);
		expect(notSentenceCase('Export GIFs to Theater')).toEqual([]);
		expect(notSentenceCase("The Site's name")).toEqual([]);
		expect(notSentenceCase("The Studio's name")).toEqual(["Studio's"]);
	});

	it('judges labels, not sentences or a value after a colon', () => {
		expect(labelShaped('Database Switcher')).toBe(true);
		expect(labelShaped('Sift opens one library at a time.')).toBe(false);
		expect(labelShaped('Hair color: Blonde')).toBe(false);
		expect(labelShaped('one two three four five six seven')).toBe(false);
	});
});

describe('a count before the word it counts goes through counted()', () => {
	const found = (source: string) => rawCounts(source).map((one) => one.found);

	it('finds a bare substitution in script and in markup, and a typed run of digits', () => {
		expect(found('`${n} files`')).toEqual(['${n} files']);
		expect(found('<span>{row.count} files</span>')).toEqual(['{row.count} files']);
		expect(found('`${group.files.length - 6} more`')).toEqual(['${group.files.length - 6} more']);
		expect(found("'2151 files'")).toEqual(['2151 files']);
	});

	it('finds a number drawn alone beside a label, by its class or its name', () => {
		expect(found('<span class="count">{row.count}</span>')).toEqual([
			'<span class="count">{row.count}</span>'
		]);
		expect(found('<strong>{names.length}</strong>')).toEqual(['<strong>{names.length}</strong>']);
		expect(found('<span class="count">{counted(row.count)}</span>')).toEqual([]);
		// Already words, or not a count: a pre-grouped "1,200 of 5,000", a star, copy.
		expect(found('<span class="pass-count">{line.part.count}</span>')).toEqual([]);
		expect(found('<span class="count">{value}</span>')).toEqual([]);
		// A note is a sentence with its count already grouped inside it, in the figure's tabular class.
		expect(found('<span class="count">{scanNote}</span>')).toEqual([]);
		expect(found('<th>{COPY.report.total}</th>')).toEqual([]);
	});

	it('finds a count whose word is itself a substitution', () => {
		expect(found('`${count} ${many}`')).toEqual(['${count} ${many}']);
		expect(found("`${done.changed} ${done.changed === 1 ? 'item' : 'items'}`")).toEqual([
			"${done.changed} ${done.changed === 1 ? 'item' : 'items'}"
		]);
		expect(found("{pageGoing.length} {pageGoing.length === 1 ? 'copy' : 'copies'}")).toHaveLength(
			1
		);
		expect(found('`${counted(count)} ${many}`')).toEqual([]);
		// Attributes, a phrase already said, and a unit are not a count of things.
		expect(found('<Chip {size} {shape} {tone} />')).toEqual([]);
		expect(found("`${files} ${single ? 'was' : 'were'} tagged`")).toEqual([]);
		expect(found("`${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`")).toEqual([]);
	});

	it('leaves no count raw on the screens that draw one', () => {
		const here = dirname(fileURLToPath(import.meta.url));
		const lib = resolve(here, '..');
		for (const file of [
			'components/EntityBand.svelte',
			'components/shell/SearchBox.svelte',
			'components/shell/SearchSuggestions.svelte',
			'settings-ui/Faces.svelte',
			'settings-ui/Routing.svelte',
			'components/organize/CreatesList.svelte',
			'components/organize/ShootsPanel.svelte',
			'settings-ui/Performance.search.ts'
		]) {
			expect(found(readFileSync(join(lib, file), 'utf8')), file).toEqual([]);
		}
	});

	it('passes a grouped count, and what is not a count of things', () => {
		expect(found('`${counted(n)} files`')).toEqual([]);
		expect(found('`${n === 1 ? "a file" : "files"}`')).toEqual([]);
		expect(found('`${width} px`')).toEqual([]);
		expect(found('`Sift names ${site} files`')).toEqual([]);
		expect(found('<Said what={first.question} links={first.links} />')).toEqual([]);
		expect(found("'Pick 120 files'")).toEqual([]);
	});
});

describe('a busy state is the control mid-press, not what it does', () => {
	it('knows one -ing word and an ellipsis, and nothing else', () => {
		expect(isBusyLabel('Saving\u2026')).toBe(true);
		expect(isBusyLabel('Answering\u2026')).toBe(true);
		expect(isBusyLabel('Save')).toBe(false);
		expect(isBusyLabel('Saving the file\u2026')).toBe(false);
		expect(isBusyLabel('and 4 more')).toBe(false);
	});
});

it('reads a word trailing full stops as words, not as a dotted name', () => {
	/* "Saving..." taken for an identifier would never be read, so neither the ellipsis rule nor
	   the verb rule could see it. */
	const texts = copyIn("export const COPY = { busy: 'Saving...' };", 'lib/x.ts', () => null).map(
		(one) => one.text
	);
	expect(texts).toContain('Saving...');
});
