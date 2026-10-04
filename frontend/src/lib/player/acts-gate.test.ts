/*
 * One act, one tooltip, on every player (`acts.ts` says why).
 *
 * Three rules, read from the source because the words are the thing held:
 *
 * 1. Inside a player, a string equal to an act's words is refused: it is read from `ACTS`, so the
 *    words exist once and a change to them is a change on every player.
 * 2. Inside a player, a button wearing an act's glyph takes its tooltip and its name from `ACTS`
 *    (directly, or through the one `const` it is worked out in). A button that is dimmed for good
 *    (a bare `disabled`) keeps its reason instead.
 * 3. Anywhere in the client, a retired second name for an act (`RETIRED`) is refused.
 * 4. Inside a player, a tooltip's key is read from the act table (`keyOf`, over `ACT_KEYS`), never
 *    written by hand, and a tooltip naming an act that has a key carries one: keys each button
 *    wrote for itself would go uneven (I on one "Open mini player" and none on another). A player that does not answer the key on some
 *    screen still reads it from the table, and passes nothing there.
 *
 * Each rule is also driven with markup written for the purpose, so a rule that stopped reading
 * would fail here rather than pass on everything.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

import { keysFor } from '$lib/shell/shortcuts';
import { ACT_GLYPHS, ACT_KEYS, ACTS, keyOf, RETIRED } from './acts';

const SRC = join(process.cwd(), 'src');

/** The players: every file that draws a player's controls or declares their words. */
const PLAYER_DIRS = ['lib/components/player', 'lib/components/theater'];
const PLAYER_FILES = [
	'lib/components/AssetView.svelte',
	'lib/components/AssetModal.svelte',
	'lib/remote/RemoteScreen.svelte',
	'lib/remote/copy.ts',
	'lib/player/snapshot.ts',
	'lib/player/loop-modes.ts'
];

/* A player whose words come from a copy table of its own, whose entries read `ACTS` in turn. */
const COPY_TABLES: Readonly<Record<string, string>> = {
	'lib/remote/RemoteScreen.svelte': 'lib/remote/copy.ts'
};

const WORDS = new Set<string>(Object.values(ACTS));

/** A file less its comments, lengths kept so a line number still points at the line. */
function uncommented(code: string): string {
	const blank = (text: string) => text.replace(/[^\n]/g, ' ');
	return code
		.replace(/<!--[^]*?-->/g, blank)
		.replace(/\/\*[^]*?\*\//g, blank)
		.replace(/(^|[^:'"`\\])\/\/[^\n]*/g, (all, lead: string) => lead + blank(all.slice(1)));
}

/** Every quoted string in a file's code, with its line. */
function quoted(code: string): { line: number; text: string }[] {
	const found: { line: number; text: string }[] = [];
	for (const match of uncommented(code).matchAll(/'([^'\n\\]*)'|"([^"\n\\]*)"|`([^`$\\]*)`/g)) {
		const text = match[1] ?? match[2] ?? match[3] ?? '';
		found.push({ line: code.slice(0, match.index).split('\n').length, text });
	}
	return found;
}

/** Rule 1: an act's words written out inside a player, rather than read from `ACTS`. */
function wordsWrittenOut(code: string): string[] {
	return quoted(code)
		.filter(({ text }) => WORDS.has(text))
		.map(({ line, text }) => `line ${line}: "${text}" is an act's words; read it from ACTS`);
}

/** Rule 3: a retired second name for an act, anywhere. */
function retiredNames(code: string): string[] {
	return quoted(code)
		.filter(({ text }) => text in RETIRED)
		.map(
			({ line, text }) =>
				`line ${line}: "${text}" is a retired name; say "${ACTS[RETIRED[text]]}" (ACTS.${RETIRED[text]})`
		);
}

/* One attribute's value: a quoted string or a braced expression, one level of nesting. */
const VALUE = String.raw`("[^"]*"|\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})`;
const TAG = /<(Button|Tooltip)\b((?:[^>{}]|\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})*)>/g;

function attribute(attrs: string, name: string): string | null {
	return new RegExp(String.raw`(?:^|\s)${name}=${VALUE}`).exec(attrs)?.[1] ?? null;
}

/**
 * Whether a label's value reads its words from `ACTS`: directly, through a `const` in the file, or
 * through the file's copy table (`COPY.previous`, where the table says `previous: ACTS.previous`).
 */
function readsActs(value: string, code: string, table = ''): boolean {
	if (value.includes('ACTS.')) return true;
	const entries = [...value.matchAll(/\bCOPY\.(\w+)/g)].map((m) => m[1]);
	if (entries.length > 0 && table) {
		return entries.every((key) =>
			new RegExp(String.raw`^\s*${key}:\s*ACTS\.\w+,?\s*$`, 'm').test(table)
		);
	}
	const named = /^\{\s*([A-Za-z_$][\w$]*)\s*\}$/.exec(value)?.[1];
	if (!named) return false;
	const at = new RegExp(String.raw`(?:@const|const|let)\s+${named}\b[^=]*=`).exec(code);
	const prop = new RegExp(String.raw`^\s*${named}\s*=`, 'm').exec(code);
	const from = at ?? prop;
	if (!from) return false;
	const body = code.slice(from.index, from.index + 400);
	const end = body.search(/;|\n\s*\}|\}\s*\n|,\n/);
	return (end < 0 ? body : body.slice(0, end + 1)).includes('ACTS.');
}

