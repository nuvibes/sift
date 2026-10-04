// Every piece of chrome the user can see is Sift's, not the browser's.
//
// The browser will draw a few things for free, and each of them arrives in the operating system's
// look rather than the app's: the pale tooltip box a `title` attribute produces, the modal strip
// `alert()` and `confirm()` drop from the top of the window, the focus ring a control gets when
// nobody styled one. They are somebody else's design, appearing in the middle of this one.
//
// The `title` attribute has a second problem worth stating on its own: it is not accessible. It
// does not appear on keyboard focus, it cannot be reached by touch at all, and screen readers treat
// it inconsistently. Use the tooltip component and give the element an aria-label.
//
// What this reads, and what it deliberately does not:
//
//   - `title=` on an HTML element is refused. `title=` on a component (<Placeholder title="..">,
//     <ConfirmDialog title="..">) is an ordinary prop that happens to share the name, and is
//     fine. The two are told apart by the case of the tag, which is how Svelte tells them apart.
//   - alert/confirm/prompt are refused when they are the browser's. A file that defines its own
//     function by one of those names is calling its own, so only an explicit `window.` form is
//     refused there.
//   - `outline: none` is refused only when the file offers no focus style of its own (`--focus-ring`,
//     or `--focus-outline` where a picture covers a ring painted as a shadow). Removing the
//     default ring is normal and expected (the app has its own), but removing it and replacing
//     it with nothing leaves a keyboard user with no way to see where they are.
//   - the operating system's scrollbar is refused, and this one is checked the other way round.
//     Every other rule here looks for something a file should not contain; a scrollbar is drawn by
//     default and has to be styled OUT, so what is checked is that the global stylesheet still
//     does it. A component hiding its scrollbar entirely is refused too: a scrollbar nobody can see
//     is a box whose length nobody can judge.
//   - a native <select> is refused. It is the one control the browser will not let a page restyle:
//     its closed box and its open list both arrive in the operating system's look. The app's Select
//     component wears the app's look instead, so that is what every chooser uses. The component
//     itself is allowed to name the tag it replaces.
//   - the browser's TEXT BOX is refused, and this one is checked the same way round as the
//     scrollbar. An `<input>` nobody dressed is a white box with the operating system's border and
//     font, in the middle of a dark interface. The baseline lives in the global stylesheet rather
//     than inside one component, so it holds for every field and not only those inside a `Field`;
//     what is checked here is that it is still there.

import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const SRC = join(root, 'src');

const GENERATED = join(SRC, 'lib/generated');

// The one stylesheet that dresses the browser's own furniture, for the whole app at once.
const GLOBAL_CSS = join(SRC, 'app.css');

// Both syntaxes, because they are for different browsers rather than alternatives: the first two
// are the standard and are what Firefox reads, the third is what Chrome, Edge and Safari read.
// Matched as DECLARATIONS rather than as text: a search for the property names anywhere in the file
// is satisfied by the comment above them, which mentions all three by name. Each one says what has
// to be true and reads the value rather than pattern-matching around it. The value is the point:
// `scrollbar-width: none` contains the property and turns the thing off, and a lookahead written to
// exclude it is defeated by the whitespace before it backtracking to zero. The text box, checked
// the same way and for the same reason: it is drawn by default in the operating system's look and
// has to be styled OUT, once, for the whole app. Each entry reads a DECLARATION rather than looking
// for a word, because the prose above these explains what they are and would satisfy a gate that
// only searched for names.
/**
 * The one rule that dresses a plain text box, pulled out of the global stylesheet.
 *
 * Found by its SELECTOR (a bare `input:not(...)`, at the start of a rule), so a copy of the same
 * declarations scoped inside a component (`.control input:not(...)`) is not mistaken for it. That
 * is the exact shape this whole check exists to refuse: a look kept in one component's control
 * box is correct for every control that happens to be inside one and absent for every control
 * that is not.
 *
 * Returns the selector and the body separately, because the two answer different questions: which
 * elements it reaches, and what it actually does to them.
 */
function baselineTextBox(css) {
	const found = /(^|\})\s*(input:not\([^)]*\)[^{}]*)\{([^}]*)\}/m.exec(css);
	return found === null ? null : { selector: found[2], body: found[3] };
}

