// Which labels a settings pane draws that the settings search cannot find. See
// `check_settings_search_covers_panes.js`.

import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';

import { withoutComments } from './tree.js';

const require = createRequire(import.meta.url);

/**
 * The rows and headings a person reads as the NAME of something on a pane, and the attribute
 * each carries it in. A button's words, a switch's accessible name and a field's placeholder are
 * not here: they are the press or the box, and the row they sit in is what carries the name.
 * `SectionHeading` carries its name as its children.
 */
export const NAMED_BY = {
	SettingGroup: 'heading',
	LabelledRow: 'label',
	ActionRow: 'label',
	FieldRow: 'label',
	FactRow: 'label',
	SectionHeading: null
};

/* An opening tag of one of those, up to the end of its attributes. Attribute values may hold `>`
   inside braces (`help={a > b}`), so the tag is read to its end by counting braces, not by
   regex. */
const OPENING = new RegExp(`<(${Object.keys(NAMED_BY).join('|')})(?=[\\s>/])`, 'g');

/** A dotted name and nothing else: `COPY.pack.create`, `TILE_MARKS.name`, `label`. */
const DOTTED = /^[A-Za-z_$][\w$]*(?:\.[\w$]+)*$/;

/**
 * The end of a tag that starts at `from` (just past its name), and whether it closes itself.
 *
 * @param {string} source
 * @param {number} from
 * @returns {{ end: number, selfClosing: boolean }}
 */
function tagEnd(source, from) {
	let depth = 0;
	let quote = '';
	for (let at = from; at < source.length; at++) {
		const one = source[at];
		if (quote) {
			if (one === quote) quote = '';
			continue;
		}
		if (depth === 0 && (one === '"' || one === "'")) quote = one;
		else if (one === '{') depth++;
		else if (one === '}') depth--;
		else if (one === '>' && depth === 0) {
			return { end: at + 1, selfClosing: source[at - 1] === '/' };
		}
	}
	return { end: source.length, selfClosing: true };
}

/**
 * One attribute's value in a tag's text: `{ literal }` for `name="words"`, `{ expression }` for
 * `name={...}`, or null when the tag does not carry it.
 *
 * @param {string} tag
 * @param {string} name
 * @returns {{ literal?: string, expression?: string } | null}
 */
function attribute(tag, name) {
	const start = new RegExp(`\\s${name}=(["{])`).exec(tag);
	if (!start) return null;
	const from = start.index + start[0].length;
	if (start[1] === '"') return { literal: tag.slice(from, tag.indexOf('"', from)) };
	let depth = 1;
	for (let at = from; at < tag.length; at++) {
		if (tag[at] === '{') depth++;
		else if (tag[at] === '}' && --depth === 0) return { expression: tag.slice(from, at).trim() };
	}
	return null;
}

/**
 * Every name a pane draws on a row or heading, as written in its markup.
 *
 * @param {string} source a `.svelte` file
 * @returns {{ tag: string, line: number, literal?: string, expression?: string }[]}
 */
export function namesIn(source) {
	const bare = withoutComments(source);
	const markupFrom = bare.lastIndexOf('</script>');
	/** @type {{ tag: string, line: number, literal?: string, expression?: string }[]} */
	const found = [];
	for (const match of bare.matchAll(OPENING)) {
		if (match.index < markupFrom) continue;
		const tag = match[1];
		const { end, selfClosing } = tagEnd(bare, match.index + match[0].length);
		const line = bare.slice(0, match.index).split('\n').length;
		const carrier = NAMED_BY[/** @type {keyof typeof NAMED_BY} */ (tag)];
		if (carrier) {
			const value = attribute(bare.slice(match.index, end), carrier);
			if (value) found.push({ tag, line, ...value });
			continue;
		}
		if (selfClosing) continue;
		const close = bare.indexOf(`</${tag}>`, end);
		const inner = bare.slice(end, close).trim();
		const braced = /^\{([^{}]*)\}$/.exec(inner);
		if (braced) found.push({ tag, line, expression: braced[1].trim() });
		else if (inner && !inner.includes('{') && !inner.includes('<')) {
			found.push({ tag, line, literal: inner.replace(/\s+/g, ' ') });
		}
	}
	return found;
}

/**
 * What a pane's script names: the imports from a sibling `.search` module, the dotted aliases it
 * makes of them, and its own string constants.
 *
 * @param {string} source
 * @returns {{ imports: Map<string, { module: string, name: string }>, aliases: Map<string, string>, strings: Map<string, string> }}
 */