/** Rule 2: a button wearing an act's glyph whose tooltip or name does not come from `ACTS`. */
function glyphsNamedElsewhere(code: string, table = ''): string[] {
	const plain = uncommented(code);
	const faults: string[] = [];
	let tooltip: string | null = null;
	for (const match of plain.matchAll(TAG)) {
		const [, tag, attrs] = match;
		if (tag === 'Tooltip') {
			tooltip = attribute(attrs, 'label');
			continue;
		}
		const icon = attribute(attrs, 'icon') ?? '';
		const glyphs = [...icon.matchAll(/'([a-z_0-9]+)'|"([a-z_0-9]+)"/g)].map((m) => m[1] ?? m[2]);
		const acts = glyphs.flatMap((glyph) => ACT_GLYPHS[glyph] ?? []);
		if (acts.length === 0 || /(?:^|\s)disabled(?:\s|$)/.test(attrs)) {
			tooltip = null;
			continue;
		}
		const line = code.slice(0, match.index).split('\n').length;
		const name = attribute(attrs, 'aria-label');
		for (const [what, value] of [
			['name', name],
			['tooltip', tooltip]
		] as const) {
			if (value !== null && !readsActs(value, plain, table)) {
				faults.push(
					`line ${line}: a ${glyphs.join('/')} button's ${what} ${value} is not read from ACTS`
				);
			}
		}
		tooltip = null;
	}
	return faults;
}

/** Whether an attribute's value reads `needle`: directly, or through the one `const` it names. */
function readsThrough(value: string, code: string, needle: string): boolean {
	if (value.includes(needle)) return true;
	const named = /^\{\s*([A-Za-z_$][\w$]*)\s*\}$/.exec(value)?.[1];
	if (!named) return false;
	const at = new RegExp(String.raw`(?:@const|const|let)\s+${named}\b[^=]*=`).exec(code);
	if (!at) return false;
	const body = code.slice(at.index, at.index + 400);
	const end = body.search(/;|\n\s*\}|\}\s*\n|,\n/);
	return (end < 0 ? body : body.slice(0, end + 1)).includes(needle);
}

