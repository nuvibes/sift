// Every string a person reads in the client, found by where it is written: the one reader the
// copy gates share.
//
// ## Why a parser
//
// Regular expressions over text between tags, a few attribute names and a toast's first argument
// miss a large part of what the client says: a dialog's `consequence`, `message`, `detail` and
// `lead`; the labels in a lookup table; the sentence a ternary hands a toast; the words a `.ts`
// helper returns.
//
// So this reads with the real parsers: Svelte's own for the markup (`svelte/compiler`, the modern
// tree) and TypeScript's for every script, `.ts` file and markup expression. A string is judged by
// WHERE it sits, not by what it looks like, and in two strengths:
//
// - **Copy by position**: text between tags; an attribute or object field whose name is a copy
//   name (`COPY_NAMES`); a toast's message; what a function named for words returns
//   (`labelFor`, `sayWhen` ...); the arms of a ternary or `??` that ends in one of those. Read
//   whatever it looks like: `label: 'Faces'` is copy.
// - **Copy by shape**: any other string in a value position (a table's value, an argument, an
//   array element), read only when it is sentence-shaped: a capital then a lowercase letter or a
//   space, or two or more words with an everyday function word among them. That is what tells
//   `'Nothing to review'` from `'grid wide'`.
//
// Never read: comments; identifiers, import paths and types; a string compared against (`===`,
// `case`, `in`) or used as a key; a log call's arguments; a class, a style, an address or an icon.
//
// ## What it reports
//
// `{ line, text, kind, control }` per string. `kind` says which rule found it. `control` is set
// when the string names what a control DOES (a button's text, a confirm label, a menu row's label),
// which is what the verb allow-list reads.

import { parse } from 'svelte/compiler';
import ts from 'typescript';

/**
 * One string a person reads: the line it is on, its words, which rule found it, and whether it
 * names what a control does.
 *
 * @typedef {{ line: number, text: string, kind: string, control: boolean }} CopyString
 */

/**
 * How a string reads from where it sits (see `classify`).
 *
 * @typedef {{ strength: 'position' | 'shape', kind: string, control: boolean }} Reading
 */

/** @typedef {{ offset: number, text: string, kind: string, control: boolean }} CodeString A copy string, by its offset into the code it was found in. */

/** @typedef {(specifier: string) => string | null} ModuleResolver The source of the module an import names, or null. */

/** @typedef {import('svelte/compiler').AST.Fragment} Fragment */
/** @typedef {import('svelte/compiler').AST.ExpressionTag} ExpressionTag */
/** @typedef {Fragment | Fragment['nodes'][number]} TemplateNode A fragment, or anything one holds. */

/**
 * The fragments a block holds, under the names Svelte gives them: `{#if}` its `consequent` and
 * `alternate`, `{#each}` its `body` and `fallback`, `{#await}` its `pending`, `then` and `catch`,
 * `{#key}` its `fragment`, `{#snippet}` its `body`. A block has only its own, and any may be null.
 *
 * @typedef {{
 *   fragment?: Fragment | null,
 *   consequent?: Fragment | null,
 *   alternate?: Fragment | null,
 *   body?: Fragment | null,
 *   fallback?: Fragment | null,
 *   pending?: Fragment | null,
 *   then?: Fragment | null,
 *   catch?: Fragment | null
 * }} BlockFragments
 */

/**
 * The control a run of markup text sits in: `open` until its label has started.
 *
 * @typedef {{ open: boolean }} ControlLabel
 */

/** Attribute and field names whose value is copy, whatever it looks like. */
export const COPY_NAMES = new Set([
	'action',
	'actionLabel',
	'alt',
	'aria-description',
	'aria-label',
	'aria-placeholder',
	'aria-roledescription',
	'body',
	'cancelLabel',
	'caption',
	'confirmLabel',
	'consequence',
	'deleteWord',
	'description',
	'detail',
	'empty',
	'emptyLabel',
	'failed',
	'heading',
	'help',
	'hint',
	'label',
	'lead',
	'message',
	'note',
	'noun',
	'placeholder',
	'question',
	'removeLabel',
	'sentence',
	'subtitle',
	'summary',
	'text',
	'title',
	'tooltip',
	'what',
	'why'
]);

