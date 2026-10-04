#!/usr/bin/env node
/*
 * The server's shapes are described ONCE, by the server.
 *
 * ## The failure this is for
 *
 * The client's request and response types are generated from the schema the server itself produces
 * (`openapi.json`, regenerated and diffed in CI), so an endpoint that changes cannot land without
 * the types changing with it. What defeats that is the interface writing the same shapes out again
 * by hand beside the generated ones, and then reading its own copy.
 *
 * A hand-written copy does not fail when the server moves. It goes quietly wrong: a field declared
 * always present that the server sends only sometimes, a row missing fields the server has been
 * sending for weeks. The types agree with themselves, the tests pass, and the screens are wrong.
 *
 * ## The rule
 *
 * Two rules, and they are deliberately different in kind.
 *
 * **A shape the server already has is not written out again.** Any exported or local object type in
 * the client whose field names are a subset of some schema's fields, with at least MEANINGFUL_SIZE
 * of them, is that schema written twice. Write `components['schemas']['X']`, or `Pick<...>` of it
 * when a screen genuinely wants a narrower slice. Both stay correct by construction, and a `Pick`
 * of a field the server removes is a build error rather than a screen that stops filling in.
 *
 * **A request never names its own answer inline.** `api.get<{ items: Row[] }>(...)` describes a
 * reply in the one place nothing checks it. It is a bright line with no judgement in it: the type
 * argument must be a NAME, so that what it names can be looked at.
 *
 * ## Why the size threshold, and why it is not lower
 *
 * Two fields collide by accident. `{ id, name }` is half the schemas in the document and also every
 * dropdown row anybody has ever written; refusing it would mean arguing about coincidences instead
 * of fixing copies. Three is where a match stops being luck: across the client, three-field matches
 * are few and nearly all real, two-field matches are many and mostly coincidence.
 *
 * The small ones are not left unguarded. They are caught by the second rule instead, which does not
 * care how many fields there are: a two-field reply written inline at the call is refused for being
 * inline, whatever it says.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import ts from 'typescript';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..');
const SOURCE = join(ROOT, 'src');
const SCHEMA = join(ROOT, 'openapi.json');

/** Below this many fields, a match is as likely to be a coincidence as a copy. See the note above. */
const MEANINGFUL_SIZE = 3;

/**
 * Types that match a schema by accident, each with the reason it is not a copy.
 *
 * Every entry here is a claim somebody can check by opening the file: the fields line up with a
 * schema and mean something else. Nothing goes on this list to make the gate pass.
 */
const COINCIDENCES = [
	{
		where: 'lib/grid/cards.svelte.ts',
		type: 'Page',
		schema: 'StashWaitingPage',
		because:
			'the generic page of ANY wall (`Page<T>`: rows, total, offset), typed over every server ' +
			'page the walls read. The Stash waiting page is one instance of ' +
			"that shape the server answers; the client's `Page<T>` is the shape itself, and a " +
			'generic cannot be a copy of one concrete schema.'
	},
	{
		where: 'lib/components/organize/Thumb.svelte',
		type: 'Props',
		schema: 'PreviewView',
		because:
			"a component's own props: what to draw and where pressing it goes. `href` is a route " +
			'in this application and is not a field on anything the server sends.'
	},
	{
		where: 'lib/entity/records.svelte.ts',
		type: 'RecordLink',
		because:
			'where a record VALUE leads, worked out in the browser: the entity kind and id the value ' +
			'names, and the in-app address built from them by `pageOf`. The server sends the id ' +
			'beside the value and never an address at all: `href` here is a route in this ' +
			"application. `PreviewView` is the workbench's card picture, whose `kind` says how to " +
			'address an IMAGE and whose `href` may be null because a card is often not a link; both ' +
			'of these are always present, because a link with no address is not one.'
	},
	{
		where: 'lib/edit/geometry.ts',
		type: 'Box',
		schema: 'EditStep',
		because:
			'a rectangle on the screen, in PIXELS, and all four numbers are required. The crop step ' +
			'that eventually goes to the server is built from one of these and is not one: its four ' +
			'are optional, because a step that is a rotate has no rectangle at all.'
	},
	{
		where: 'lib/components/entity/aimed-page.svelte.ts',
		type: 'PageAim',
		schema: 'SiteView',
		because:
			'what the SCREEN is about, so a link dropped on the window lands where the page says. ' +
			'`kind` here is which of the five kinds of thing the page shows (a person, a site, a ' +
			'collection, a tag, a photo set), and `SiteView.kind` is a category of website. ' +
			'The two words mean different things, so a `Pick<>` of that schema would type this ' +
			'field as the wrong one and be wrong in the direction nothing would notice.'
	},
	{
		where: 'lib/components/entity/EntityDropZone.svelte',
		type: 'Props',
		schema: 'SiteView',
		because:
			"the same three values as `PageAim` above, as a component's props: this is the thing " +
			'that publishes them. `kind` is which kind of page this is, not a category of website.'
	},
	{
		where: 'lib/components/entity/EntityHistory.svelte',
		type: 'Props',
		schema: 'JobView',
		because:
			"a component's props: which kind of thing a history is being drawn of, which one of " +
			'them, and what to call them while the read is out. `JobView` is a BACKGROUND JOB, and ' +
			'its `subject` is which table a queued piece of work is about beside a separate ' +
			"`subject_id`; these three are the panel's own question, and two of the three words " +
			'that collide are `id` and `name`. A `Pick<>` of a job would type the panel by ' +
			'something it has nothing to do with.'
	},
	{
		where: 'lib/grid/justify.ts',
		type: 'PlacedTile',
		schema: 'AssetDetail',
		because:
			'what the layout WORKED OUT: a width and a height in pixels, both required, for a tile ' +
			'that has been placed in a row. The server sends neither: its own size is optional and ' +
			'may be null, which is exactly the case this arithmetic exists to resolve.'
	}
];