/** Rule 4: a tooltip's key written by hand, or missing where the act it names has one. */
function keysByHand(code: string): string[] {
	const plain = uncommented(code);
	const faults: string[] = [];
	for (const match of plain.matchAll(TAG)) {
		const [, tag, attrs] = match;
		if (tag !== 'Tooltip') continue;
		const line = code.slice(0, match.index).split('\n').length;
		const key = attribute(attrs, 'shortcut');
		if (key !== null && !readsThrough(key, plain, 'keyOf(')) {
			faults.push(`line ${line}: a tooltip's key ${key} is written by hand; read it with keyOf`);
		}
		const label = attribute(attrs, 'label') ?? '';
		const keyed = [...label.matchAll(/\bACTS\.(\w+)/g)]
			.map((m) => m[1])
			.filter((act) => act in ACT_KEYS);
		if (key === null && keyed.length > 0) {
			faults.push(
				`line ${line}: a tooltip for ${keyed.join('/')} shows no key; read it with keyOf`
			);
		}
	}
	return faults;
}

function filesUnder(dir: string): string[] {
	return readdirSync(join(SRC, dir)).flatMap((name) => {
		const path = join(SRC, dir, name);
		if (statSync(path).isDirectory()) return filesUnder(relative(SRC, path));
		return /\.(svelte|ts)$/.test(name) && !/\.test\.|Harness|Probe/.test(name)
			? [relative(SRC, path)]
			: [];
	});
}

const players = [...PLAYER_DIRS.flatMap((dir) => filesUnder(dir)), ...PLAYER_FILES].map((path) =>
	path.split('\\').join('/')
);

const everything = [...filesUnder('lib'), ...filesUnder('routes')]
	.map((path) => path.split('\\').join('/'))
	.filter((path) => path !== 'lib/player/acts.ts');

function read(path: string): string {
	return readFileSync(join(SRC, path), 'utf8');
}

describe('the players', () => {
	it('are found, so the rules below read something', () => {
		expect(players.length).toBeGreaterThan(25);
		expect(players).toContain('lib/components/player/MiniPlayer.svelte');
		expect(players).toContain('lib/components/theater/CellControls.svelte');
	});

	it("write no act's words out: they read them from ACTS", () => {
		const faults = players.flatMap((path) =>
			wordsWrittenOut(read(path)).map((f) => `${path} ${f}`)
		);
		expect(faults).toEqual([]);
	});

	it("name every button wearing an act's glyph from ACTS", () => {
		const faults = players.flatMap((path) =>
			glyphsNamedElsewhere(read(path), COPY_TABLES[path] ? read(COPY_TABLES[path]) : '').map(
				(f) => `${path} ${f}`
			)
		);
		expect(faults).toEqual([]);
	});

	it('join acts only to keys the shortcut list declares', () => {
		const joined = Object.values(ACT_KEYS).flatMap((keys) => Object.values(keys ?? {}));
		expect(joined.length).toBeGreaterThan(20);
		expect(joined.filter((id) => id === undefined || keysFor(id) === null)).toEqual([]);
		// F on every Full screen, I on Theater's mini player, as on the Player's.
		expect(keysFor(keyOf('fullScreen', 'theater') ?? '')).toBe('F');
		expect(keysFor(keyOf('fullScreen', 'player') ?? '')).toBe('F');
		expect(keysFor(keyOf('miniPlayer', 'theater') ?? '')).toBe('I');
	});

	it('read every tooltip key from the act table, and show one wherever the act has one', () => {
		const faults = players.flatMap((path) => keysByHand(read(path)).map((f) => `${path} ${f}`));
		expect(faults).toEqual([]);
	});
});

/* It reads every client file: a minute under a loaded machine, seconds alone. */
describe('the whole client', { timeout: 60_000 }, () => {
	it('says no retired second name for an act', () => {
		// Only a file naming a retired word at all is read closely: the close read is the slow part.
		const retired = Object.keys(RETIRED);
		const faults = everything.flatMap((path) => {
			const code = read(path);
			if (!retired.some((word) => code.includes(word))) return [];
			return retiredNames(code).map((f) => `${path} ${f}`);
		});
		expect(faults).toEqual([]);
	});
});