/**
 * Marks typed where a character belongs (an arrow, three full stops), which a run of markup text
 * can hold with no word beside it. The `typography` list judges them; this only makes sure a text
 * made of nothing else is read at all.
 */
const TYPED_MARK = /->|<-|=>|\.\.\./;

/** Attribute and field names whose value is never copy: styling, addresses, keys, identities. */
export const NOT_COPY_NAMES = new Set([
	'as',
	'autocomplete',
	'class',
	'data-testid',
	'for',
	'form',
	'height',
	'href',
	'icon',
	'id',
	'inputmode',
	'key',
	// The client's own addresses: a settings section's id, a screen's tab, never words.
	'section',
	'show',
	// A settings-search entry's other names for a block: old words on purpose, so a person who
	// types the word the screen once said still finds it. Searched, never shown.
	'keywords',
	'kind',
	'lang',
	'method',
	'pattern',
	'rel',
	'role',
	'size',
	'slot',
	'src',
	'srcset',
	'style',
	'target',
	'tone',
	'type',
	'value',
	'variant',
	'width'
]);

/** A function or variable whose name says it holds words: what it returns is copy by position. */
const WORDY_NAME =
	/(label|title|heading|word|say|sentence|text|message|caption|noun|verb|hint|help|tooltip|summary|phrase|toast|reason|explain|describe)/i;

/** Calls whose first argument is a message a person reads. */
const TOAST_CALL =
	/^(?:toasts?|notify)\.(?:show|success|error|info|warn|warning)$|^(?:alert|confirm)$/;

/** Calls whose arguments are never copy: logs, the DOM, storage, addresses, patterns. */
const NOT_COPY_CALL =
	/^(?:console\.\w+|log\.\w+|require|import|goto|fetch|api\.\w+|new URL|URL|URLSearchParams|RegExp|new RegExp|\w+\.(?:querySelector(?:All)?|getElementById|closest|matches|addEventListener|removeEventListener|setAttribute|getAttribute|hasAttribute|removeAttribute|toggleAttribute|getPropertyValue|setProperty|removeProperty|getItem|setItem|removeItem|startsWith|endsWith|includes|indexOf|lastIndexOf|split|replace|replaceAll|match|matchAll|test|join|padStart|padEnd|get|set|has|delete|add|toggle|contains|dispatchEvent|postMessage|createElement|localeCompare|toLocaleString|toLocaleDateString|toLocaleTimeString|search|searchParams\.\w+|matchMedia))$/;

/** Everyday words a sentence has and a list of class names does not. */
const FUNCTION_WORD =
	/\b(?:the|a|an|of|to|in|is|for|and|or|on|with|this|that|it|not|from|by|your|you|be|are|was|has|have|can|will|no|any|all|at|as|its|yet|than|when|what|who|how)\b/i;