// The text box, checked the same way as the scrollbar and for the same reason: it is drawn by
// default in the operating system's look and has to be styled OUT, once, for the whole app.
//
// Every entry reads the BASELINE RULE rather than searching the file. A search for the word
// `textarea` would be satisfied by the rule directly underneath the baseline (the one that lets a
// text area grow) even with `textarea` removed from the baseline.
const TEXT_BOX_RULES = [
	{
		what: 'a baseline rule for `input`, as a bare tag rather than inside a component',
		ok: (css) => baselineTextBox(css) !== null
	},
	{
		what: '`textarea` on that baseline, so a multi-line box is dressed like a single-line one',
		ok: (css) => /\btextarea\b/.test(baselineTextBox(css)?.selector ?? '')
	},
	{
		what: 'a ground colour on that baseline, so a box is not the browser white',
		ok: (css) => /background:\s*var\(--/.test(baselineTextBox(css)?.body ?? '')
	},
	{
		what: "a border on that baseline, so a box has this app's edge and not the site's",
		ok: (css) => /border:\s*[^;]*var\(--/.test(baselineTextBox(css)?.body ?? '')
	}
];

const SCROLLBAR_RULES = [
	{
		what: 'scrollbar-width, set to something other than none',
		ok: (css) => {
			const found = /scrollbar-width:\s*([\w-]+)/.exec(css);
			return found !== null && found[1] !== 'none';
		}
	},
	{
		what: 'scrollbar-color, with colours in it',
		ok: (css) => /scrollbar-color:\s*[\w(-]/.test(css)
	},
	{
		what: 'a styled ::-webkit-scrollbar-thumb',
		ok: (css) => /::-webkit-scrollbar-thumb\s*{/.test(css)
	}
];

/** CSS with its comments taken out, so a rule cannot be satisfied by prose describing it. */
function withoutComments(css) {
	return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/**
 * The same, but the file keeps its shape: every character of a comment becomes a space and every
 * newline stays where it was.
 *
 * For the rules that report a LINE. Taking a comment out shortens everything after it, so a line
 * number read off the shortened text points somewhere else in the real file, and a gate that names
 * the wrong line is read as a gate that is wrong.
 */
function commentsBlanked(css) {
	return css.replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, ' '));
}

// The one file allowed to write <select>: the component that replaces it, whose comments name the
// tag they stand in for.
const SELECT_COMPONENT = join(SRC, 'lib/components/common/Select.svelte');

const LOOKS_AT = new Set(['.svelte', '.ts', '.js']);

// The dialogs the browser draws itself.
const NATIVE_DIALOGS = ['alert', 'confirm', 'prompt'];

async function* walk(dir) {
	for (const entry of await readdir(dir, { withFileTypes: true })) {
		const path = join(dir, entry.name);
		if (path.startsWith(GENERATED)) continue;
		// Nothing the framework or a package manager wrote, for the same reason the colour gate
		// skips them: vitest caches its dependencies under `src/node_modules`, and one of them
		// calls alert(). A gate about OUR interface has to look only at ours.
		if (entry.isDirectory() && (entry.name === '.svelte-kit' || entry.name === 'node_modules')) {
			continue;
		}
		if (entry.isDirectory()) yield* walk(path);
		else yield path;
	}
}

/** The name of the tag an attribute at `index` belongs to, or null if it is not inside one. */
function owningTag(text, index) {
	// Backwards to the nearest tag opening. Scanning backwards rather than matching a whole tag
	// forwards is what makes this survive an attribute value containing a `>`: an inline arrow
	// function, `onclick={() => ...}`, is the common one, and it sits before the attribute often
	// enough that a forward match would stop short and miss exactly what this is looking for.
	for (let at = index; at >= 0; at -= 1) {
		if (text[at] !== '<') continue;
		const rest = text.slice(at + 1, at + 40);
		const name = /^([A-Za-z][\w.-]*)/.exec(rest);
		return name ? name[1] : null;
	}
	return null;
}

function lineOf(text, index) {
	return text.slice(0, index).split('\n').length;
}

const offences = [];

for await (const path of walk(SRC)) {
	if (![...LOOKS_AT].some((ext) => path.endsWith(ext))) continue;

	const text = await readFile(path, 'utf8');
	const where = relative(root, path);

	// --- native tooltips -------------------------------------------------------------------
	if (path.endsWith('.svelte')) {
		for (const match of text.matchAll(/\btitle\s*=/g)) {
			const tag = owningTag(text, match.index);
			// A component's props are its own business; only a real element renders a tooltip.
			if (!tag || tag[0] !== tag[0].toLowerCase()) continue;
			offences.push(
				`${where}:${lineOf(text, match.index)}  title= on <${tag}>  ` +
					'(use the tooltip component, and aria-label for assistive technology)'
			);
		}
	}

	// --- native dialogs --------------------------------------------------------------------
	for (const name of NATIVE_DIALOGS) {
		const declaresItsOwn = new RegExp(
			`(function\\s+${name}\\s*\\(|(?:const|let|var)\\s+${name}\\s*=)`
		).test(text);

		const pattern = declaresItsOwn
			? new RegExp(`\\bwindow\\.${name}\\s*\\(`, 'g')
			: new RegExp(`(?<![.\\w])(?:window\\.)?${name}\\s*\\(`, 'g');

		for (const match of text.matchAll(pattern)) {
			offences.push(
				`${where}:${lineOf(text, match.index)}  ${name}()  ` + '(use the app dialog or a toast)'
			);
		}
	}

	// --- native form controls --------------------------------------------------------------
	// A native <select> cannot be restyled; use the app's Select component instead.
	if (path.endsWith('.svelte') && path !== SELECT_COMPONENT) {
		for (const match of text.matchAll(/<select[\s>]/g)) {
			offences.push(
				`${where}:${lineOf(text, match.index)}  <select>  ` +
					'(use the Select component from $lib/components/common)'
			);
		}
	}

	// --- scrollbars a component tries to hide altogether -----------------------------------
	if (path !== GLOBAL_CSS) {
		for (const match of text.matchAll(
			/scrollbar-width:\s*none|::-webkit-scrollbar\s*{[^}]*display:\s*none/g
		)) {
			offences.push(
				`${where}:${lineOf(text, match.index)}  a hidden scrollbar  ` +
					'(the app styles them globally; hiding one leaves no way to judge how much more there is)'
			);
		}
	}

	// --- focus rings -----------------------------------------------------------------------
	// Removing the default is expected; removing it and putting nothing back is not.
	if (
		/outline:\s*none/.test(text) &&
		!text.includes('--focus-ring') &&
		!text.includes('--focus-outline')
	) {
		const at = text.search(/outline:\s*none/);
		offences.push(
			`${where}:${lineOf(text, at)}  outline: none with no focus style  ` +
				'(a keyboard user cannot see where they are; use var(--focus-ring))'
		);
	}

	// --- the ring token used as an outline ---------------------------------------------------
	// `--focus-ring` is a BOX-SHADOW value: `inset 0 0 0 2px <colour>`. No part of that is anything
	// an outline can be, so `outline: var(--focus-ring)` is invalid the moment the token is
	// substituted and computes back to no outline at all, with no warning anywhere, and with the
	// global `:focus-visible` rule still drawing its ring underneath, so the control looks exactly
	// right while the declaration does nothing. Read with the comments blanked, or this rule's own
	// explanation is an offence.
	for (const match of commentsBlanked(text).matchAll(/outline\s*:\s*var\(\s*--focus-ring\s*\)/g)) {
		offences.push(
			`${where}:${lineOf(text, match.index)}  outline: var(--focus-ring)  ` +
				'(that token is a box-shadow value; the ring comes from app.css :focus-visible)'
		);
	}
}

// The global stylesheet has to keep dressing them. Checked once, rather than per file, because one
// rule set covers every scrolling box in the app, and because the failure this catches is somebody
// deleting it, not somebody adding something.
{
	const text = withoutComments(await readFile(GLOBAL_CSS, 'utf8'));
	for (const rule of SCROLLBAR_RULES) {
		if (rule.ok(text)) continue;
		offences.push(
			`${relative(root, GLOBAL_CSS)}  no ${rule.what}  ` +
				"(scrollbars fall back to the operating system's, in its colours and its proportions)"
		);
	}
	for (const rule of TEXT_BOX_RULES) {
		if (rule.ok(text)) continue;
		offences.push(
			`${relative(root, GLOBAL_CSS)}  no ${rule.what}  ` +
				'(every text box in the app falls back to the browser white, and the only ones that ' +
				'do not are the ones that happen to sit inside a component that dresses them)'
		);
	}
}

if (offences.length > 0) {
	console.error("The browser's own chrome is showing through:\n");
	for (const offence of offences) console.error(`  ${offence}`);
	console.error(
		"\nEverything the user can see wears this app's look. A native tooltip, a native dialog " +
			'or an unstyled focus ring is the operating system drawing part of the interface, in a ' +
			'typeface and a shape nobody here chose.'
	);
	process.exit(1);
}

console.log('ok   no native browser chrome');
