// A class handed to a COMPONENT, with a scoped rule for it in the same file.
//
// ## The failure this is for
//
// A class written on a component lands on an element compiled in THAT component's file, so it
// carries that file's scope hash and not this one's. A scoped rule here never reaches it. The fix
// is always `:global`, and the trap is that nothing says so:
//
//   - The **dead-CSS** check does not catch it. It asks whether a selector matches anything, sees
//     the literal class name in this file's markup, and takes that as proof the rule is used. The
//     rule reads as live and does nothing.
//   - The **styled** check does not catch it either. That one asks the opposite question (does
//     this class have a rule?), and the answer is yes. There is a rule. It is simply unreachable.
//
// So the two checks between them cover "a rule with no element" and "an element with no rule", and
// this is the third case: a rule and an element that cannot see each other. The symptom is never a
// stylesheet that looks wrong. It is a control drawn bare, or drawn wherever the flow puts it.
//
// ## The one thing it must not do
//
// A class can legitimately be on BOTH a component and a plain element in the same file: the
// search box hands `.token` to a chip and also writes it on a suggestion row, and the settings
// shell hands `.item` to a Pressable and also to an `<a>`. In those files the scoped rule is
// correct: it reaches the plain element. So a class is only reported when it is handed to a
// component and appears on no plain element in that file.

import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = join(HERE, '..', 'src');

async function everySvelteFile(dir) {
	const found = [];
	for (const entry of await readdir(dir, { withFileTypes: true })) {
		if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
		const full = join(dir, entry.name);
		if (entry.isDirectory()) found.push(...(await everySvelteFile(full)));
		else if (entry.name.endsWith('.svelte')) found.push(full);
	}
	return found;
}

/** Every class written in one `class=` attribute, literal words only. */
function words(text) {
	return text.match(/[A-Za-z][\w-]*/g) ?? [];
}

/*
 * Every opening tag in a piece of markup, with its attributes, scanned rather than pattern-matched.
 *
 * A regular expression that allows either "any character that is not an angle bracket" or "a quoted
 * string" is correct for HTML and not for Svelte: an attribute value here is an expression, and an
 * expression may contain `<`.
 *
 *     <Icon filled={set !== null && value <= set} />
 *     <span class="count">{value}</span>
 *
 * At the `<` in `value <= set` such a pattern backtracks into the quoted branch, takes the closing
 * quote of an earlier attribute as an opening one, and runs on to the next quote in the file, so
 * `count` would be read as an attribute of `Icon`. The same mis-pairing can swallow a class that
 * really was handed to a component into a preceding plain tag, and then this check would quietly
 * pass what it exists to find.
 *
 * So: find a tag name, then walk forward one character at a time, remembering whether the cursor is
 * inside a quoted string or inside a braced expression, and stop at the first `>` that is in
 * neither. A brace-depth counter is what makes `{a > b ? '<' : '>'}` a single attribute rather than
 * the end of the tag.
 */
function tagsIn(markup) {
	const found = [];
	const NAME = /<([A-Za-z][\w.:-]*)/g;
	for (const start of markup.matchAll(NAME)) {
		let at = start.index + start[0].length;
		let quote = null;
		let depth = 0;
		while (at < markup.length) {
			const ch = markup[at];
			if (quote !== null) {
				if (ch === quote) quote = null;
			} else if (ch === '"' || ch === "'") {
				quote = ch;
			} else if (ch === '{') {
				depth += 1;
			} else if (ch === '}') {
				depth = Math.max(0, depth - 1);
			} else if (ch === '>' && depth === 0) {
				break;
			}
			at += 1;
		}
		found.push({ name: start[1], attrs: markup.slice(start.index + start[0].length, at) });
	}
	return found;
}

const problems = [];
let scanned = 0;

for (const path of await everySvelteFile(SOURCE)) {
	const where = relative(SOURCE, path).replaceAll('\\', '/');
	const body = await readFile(path, 'utf8');

	const opens = body.indexOf('<style>');
	if (opens === -1) continue; // nothing to be unreachable
	scanned += 1;

	/* From after the script, or from the top when there is no script at all. `lastIndexOf`
	   answers -1 for a component that has none, and `slice(-1, ...)` counts from the end, so
	   without this the markup read would be the file's last character. */
	const afterScript = body.lastIndexOf('</script>');
	const markup = body
		.slice(afterScript === -1 ? 0 : afterScript, opens)
		.replace(/<!--[\s\S]*?-->/g, '');
	const style = body.slice(opens).replace(/\/\*[\s\S]*?\*\//g, '');

	/*
	 * Tags are split by the case of their first letter, which is exactly how Svelte itself decides:
	 * `<div>` is an element and `<Button>` is a component. The attribute value is read out of both
	 * the quoted and the braced form (`class="a b"` and `class={cond ? 'a' : 'b'}`), because a
	 * class handed in conditionally is handed in.
	 */
	const handed = new Set();
	const plain = new Set();
	for (const tag of tagsIn(markup)) {
		const into = /^[A-Z]/.test(tag.name) && !tag.name.startsWith('svelte:') ? handed : plain;
		for (const attr of tag.attrs.matchAll(/\sclass=(?:"([^"]*)"|\{([^}]*)\})/g)) {
			for (const word of words(attr[1] ?? attr[2] ?? '')) into.add(word);
		}
	}
	/* `class:name` is only ever on a real element: Svelte refuses the directive on a component. */
	for (const match of markup.matchAll(/class:([A-Za-z][\w-]*)/g)) plain.add(match[1]);
	if (handed.size === 0) continue;

	/* Global rules are the CORRECT answer, so they are taken out before looking. What is left is
	   every scoped selector in the file. */
	const scoped = style.replace(/:global\([^)]*\)/g, ' ');

	const unreachable = [...handed]
		.filter((one) => !plain.has(one))
		.filter((one) => new RegExp(`\\.${one}(?![\\w-])`).test(scoped));

	if (unreachable.length > 0) problems.push({ where, unreachable });
}

/* A scan that found nothing would pass forever. */
if (scanned < 80) {
	console.error(
		`\nhanded-class: only ${scanned} components with a stylesheet found under src/.\n` +
			`  That is too few to be right: the tree moved, or this check is reading the wrong one.\n`
	);
	process.exit(1);
}

if (problems.length > 0) {
	console.error('\nA scoped rule for a class handed to a component. It reaches nothing.\n');
	console.error(
		"  The class lands on an element compiled in the COMPONENT's file, carrying that file's\n" +
			"  scope hash rather than this one's, so a scoped selector here can never match it. The\n" +
			'  rule is not dead (the dead-CSS check can see the class in the markup and believes it):\n' +
			'  it is simply unreachable, and the control is drawn without whatever it said.\n\n' +
			'  Wrap the selector: `.parent :global(.thing) { ... }`, scoped under something in THIS\n' +
			'  file so it stays a rule about this screen rather than about every `.thing` in the app.\n'
	);
	for (const one of problems) {
		console.error(`    ${one.where}`);
		for (const name of one.unreachable) console.error(`      .${name}`);
	}
	console.error('');
	process.exit(1);
}

console.log(
	`handed-class: ${scanned} components checked; every class handed to a component is either ` +
		`dressed globally or not dressed here`
);