/** Characters prose does not use and code does. */
const CODE_SHAPE =
	/[{}<>=;\\|`$#]|\w\/\w|^\s*[-.:/]|^\s*[a-z-]+:\s*\S+;?$|\bvar\(|\d(?:px|rem|em|ms|s|%)\b/;

/**
 * A literal that only names something: a key, a path, a wire value, a camelCase name. One word of
 * plain letters is NOT this (`label: 'Faces'` is copy), and neither is a hyphenated word, nor a
 * word trailing full stops: "Saving..." is a word with an ellipsis, not a dotted name.
 */
const IDENTIFIER = /^(?![A-Za-z]+\.{2,}$)[\w-]*[_./:@][\w./:@-]*$|^[a-z]+[A-Z]\w*$|^[\d\W]*$/;

/**
 * Whether a string, found in a position that says nothing, reads as words for a person.
 *
 * @param {string} text
 * @returns {boolean}
 */
export function sentenceShaped(text) {
	const plain = text.trim();
	if (!/\s/.test(plain) || CODE_SHAPE.test(plain)) return false;
	if (/^[A-Z][a-z\s']/.test(plain)) return true;
	return FUNCTION_WORD.test(plain) && /[a-z]{2,}\s+[a-z]{2,}/i.test(plain);
}

/**
 * Whether a string found in a copy position carries any words at all.
 *
 * @param {string} text
 */
const hasWords = (text) => /[A-Za-z]{2,}/.test(text) && !IDENTIFIER.test(text.trim());

/**
 * The words of a string node, a substitution read as a space.
 *
 * @param {ts.Node} node
 * @returns {string | null}
 */
function textOf(node) {
	if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
	if (ts.isTemplateExpression(node)) {
		return node.head.text + node.templateSpans.map((span) => ` ${span.literal.text}`).join('');
	}
	return null;
}

/** @type {Set<ts.SyntaxKind>} */
const TRANSPARENT = new Set([
	ts.SyntaxKind.ParenthesizedExpression,
	ts.SyntaxKind.AsExpression,
	ts.SyntaxKind.SatisfiesExpression,
	ts.SyntaxKind.NonNullExpression,
	ts.SyntaxKind.TypeAssertionExpression,
	ts.SyntaxKind.TemplateSpan,
	ts.SyntaxKind.TemplateExpression,
	ts.SyntaxKind.ArrayLiteralExpression
]);

/** @type {Set<ts.SyntaxKind>} */
const PASSING_OPERATORS = new Set([
	ts.SyntaxKind.PlusToken,
	ts.SyntaxKind.QuestionQuestionToken,
	ts.SyntaxKind.BarBarToken,
	ts.SyntaxKind.AmpersandAmpersandToken
]);

/**
 * The name a property, variable or function is written under, or ''.
 *
 * @param {ts.NamedDeclaration | undefined} node
 * @returns {string}
 */
function nameOf(node) {
	const name = node?.name;
	if (!name) return '';
	if (ts.isIdentifier(name) || ts.isPrivateIdentifier(name)) return name.text;
	if (ts.isStringLiteral(name) || ts.isNumericLiteral(name)) return name.text;
	return '';
}

/**
 * The function a `return` or an arrow body belongs to, named as it is called.
 *
 * @param {ts.Node} node
 * @returns {string}
 */
function enclosingFunctionName(node) {
	// A source file's `parent` is undefined, whatever the type says: that is where the walk ends.
	for (let at = /** @type {ts.Node | undefined} */ (node); at; at = at.parent) {
		if (ts.isFunctionDeclaration(at) || ts.isMethodDeclaration(at)) return nameOf(at);
		if (ts.isArrowFunction(at) || ts.isFunctionExpression(at)) {
			const holder = at.parent;
			if (holder && (ts.isVariableDeclaration(holder) || ts.isPropertyAssignment(holder))) {
				return nameOf(holder);
			}
			return '';
		}
	}
	return '';
}

/**
 * Whether an object literal is a control: it carries a label and something that runs.
 *
 * @param {ts.ObjectLiteralExpression} object
 * @returns {boolean}
 */
function isAControl(object) {
	return object.properties.some((property) => {
		const name = nameOf(property);
		return (
			/^(?:on\w+|run|action|do|press|select|href)$/.test(name) &&
			(ts.isMethodDeclaration(property) ||
				(ts.isPropertyAssignment(property) &&
					(ts.isArrowFunction(property.initializer) ||
						ts.isFunctionExpression(property.initializer) ||
						ts.isIdentifier(property.initializer) ||
						ts.isPropertyAccessExpression(property.initializer))))
		);
	});
}

/**
 * How one string literal reads, from where it sits: `null` for never copy, else
 * `{ strength: 'position' | 'shape', kind, control }`.
 *
 * `top` is the strength the outermost expression was handed by its markup context: text
 * between tags or a copy attribute is 'position', any other attribute 'shape'.
 *
 * @param {ts.Node} literal
 * @param {Reading | null} top
 * @returns {Reading | null}
 */
function classify(literal, top) {
	let child = literal;
	let at = literal.parent;
	for (;;) {
		if (!at || ts.isSourceFile(at)) return top;
		if (ts.isExpressionStatement(at)) {
			// A markup expression is parsed as one statement: reaching it is reaching the markup.
			return ts.isSourceFile(at.parent) && top ? top : null;
		}
		if (TRANSPARENT.has(at.kind)) {
			child = at;
			at = at.parent;
			continue;
		}
		if (ts.isConditionalExpression(at)) {
			if (child === at.condition) return null;
			child = at;
			at = at.parent;
			continue;
		}
		if (ts.isBinaryExpression(at)) {
			if (!PASSING_OPERATORS.has(at.operatorToken.kind)) return null;
			if (at.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken && child === at.left) {
				return null;
			}
			child = at;
			at = at.parent;
			continue;
		}
		break;
	}

	if (ts.isPropertyAssignment(at)) {
		if (child !== at.initializer) return null;
		const name = nameOf(at);
		if (NOT_COPY_NAMES.has(name)) return null;
		// A lookup table's value is copy when the TABLE is named for words: `LABELS.canceled`.
		let table = ts.isObjectLiteralExpression(at.parent) ? at.parent.parent : null;
		while (table && TRANSPARENT.has(table.kind)) table = table.parent;
		const tableName =
			table && (ts.isVariableDeclaration(table) || ts.isPropertyAssignment(table))
				? nameOf(table)
				: '';
		if (COPY_NAMES.has(name) || WORDY_NAME.test(name) || WORDY_NAME.test(tableName)) {
			const control =
				name === 'confirmLabel' ||
				name === 'cancelLabel' ||
				name === 'actionLabel' ||
				(name === 'label' && ts.isObjectLiteralExpression(at.parent) && isAControl(at.parent));
			return { strength: 'position', kind: `field:${name}`, control };
		}
		return { strength: 'shape', kind: `table:${name}`, control: false };
	}
	if (ts.isCallExpression(at) || ts.isNewExpression(at)) {
		if (child === at.expression) return null;
		const callee = (ts.isNewExpression(at) ? 'new ' : '') + at.expression.getText();
		if (TOAST_CALL.test(callee)) return { strength: 'position', kind: 'toast', control: false };
		if (NOT_COPY_CALL.test(callee)) return null;
		return { strength: 'shape', kind: `argument:${callee.slice(0, 40)}`, control: false };
	}
	if (ts.isReturnStatement(at) || (ts.isArrowFunction(at) && child === at.body)) {
		const name = enclosingFunctionName(at);
		if (WORDY_NAME.test(name))
			return { strength: 'position', kind: `return:${name}`, control: false };
		return { strength: 'shape', kind: `return:${name}`, control: false };
	}
	if (ts.isVariableDeclaration(at) || ts.isPropertyDeclaration(at)) {
		if (child !== at.initializer) return null;
		const name = nameOf(at);
		if (WORDY_NAME.test(name))
			return { strength: 'position', kind: `value:${name}`, control: false };
		return { strength: 'shape', kind: `value:${name}`, control: false };
	}
	if (ts.isBinaryExpression(at) && at.operatorToken.kind === ts.SyntaxKind.EqualsToken) {
		if (child !== at.right) return null;
		return { strength: 'shape', kind: 'assigned', control: false };
	}
	if (
		ts.isImportDeclaration(at) ||
		ts.isExportDeclaration(at) ||
		ts.isExternalModuleReference(at) ||
		ts.isLiteralTypeNode(at) ||
		ts.isCaseClause(at) ||
		ts.isElementAccessExpression(at) ||
		ts.isEnumMember(at) ||
		ts.isComputedPropertyName(at) ||
		ts.isSwitchStatement(at) ||
		ts.isIfStatement(at) ||
		ts.isTypeOfExpression(at) ||
		ts.isTaggedTemplateExpression(at)
	) {
		return null;
	}
	return { strength: 'shape', kind: 'value', control: false };
}

/**
 * Whether a literal is written inside a pane's copy module: the object a `const COPY = { ... }`
 * declares (`settings-ui/<Pane>.search.ts`).
 *
 * Every string in one is a word on a screen by construction (holding the pane's words is the
 * whole of what the module is for), so it is read by position, whatever its key is called and
 * however short it is. Otherwise a one-word label moved out of the markup and into the module
 * ('Tasks', 'Generate', 'Tonight') is read by nobody: its key names no words, and one word is not
 * sentence-shaped. The nearest declaration decides, so a local `const` inside a function in the
 * module is judged as itself.
 *
 * @param {ts.Node} node
 * @returns {boolean}
 */
function insideCopyModule(node) {
	for (let at = node.parent; at && !ts.isSourceFile(at); at = at.parent) {
		if (ts.isVariableDeclaration(at)) return nameOf(at) === 'COPY';
	}
	return false;
}

/**
 * Every copy string in a run of TypeScript, as `{ offset, text, kind, control }`.
 *
 * `top` is how the markup that holds this code reads it (see `classify`), or null for a script.
 *
 * @param {string} code
 * @param {Reading | null} top
 * @returns {CodeString[]}
 */
function stringsInCode(code, top) {
	const file = ts.createSourceFile('copy.ts', code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
	/** @type {CodeString[]} */
	const found = [];
	/** @param {ts.Node} node */
	const visit = (node) => {
		const text = textOf(node);
		if (text !== null) {
			let reading = classify(node, top);
			if (reading && reading.strength === 'shape' && insideCopyModule(node))
				reading = { ...reading, strength: 'position' };
			if (reading) {
				const keep = reading.strength === 'position' ? hasWords(text) : sentenceShaped(text);
				if (keep) {
					found.push({
						offset: node.getStart(file),
						text,
						kind: reading.kind,
						control: reading.control
					});
				}
			}
			// A template's substitutions may hold words of their own: `${n === 1 ? 'file' : 'files'}`.
			if (!ts.isTemplateExpression(node)) return;
		}
		ts.forEachChild(node, visit);
	};
	visit(file);
	return found;
}

/**
 * The strings a pane's copy module declares, by dotted path: `{ 'scan.name': 'Scan now', ... }`.
 *
 * A control whose label is written `{COPY.scan.name}` names its verb in the module, not in the
 * markup; a reader that stopped at the expression would read the label as "opens with data" and
 * check no verb at all. Only the COPY object the module exports is read, and only its string
 * literals; a function-valued entry (`(n) => \`Scanning ${n}\``) is a sentence built at run time,
 * which the module's own strings already cover.
 *
 * @param {string} moduleSource
 * @returns {Map<string, string>}
 */