export function scriptNames(source) {
	const script = withoutComments(source.slice(0, source.lastIndexOf('</script>')));
	const imports = new Map();
	for (const match of script.matchAll(/import\s*\{([^}]*)\}\s*from\s*'\.\/([\w.-]+\.search)'/g)) {
		for (const part of match[1].split(',')) {
			const [name, local] = part
				.trim()
				.replace(/^type\s+/, '')
				.split(/\s+as\s+/);
			if (name) imports.set((local ?? name).trim(), { module: match[2], name: name.trim() });
		}
	}
	const aliases = new Map();
	const strings = new Map();
	for (const match of script.matchAll(/const\s+([A-Za-z_$][\w$]*)\s*=\s*([^;\n]+);?/g)) {
		const value = match[2].trim();
		const quoted = /^(['"])(.*)\1$/.exec(value);
		if (quoted) strings.set(match[1], quoted[2]);
		else if (DOTTED.test(value)) aliases.set(match[1], value);
	}
	return { imports, aliases, strings };
}

/**
 * A `.search.ts` module's exports, evaluated: its words are data, and the only honest reading
 * of `COPY.pack.create` is the string it holds.
 *
 * Transpiled with the TypeScript the client already depends on and run in a fresh context. A
 * sibling `.search` import is loaded the same way; anything else it imports (a counting helper,
 * a formatter) is a stand-in that answers every call and every property with itself and reads
 * as empty text, because those only ever build sentences inside functions, never a name.
 *
 * @param {string} name e.g. `Faces.search`
 * @param {(name: string) => string} read the module's TypeScript source
 * @param {Map<string, Record<string, unknown>>} [loaded]
 * @returns {Record<string, unknown>}
 */
export function loadSearchModule(name, read, loaded = new Map()) {
	const known = loaded.get(name);
	if (known) return known;
	const ts = require('typescript');
	const js = ts.transpileModule(read(name), {
		compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
	}).outputText;
	/** @type {Record<string, unknown>} */
	const exports = {};
	loaded.set(name, exports);
	const requireHere = (/** @type {string} */ spec) => {
		const sibling = /^\.\/([\w.-]+\.search)$/.exec(spec);
		return sibling ? loadSearchModule(sibling[1], read, loaded) : standIn();
	};
	runInNewContext(js, { exports, require: requireHere, module: { exports } });
	return exports;
}

function standIn() {
	/** @type {any} */
	const self = new Proxy(function () {}, {
		get: (_, key) => (key === Symbol.toPrimitive ? () => '' : key === 'then' ? undefined : self),
		apply: () => self
	});
	return self;
}

/**
 * Walk a dotted path from a value: `pack.create` from `COPY`.
 *
 * @param {unknown} value
 * @param {string[]} path
 * @returns {unknown}
 */
function walk(value, path) {
	let here = value;
	for (const step of path) {
		if (here === null || typeof here !== 'object') return undefined;
		here = /** @type {Record<string, unknown>} */ (here)[step];
	}
	return here;
}

/**
 * The words one drawn name reads as, or null when they are decided while the screen runs (a
 * tunnel's name, a task's title, a sentence chosen by a condition): those are the person's own
 * data or a state, never a setting to search for, and no source reading could know them.
 *
 * @param {{ literal?: string, expression?: string }} drawn
 * @param {ReturnType<typeof scriptNames>} names
 * @param {(module: string) => Record<string, unknown>} load
 * @returns {string | null}
 */
export function wordsOf(drawn, names, load) {
	/* Words with a value set into them (`Rename {name}`) name a person's own thing. */
	if (drawn.literal !== undefined) return drawn.literal.includes('{') ? null : drawn.literal;
	let expression = drawn.expression ?? '';
	for (let hops = 0; hops < 5 && DOTTED.test(expression); hops++) {
		const [root, ...path] = expression.split('.');
		const alias = names.aliases.get(root);
		if (alias) {
			expression = [alias, ...path].join('.');
			continue;
		}
		if (path.length === 0 && names.strings.has(root)) return names.strings.get(root) ?? null;
		const imported = names.imports.get(root);
		if (!imported) return null;
		const value = walk(load(imported.module)[imported.name], path);
		return typeof value === 'string' ? value : null;
	}
	return null;
}

/** The comparison: the same words, whatever the case and the spacing. */
export const said = (/** @type {string} */ text) => text.trim().replace(/\s+/g, ' ').toLowerCase();

/**
 * Every name the search index declares, from every `.search.ts` module's `SEARCHABLE`.
 *
 * @param {string[]} modules
 * @param {(module: string) => Record<string, unknown>} load
 * @returns {Set<string>}
 */
export function declaredNames(modules, load) {
	const names = new Set();
	for (const module of modules) {
		const entries = load(module).SEARCHABLE;
		if (!Array.isArray(entries)) continue;
		for (const entry of entries) {
			if (typeof entry?.name === 'string') names.add(said(entry.name));
		}
	}
	return names;
}

/**
 * The names one pane draws that the index does not declare.
 *
 * @param {string} source the pane
 * @param {Set<string>} declared from `declaredNames`
 * @param {(module: string) => Record<string, unknown>} load
 * @param {Set<string>} excused `said` names that are not settings (see the gate's list)
 * @returns {{ line: number, words: string }[]}
 */
export function unfoundIn(source, declared, load, excused = new Set()) {
	const names = scriptNames(source);
	/** @type {{ line: number, words: string }[]} */
	const unfound = [];
	for (const drawn of namesIn(source)) {
		const words = wordsOf(drawn, names, load);
		if (words === null || !words.trim()) continue;
		if (declared.has(said(words)) || excused.has(said(words))) continue;
		unfound.push({ line: drawn.line, words });
	}
	return unfound;
}
