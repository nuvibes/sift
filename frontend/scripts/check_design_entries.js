// Every primitive declares itself, no two claim one role, and the gallery draws each one by name.
//
// ## What this holds
//
// The primitives folder is the design system. Each primitive carries ONE typed declaration in its
// own module script (`$lib/design/entry`): its name, where it sits, what it is for, what it is
// built on, and which looks it has. The gallery reads the declarations to draw its index and each
// section's header, so there is no second list to keep in step with the folder and the barrel. This
// gate is what makes the declaration a rule rather than a habit:
//
//   1. Every `.svelte` under `lib/components/common/` (not a test, not a harness) declares itself,
//      and the declaration is well-formed: a category from the list, a role that is a sentence, a
//      basis in the one spelling.
//   2. No two primitives claim the same ROLE. A second component with the same purpose is the
//      first one written twice, and this is the only place in the tree that can say so before the
//      copy has callers. (The hand-rolled ratchet counts copies OUTSIDE the primitives; this is the
//      copy inside them.)
//   3. The basis agrees with the file. `bits-ui:Name` is refused unless the file imports that name
//      from the library, and a file importing the library must say so in its basis, so the
//      declaration cannot describe a component that is built differently.
//   4. The barrel exports every declared primitive.
//   5. The gallery draws every declared primitive BY NAME (a `<Specimen of="Name">`) unless the
//      file says `NOT ON THE GALLERY:` with a reason (a host that renders nothing; a singleton the
//      layout already draws). Reachability through some other component's import is not enough:
//      a primitive drawn only inside something else has no section, no header and no states of
//      its own on the page.
//
// Every one of these is a hard ban: the count is zero on all five, and a ratchet at zero is a ban
// with an extra file.
//
// ## Why the spelling of a basis is imported rather than copied
//
// `BASIS_PART` and `CATEGORIES` are read from `$lib/design/entry.ts` itself. Node strips the types
// on the way in, so the gate and the type system hold one regex and one list, and cannot disagree.

import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { BASIS_PART, CATEGORIES } from '../src/lib/design/entry.ts';
import { everySvelteFile, fromSource, SOURCE, withoutComments } from './lib/tree.js';

const PRIMITIVES = join(SOURCE, 'lib', 'components', 'common');
const BARREL = join(PRIMITIVES, 'index.ts');
const GALLERY = join(SOURCE, 'routes', 'design', '+page.svelte');

/** The written judgement for a primitive the gallery is not asked to draw. Same spelling as `check_bits_first.js`. */
const DRAWS_NOTHING = 'NOT ON THE GALLERY:';
/** Test-only harnesses and probes. Not interface. */
const HARNESS = /(Harness|Probe)\.svelte$|\.test\.svelte$/;

/** The declaration, as text. Only the literal is read; the types around it are Svelte's business. */
const DECLARATION = /export const design = (\{[\s\S]*?\}) satisfies DesignEntry;/;

/**
 * The literal, evaluated. It is a plain object of strings and string arrays written by hand in this
 * repository, read at build time, and `svelte-check` has already typed it, so evaluating it is
 * reading it. Anything that is not that shape is a complaint below, not a crash.
 */
function parseDeclaration(literal) {
	return new Function(`return (${literal});`)();
}

const complaints = [];
const entries = [];

for (const path of await everySvelteFile(PRIMITIVES)) {
	if (HARNESS.test(path)) continue;
	const where = fromSource(path);
	const source = await readFile(path, 'utf8');
	const found = source.match(DECLARATION);
	if (!found) {
		complaints.push(
			`${where}: no design declaration. Every primitive says what it is, in its own module script:\n` +
				`      export const design = { name, category, role, basis, states } satisfies DesignEntry;\n` +
				`    See src/lib/design/entry.ts for what each field means.`
		);
		continue;
	}
	let entry;
	try {
		entry = parseDeclaration(found[1]);
	} catch (error) {
		complaints.push(
			`${where}: the design declaration does not read as an object literal (${error.message}).`
		);
		continue;
	}

	const expected = where.slice('lib/components/common/'.length, -'.svelte'.length);
	if (entry.name !== expected) {
		complaints.push(
			`${where}: declares name '${entry.name}', and the file is ${expected}.svelte. The name is the file's.`
		);
	}
	if (!CATEGORIES.includes(entry.category)) {
		complaints.push(
			`${where}: category '${entry.category}' is not one of ${CATEGORIES.join(', ')}.`
		);
	}
	if (typeof entry.role !== 'string' || entry.role.trim().split(/\s+/).length < 3) {
		complaints.push(`${where}: the role must be a sentence saying what this is FOR, not a label.`);
	}
	const parts = String(entry.basis)
		.split(';')
		.map((part) => part.trim());
	for (const part of parts) {
		if (!BASIS_PART.test(part)) {
			complaints.push(
				`${where}: basis part '${part}' is not in the one spelling: bits-ui:Name, site:<tag>, composes:A,B or own.`
			);
		}
	}

	// The basis against the file's imports, read from the code rather than from prose.
	const code = withoutComments(source);
	const imported = new Set(
		[...code.matchAll(/import\s*\{([^}]+)\}\s*from\s*'bits-ui'/g)].flatMap((one) =>
			one[1]
				.split(',')
				.map((name) => name.trim().split(/\s+as\s+/)[0])
				.filter(Boolean)
		)
	);
	const claimed = parts
		.filter((part) => part.startsWith('bits-ui:'))
		.map((part) => part.slice('bits-ui:'.length));
	for (const name of claimed) {
		if (!imported.has(name)) {
			complaints.push(
				`${where}: basis names bits-ui:${name}, and the file does not import ${name} from 'bits-ui'.`
			);
		}
	}
	// Portal and the utilities are plumbing, not what a component is built on.
	const built = [...imported].filter(
		(name) => !['Portal', 'BitsConfig', 'IsUsingKeyboard'].includes(name)
	);
	if (built.length > 0 && claimed.length === 0) {
		complaints.push(
			`${where}: imports ${built.join(', ')} from bits-ui and its basis does not say so.`
		);
	}

	entries.push({ where, entry, exempt: source.includes(DRAWS_NOTHING) });
}