export function copyModuleStrings(moduleSource) {
	const file = ts.createSourceFile(
		'copy.ts',
		moduleSource,
		ts.ScriptTarget.Latest,
		true,
		ts.ScriptKind.TS
	);
	/** @type {Map<string, string>} */
	const strings = new Map();
	/**
	 * @param {ts.ObjectLiteralExpression} object
	 * @param {string} prefix
	 */
	const walk = (object, prefix) => {
		for (const property of object.properties) {
			if (!ts.isPropertyAssignment(property)) continue;
			const name = nameOf(property);
			const path = prefix ? `${prefix}.${name}` : name;
			const value = property.initializer;
			if (ts.isObjectLiteralExpression(value)) walk(value, path);
			else {
				const text = textOf(value);
				if (text !== null) strings.set(path, text);
			}
		}
	};
	/** @param {ts.Node} node */
	const visit = (node) => {
		if (
			ts.isVariableDeclaration(node) &&
			nameOf(node) === 'COPY' &&
			node.initializer &&
			ts.isObjectLiteralExpression(node.initializer)
		) {
			walk(node.initializer, '');
			return;
		}
		ts.forEachChild(node, visit);
	};
	visit(file);
	return strings;
}

/** `{COPY.scan.name}` as the markup writes it: the module's own name, then the path. */
const COPY_REFERENCE = /^\s*COPY((?:\.[A-Za-z_$][\w$]*)+)\s*$/;

