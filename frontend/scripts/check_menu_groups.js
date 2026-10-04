// Every menu is divided into its parts by groups, and the line between two parts is the group's.
//
// A menu is read in parts (go there, file it, keep a copy, change it, who sees it, and the one that
// destroys something last), and the lines are what make the parts visible. `ContextMenuGroup` is
// the one thing that draws a line; a declared verb names its part with `group` and `menuGroups`
// puts the parts in their one order. What this refuses, in code with its comments taken out
// (`lib/menu-groups.js`, which the unit suite drives with planted lines:
// `src/lib/build/menu-groups-gate.test.ts`): a `ContextMenuSeparator` placed by hand outside the
// primitives; a menu written as markup with no group whose rows are of two kinds, however few (a
// row that destroys beside one that does not, or a setting beside a row that acts); a markup menu
// of more than five rows with no group; and a declared list of two or more verbs with one that
// names no group and is not destructive, whatever its length. Not a ratchet: there are none left,
// so one is a failure.

import { readFile } from 'node:fs/promises';

import { menuFaultsIn } from './lib/menu-groups.js';
import { everyFile, fromSource, SOURCE, withoutComments } from './lib/tree.js';

/** The primitives, where the line itself is drawn. */
const PRIMITIVES = 'lib/components/common/';

/** A test plants what it asserts about. */
const A_TEST = /\.(test|spec)\.ts$|\.test\.svelte$/;

const offenders = [];

for (const path of await everyFile(SOURCE, ['.ts', '.svelte'])) {
	const where = fromSource(path);
	if (A_TEST.test(where) || where.endsWith('.d.ts')) continue;
	const code = withoutComments(await readFile(path, 'utf8'));
	for (const { line, what } of menuFaultsIn(code, { primitive: where.startsWith(PRIMITIVES) })) {
		offenders.push(`${where}:${line}  ${what}`);
	}
}

if (offenders.length > 0) {
	console.error(
		`\n  ${offenders.length} menu(s) not divided into groups:\n    ` +
			offenders.join('\n    ') +
			'\n  Instead: give each declared verb a `group` (see VerbGroup in' +
			' lib/components/common/verbs.ts), and wrap each part of a menu written as markup in a' +
			' ContextMenuGroup, which draws the line in front of it.\n'
	);
	process.exit(1);
}

console.log('Menu groups: every menu is divided by its groups, and no line is placed by hand.');