/** The one file that IS the generated schema, and the client that reads it. */
const GENERATED = ['lib/api/schema.d.ts'];

function* walk(where) {
	for (const entry of readdirSync(where)) {
		const path = join(where, entry);
		if (statSync(path).isDirectory()) {
			yield* walk(path);
			continue;
		}
		if (path.endsWith('.ts') || path.endsWith('.svelte')) yield path;
	}
}

/** The script half of a Svelte file, or the whole of a TypeScript one. */
function scriptOf(path, text) {
	if (!path.endsWith('.svelte')) return text;
	const blocks = [...text.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)];
	return blocks.length > 0 ? blocks.map((block) => block[1]).join('\n;\n') : null;
}

function fieldsOf(schema) {
	return new Set(Object.keys(schema.properties ?? {}));
}

const spec = JSON.parse(readFileSync(SCHEMA, 'utf8'));
const schemas = new Map();
for (const [name, schema] of Object.entries(spec.components?.schemas ?? {})) {
	const fields = fieldsOf(schema);
	if (fields.size >= MEANINGFUL_SIZE) schemas.set(name, fields);
}

/** The schema this type is a copy of, if it is a copy of one. */
function copyOf(fields) {
	if (fields.size < MEANINGFUL_SIZE) return null;
	for (const [name, theirs] of schemas) {
		if (fields.size > theirs.size) continue;
		let all = true;
		for (const field of fields) {
			if (!theirs.has(field)) {
				all = false;
				break;
			}
		}
		if (all) return name;
	}
	return null;
}

const copies = [];
const inlined = [];

for (const path of walk(SOURCE)) {
	const rel = relative(SOURCE, path).split('\\').join('/');
	if (GENERATED.includes(rel)) continue;
	const text = readFileSync(path, 'utf8');
	const script = scriptOf(path, text);
	if (script === null) continue;
	const source = ts.createSourceFile(path, script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
	const lineOf = (node) => source.getLineAndCharacterOfPosition(node.getStart()).line + 1;

	const visit = (node) => {
		let name = null;
		let members = null;
		if (ts.isInterfaceDeclaration(node)) {
			name = node.name.text;
			members = node.members;
		} else if (ts.isTypeAliasDeclaration(node) && ts.isTypeLiteralNode(node.type)) {
			name = node.name.text;
			members = node.type.members;
		}
		if (name !== null && members !== null) {
			const fields = new Set(
				members
					.filter((member) => ts.isPropertySignature(member))
					.map((member) => member.name.getText().replace(/^['"]|['"]$/g, ''))
			);
			const schema = copyOf(fields);
			const excused = COINCIDENCES.some((one) => one.where === rel && one.type === name);
			if (schema !== null && !excused) {
				copies.push({ where: `${rel}:${lineOf(node)}`, name, schema });
			}
		}

		/* A reply described where the request is made. The shape of the type argument is the whole
		   test: a name is fine wherever it points, because a name can be followed. */
		if (
			ts.isCallExpression(node) &&
			ts.isPropertyAccessExpression(node.expression) &&
			ts.isIdentifier(node.expression.expression) &&
			node.expression.expression.text === 'api' &&
			node.typeArguments?.length === 1 &&
			ts.isTypeLiteralNode(node.typeArguments[0])
		) {
			inlined.push({
				where: `${rel}:${lineOf(node)}`,
				call: `api.${node.expression.name.text}`,
				said: node.typeArguments[0].getText().replace(/\s+/g, ' ')
			});
		}

		ts.forEachChild(node, visit);
	};
	visit(source);
}

if (copies.length > 0 || inlined.length > 0) {
	if (copies.length > 0) {
		console.error(`\nThe server's own shapes, written out a second time in the client:\n`);
		for (const one of copies) {
			console.error(`  ${one.where}`);
			console.error(`    ${one.name} is components['schemas']['${one.schema}'] written again.`);
			console.error(
				`    Write \`type ${one.name} = components['schemas']['${one.schema}']\`, or a` +
					` \`Pick<>\`\n    of it where this really wants fewer fields.\n`
			);
		}
	}
	if (inlined.length > 0) {
		console.error(`\nA reply described where it is asked for, rather than named:\n`);
		for (const one of inlined) {
			console.error(`  ${one.where}`);
			console.error(`    ${one.call}<${one.said}>(...)`);
			console.error(
				`    Name the schema this route answers, \`components['schemas']['...']\`,\n` +
					`    so that what the client believes about it is somewhere it can be read.\n`
			);
		}
	}
	console.error(
		'The client is generated from the schema the server produces, and CI regenerates it and\n' +
			'refuses a mismatch. A copy beside it is outside all of that: it does not fail when the\n' +
			'server moves, it goes quietly wrong, and the screen is where somebody finds out.\n'
	);
	process.exit(1);
}

console.log(
	`one server type: clean (${schemas.size} schemas, ` +
		`${COINCIDENCES.length} named coincidence${COINCIDENCES.length === 1 ? '' : 's'})`
);