/**
 * The module a component's `import { COPY } from './X.search'` names, or null.
 *
 * @param {string} scriptSource
 * @returns {string | null}
 */
function copyModuleOf(scriptSource) {
	const found = /import\s*\{[^}]*\bCOPY\b[^}]*\}\s*from\s*['"]([^'"]+)['"]/.exec(scriptSource);
	return found ? found[1] : null;
}

/** The element names whose own text is a control's label. */
const CONTROL_ELEMENTS = /^(?:button|Button|IconButton|MenuItem|ActionButton|LinkButton)$/;

/** Attributes that name what a control does, wherever they are written. */
const CONTROL_ATTRIBUTES = new Set(['confirmLabel', 'cancelLabel', 'actionLabel']);

/**
 * @param {string} source
 * @param {number} offset
 */
const lineAt = (source, offset) => source.slice(0, offset).split('\n').length;

/**
 * Where a node of a component's script tree sits in the component's source.
 *
 * Svelte parses script with acorn, which writes `start` and `end` on every node; the `estree`
 * types `svelte/compiler` declares that tree with leave them out.
 *
 * @param {import('estree').Node} node
 * @returns {{ start: number, end: number }}
 */
const spanOf = (node) =>
	/** @type {import('estree').Node & { start: number, end: number }} */ (node);