describe('the rules, on markup written for the purpose', () => {
	it("refuse an act's words written out, and pass them read from ACTS", () => {
		expect(wordsWrittenOut(`<Tooltip label={playing ? 'Pause' : 'Play'}>`)).toHaveLength(2);
		expect(wordsWrittenOut('<Tooltip label={playing ? ACTS.pause : ACTS.play}>')).toEqual([]);
		// A comment may say the words.
		expect(wordsWrittenOut("<!-- the 'Play' press -->\n// 'Pause'")).toEqual([]);
	});

	it('refuse a second name for full screen on any player', () => {
		const theater = `<Tooltip label={on ? 'Leave full screen' : 'Fill the screen'}>`;
		expect(retiredNames(theater)).toEqual([
			'line 1: "Fill the screen" is a retired name; say "Full screen" (ACTS.fullScreen)'
		]);
	});

	it("refuse a button wearing an act's glyph and named in words of its own", () => {
		const own = `<Tooltip label="Go big">\n<Button icon={on ? 'fullscreen_exit' : 'fullscreen'} aria-label="Go big" />\n</Tooltip>`;
		expect(glyphsNamedElsewhere(own)).toHaveLength(2);
		const read = `<Tooltip label={on ? ACTS.leaveFullScreen : ACTS.fullScreen}>\n<Button icon="fullscreen" aria-label={on ? ACTS.leaveFullScreen : ACTS.fullScreen} />\n</Tooltip>`;
		expect(glyphsNamedElsewhere(read)).toEqual([]);
	});

	it('follow a label through the const it is worked out in', () => {
		const through = `{@const words = back ? ACTS.previous : 'Nothing before this'}\n<Tooltip label={words}><Button icon="skip_previous" aria-label={words} /></Tooltip>`;
		expect(glyphsNamedElsewhere(through)).toEqual([]);
		const own = `{@const words = back ? 'Before' : 'Nothing before this'}\n<Tooltip label={words}><Button icon="skip_previous" aria-label={words} /></Tooltip>`;
		expect(glyphsNamedElsewhere(own)).toHaveLength(2);
	});

	it('refuse a key written by hand, and a keyed act shown with none', () => {
		const byHand = `<Tooltip label={ACTS.miniPlayer} shortcut="player.corner"><Button icon="picture_in_picture" aria-label={ACTS.miniPlayer} /></Tooltip>`;
		expect(keysByHand(byHand)).toEqual([
			'line 1: a tooltip\'s key "player.corner" is written by hand; read it with keyOf'
		]);
		const ternary = `<Tooltip label={muted ? ACTS.unmute : ACTS.mute} shortcut={on ? 'player.mute' : undefined}>`;
		expect(keysByHand(ternary)).toHaveLength(1);
		const none = `<Tooltip label={on ? ACTS.leaveFullScreen : ACTS.fullScreen}>`;
		expect(keysByHand(none)).toEqual([
			'line 1: a tooltip for leaveFullScreen/fullScreen shows no key; read it with keyOf'
		]);
		const read = `{@const fill = keyOf(on ? 'leaveFullScreen' : 'fullScreen', keyboard)}\n<Tooltip label={on ? ACTS.leaveFullScreen : ACTS.fullScreen} shortcut={fill}>`;
		expect(keysByHand(read)).toEqual([]);
		const unanswered = `<Tooltip label={ACTS.fullSize} shortcut={docked ? keyOf('fullSize', 'player') : undefined}>`;
		expect(keysByHand(unanswered)).toEqual([]);
		// An act with no key anywhere carries none.
		expect(keysByHand('<Tooltip label={ACTS.close}>')).toEqual([]);
	});

	it('let a control dimmed for good keep its reason', () => {
		const dimmed = `<Tooltip label="A picture has no seconds to clip"><Button icon="content_cut" aria-label="A picture has no seconds to clip" disabled /></Tooltip>`;
		expect(glyphsNamedElsewhere(dimmed)).toEqual([]);
	});
});