/* A scan that found nothing would pass, silently, forever. */
if (entries.length < 40) {
	console.error(
		`\ndesign-entries: found only ${entries.length} primitives under src/lib/components/common.\n  That is too few to be right: the folder moved, or this gate is reading the wrong one.\n`
	);
	process.exit(1);
}

// 2. One role, one primitive.
const byRole = new Map();
for (const { where, entry } of entries) {
	const key = String(entry.role)
		.toLowerCase()
		.replace(/[^a-z0-9 ]/g, '')
		.replace(/\s+/g, ' ')
		.trim();
	if (byRole.has(key)) {
		complaints.push(
			`${where} and ${byRole.get(key)} both claim the role '${entry.role}'.\n` +
				`    Two primitives with one purpose are one primitive written twice. Extend the first, or\n` +
				`    say in the role what the second does that the first cannot.`
		);
	} else byRole.set(key, where);
}

// 4. The barrel names every one of them.
const barrel = withoutComments(await readFile(BARREL, 'utf8'), { markup: false });
const exported = new Set(
	[...barrel.matchAll(/export \{ default as (\w+) \} from '\.\/(\w+)\.svelte'/g)].map(
		(one) => one[1]
	)
);
for (const { where, entry } of entries) {
	if (!exported.has(entry.name)) {
		complaints.push(
			`${where}: not exported from lib/components/common/index.ts. Add\n      export { default as ${entry.name} } from './${entry.name}.svelte';`
		);
	}
}

// 5. The gallery draws each by name, where the gallery is. It is kept in a separate repository
//    (see .gitignore); a clone of this one has no gallery, and this half says so rather than
//    passing in silence.
const galleryHere = existsSync(GALLERY);
/* Markup comments only. The page's script holds a `/*` inside a string, and blanking from there to
   the next close would take most of the page with it, and a section is markup, not script. */
const gallery = galleryHere
	? withoutComments(await readFile(GALLERY, 'utf8'), { block: false, line: false })
	: '';
const drawn = new Set();
for (const one of gallery.matchAll(/<Specimen\b[^>]*?\bof=(?:"([A-Za-z]+)"|\{\[([^\]]+)\]\})/g)) {
	if (one[1]) drawn.add(one[1]);
	else for (const name of one[2].matchAll(/'([A-Za-z]+)'/g)) drawn.add(name[1]);
}
const known = new Set(entries.map((one) => one.entry.name));
for (const name of drawn) {
	if (!known.has(name))
		complaints.push(
			`routes/design/+page.svelte draws a Specimen of='${name}', and nothing declares that name.`
		);
}
for (const { where, entry, exempt } of entries) {
	if (!galleryHere || exempt || drawn.has(entry.name)) continue;
	complaints.push(
		`${where}: not drawn on the gallery. Add a section to src/routes/design/+page.svelte:\n` +
			`      <Specimen of="${entry.name}" help="..."> ...the real component, in every state it declares... </Specimen>\n` +
			`    If it genuinely draws nothing, say so in the file: ${DRAWS_NOTHING} <the reason>`
	);
}

if (complaints.length > 0) {
	console.error('\nEvery primitive declares itself, once, and the gallery draws it.\n');
	for (const complaint of complaints) console.error(`  ${complaint}\n`);
	process.exit(1);
}

console.log(
	`design-entries: ${entries.length} primitives declared, ${byRole.size} distinct roles, ` +
		(galleryHere
			? `${drawn.size} drawn on the gallery by name`
			: 'the gallery is not in this tree, so that half was not measured')
);