/**
 * A Svelte component's copy.
 *
 * @param {string} source
 * @param {ModuleResolver | null} resolveModule
 * @returns {CopyString[]}
 */
function copyInSvelte(source, resolveModule) {
	/** @type {CopyString[]} */
	const found = [];
	/* The pane's copy module, when the component imports one and the caller can read it. */
	/** @type {Map<string, string> | null} */
	let moduleStrings = null;
	/**
	 * @param {number} offset
	 * @param {string} text
	 * @param {string} kind
	 * @param {boolean} control
	 */
	const push = (offset, text, kind, control) =>
		found.push({ line: lineAt(source, offset), text, kind, control });

	const root = parse(source, { modern: true });
	for (const script of [root.instance, root.module]) {
		if (!script) continue;
		const { start, end } = spanOf(script.content);
		const code = source.slice(start, end);
		for (const one of stringsInCode(code, null)) {
			push(start + one.offset, one.text, one.kind, one.control);
		}
		const specifier = copyModuleOf(code);
		if (specifier && resolveModule && moduleStrings === null) {
			const moduleSource = resolveModule(specifier);
			if (moduleSource) moduleStrings = copyModuleStrings(moduleSource);
		}
	}

	/**
	 * A control label written as a reference into the copy module, resolved to its words.
	 *
	 * @param {ExpressionTag} tag
	 * @returns {string | null}
	 */
	const referenced = (tag) => {
		if (!moduleStrings) return null;
		const { start, end } = spanOf(tag.expression);
		const match = COPY_REFERENCE.exec(source.slice(start, end));
		if (!match) return null;
		return moduleStrings.get(match[1].slice(1)) ?? null;
	};

	/**
	 * An expression in the markup, read with the strength its context gives it.
	 *
	 * @param {ExpressionTag} tag
	 * @param {Reading} top
	 */
	const expression = (tag, top) => {
		const { start, end } = spanOf(tag.expression);
		// Parenthesised so an object literal is not read as a block; the offset steps back one.
		for (const one of stringsInCode(`(${source.slice(start, end)})`, top)) {
			push(start + one.offset - 1, one.text, one.kind, one.control);
		}
	};

	/**
	 * @param {TemplateNode | null | undefined} node
	 * @param {ControlLabel | null} control
	 */
	const visit = (node, control) => {
		if (!node || typeof node !== 'object') return;
		// `control` is null, or the label of the control this text sits in: only its FIRST words name
		// what the control does ("Show {n} more" starts with Show; "more" is not a label).
		if (node.type === 'Text') {
			const text = node.data.replace(/\s+/g, ' ').trim();
			if (/[A-Za-z]{2,}/.test(text)) {
				push(node.start, text, 'text', Boolean(control?.open));
				if (control) control.open = false;
			} else if (TYPED_MARK.test(text)) {
				// No words, and still on screen: "12 -> 12" is a number, a typed arrow and a
				// number, so a reader that wanted two letters would never meet the arrow. Read for
				// how it is written only: it names no control and starts no label.
				push(node.start, text, 'text', false);
			}
			return;
		}
		if (node.type === 'Comment') return;
		if (node.type === 'ExpressionTag') {
			const words = control?.open ? referenced(node) : null;
			if (words !== null) push(node.start, words, 'copy-module', true);
			else
				expression(node, { strength: 'position', kind: 'text', control: Boolean(control?.open) });
			// Whatever it held, the label has started: with its own words if the expression had
			// any, with data ("{n} more") if not, and a label that opens with data names no verb.
			if (control) control.open = false;
			return;
		}
		if ('attributes' in node && Array.isArray(node.attributes)) {
			const isControl = CONTROL_ELEMENTS.test(node.name ?? '');
			for (const attribute of node.attributes) {
				if (attribute.type !== 'Attribute') continue;
				const name = attribute.name;
				if (NOT_COPY_NAMES.has(name) || /^on/.test(name)) continue;
				const copy = COPY_NAMES.has(name);
				const namesTheControl =
					CONTROL_ATTRIBUTES.has(name) ||
					(isControl && (name === 'label' || name === 'aria-label'));
				const parts = Array.isArray(attribute.value)
					? attribute.value
					: attribute.value === true
						? []
						: [attribute.value];
				// A VALUE OF WORDS AND SUBSTITUTIONS IS ONE STRING: "Move {entry} up" is its words with
				// a gap where the substitution is, read the way a template literal is (`textOf`), and
				// never "Move" and a bare "up" as two labels. One that opens with a substitution keeps
				// its leading gap, so a label that starts with data names no verb.
				const mixed = parts.length > 1 && parts.some((part) => part.type === 'Text') ? parts : null;
				if (mixed) {
					const words = mixed
						.map((part) => (part.type === 'Text' ? part.data.replace(/\s+/g, ' ') : ' '))
						.join('');
					const text = mixed[0].type === 'Text' ? words.trim() : ` ${words.trim()}`;
					if (copy ? hasWords(text) : sentenceShaped(text)) {
						push(mixed[0].start, text, `attribute:${name}`, namesTheControl);
					}
				}
				for (const part of parts) {
					if (part.type === 'Text') {
						if (mixed) continue;
						const text = part.data.replace(/\s+/g, ' ').trim();
						if (copy ? hasWords(text) : sentenceShaped(text)) {
							push(part.start, text, `attribute:${name}`, namesTheControl);
						}
					} else if (part.type === 'ExpressionTag') {
						const words = namesTheControl ? referenced(part) : null;
						if (words !== null) {
							push(part.start, words, `copy-module:${name}`, true);
							continue;
						}
						/** @type {Reading} */
						const top = copy
							? { strength: 'position', kind: `attribute:${name}`, control: namesTheControl }
							: { strength: 'shape', kind: `attribute:${name}`, control: false };
						expression(part, top);
					}
				}
			}
			visit(node.fragment, isControl ? { open: true } : control);
			return;
		}
		// What is left is a fragment, a block or a tag; a block keeps its fragments under these names.
		const inner = /** @type {BlockFragments} */ (node);
		for (const key of /** @type {const} */ ([
			'fragment',
			'consequent',
			'alternate',
			'body',
			'fallback',
			'pending',
			'then',
			'catch'
		])) {
			if (inner[key]) visit(inner[key], control);
		}
		if ('nodes' in node && Array.isArray(node.nodes)) {
			for (const child of node.nodes) visit(child, control);
		}
	};
	visit(root.fragment, null);
	return found;
}

/**
 * A `.ts` module's copy.
 *
 * @param {string} source
 * @returns {CopyString[]}
 */
function copyInScript(source) {
	return stringsInCode(source, null).map((one) => ({
		line: lineAt(source, one.offset),
		text: one.text,
		kind: one.kind,
		control: one.control
	}));
}

/**
 * Every string a person reads in one client source file.
 *
 * `where` is the path under `src`; a `.svelte` file is read as markup and scripts, anything else
 * as TypeScript. `resolveModule(specifier)` hands back the source of the pane's copy module the
 * component imports (`./X.search`), or null, so a control label written `{COPY.x}` is read as the
 * words it stands for; without it such a label is read as data. A file either parser refuses is reported as one entry of kind `unreadable`, so a
 * gate can fail on it rather than read it as a file with nothing to say.
 *
 * @param {string} source
 * @param {string} where
 * @param {ModuleResolver | null} [resolveModule]
 * @returns {CopyString[]}
 */
export function copyIn(source, where, resolveModule = null) {
	try {
		return where.endsWith('.svelte') ? copyInSvelte(source, resolveModule) : copyInScript(source);
	} catch (error) {
		/* What was thrown, by its message when it carries one. */
		const message =
			(typeof error === 'object' || typeof error === 'function') &&
			error !== null &&
			'message' in error
				? error.message
				: undefined;
		return [{ line: 1, text: String(message ?? error), kind: 'unreadable', control: false }];
	}
}
