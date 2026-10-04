// The fence around the design system: the ways a new look could be drawn without a primitive, each
// one either refused outright or counted and only ever allowed to fall.
//
// ## What this is for
//
// Each of these is a way to add a look to a screen that no shared component owns:
//
//   - a second library, or a platform mechanism the primitives already wrap (`<dialog>`, the
//     `popover` attribute, `showModal`, a floating-position library): HARD BAN;
//   - the headless library imported anywhere but the primitives folder, so a screen builds its
//     own menu or dialog beside the shared one: HARD BAN;
//   - a raw `<svg>` outside the brand marks: a ratchet, because one plotted curve is legitimate;
//   - a `@keyframes` of a screen's own, a literal duration on a transition, a literal length where
//     a token exists, a box drawn by hand (radius, ground and an edge or an inset in one rule), an
//     inline `style=` attribute: RATCHETS, each recorded at the number found and only ever lower;
//   - a route that never reaches `PageFrame`: a ratchet, with `WHY NOT FRAMED:` for the screen
//     that genuinely is not a page (the file view, the theater's wall).
//
// ## Why ratchets and not bans, where the number is not zero
//
// The same reason `check_handrolled.js` gives: a gate that fails on two hundred files the day it is
// written is weakened until it passes, and a weakened gate reports success. The number is the real
// one, it may only fall, and every conversion locks its own progress in. The bans are the rules
// whose number is zero, which is the one moment a ban costs nothing.
//
// Every rule reads the file's CODE: markup with its comments blanked, stylesheets with theirs.
// `lib/tree.js` is the walk, the stripper and the ratchet. Nothing here is a second copy.

import { readFile } from 'node:fs/promises';
import { dirname, join, normalize, relative } from 'node:path';

import { everySvelteFile, fromSource, ratchet, SOURCE, withoutComments } from './lib/tree.js';

/** The primitives, and the one folder that draws things on purpose. */
const PRIMITIVES = 'lib/components/common/';
const DRAWN_ON_PURPOSE = 'routes/design/';
/** The brand marks are vectors and nothing else is. */
const BRAND = 'lib/brand/';
/** The written judgement for a route that is not a page. */
const NOT_A_PAGE = 'WHY NOT FRAMED:';

/** The stylesheet of a component, comments blanked. Empty when it has none. */
function styleOf(code) {
	const found = code.match(/<style[^>]*>([\s\S]*?)<\/style>/);
	return found ? found[1] : '';
}

/** The markup of a component: everything outside its script and style blocks. */
function markupOf(code) {
	return code
		.replace(/<script[^>]*>[\s\S]*?<\/script>/g, '')
		.replace(/<style[^>]*>[\s\S]*?<\/style>/g, '');
}

/**
 * The bans. Each finds every match in one file; one match is one complaint.
 */
const BANS = [
	{
		id: 'mechanism',
		what: 'a site mechanism a primitive already wraps',
		instead:
			'Modal is every dialog (bits-ui Dialog); MenuButton, RowMenu and ContextMenu are every menu; Tooltip is every tooltip.\n' +
			'    A `<dialog>`, a `popover` attribute, `showModal()`, `showPopover()` or a floating-position library beside them is a second implementation.',
		find: (code) => {
			const markup = markupOf(code);
			return [
				...markup.matchAll(/<dialog\b/g),
				...markup.matchAll(/<[a-z][a-z0-9-]*\b[^>]*\spopover(?:=|\s|>|\/)/g),
				...code.matchAll(/\bshowModal\(|\bshowPopover\(/g),
				...code.matchAll(/from\s+'@floating-ui/g)
			].length;
		},
		only: () => true
	},
	{
		id: 'library-outside-primitives',
		what: "an import from 'bits-ui' outside lib/components/common",
		instead:
			'A screen that needs the library needs a PRIMITIVE: wrap the part once in lib/components/common, declare it,\n' +
			'    draw it on the gallery, and use that. The library imported per screen is a menu or a dialog no gate can see.',
		find: (code) => code.match(/from\s+'bits-ui'/g)?.length ?? 0,
		only: (where) => !where.startsWith(PRIMITIVES) && !where.startsWith(DRAWN_ON_PURPOSE)
	},
	{
		id: 'keyframes',
		what: 'a @keyframes declared in a component',
		instead:
			'every motion is declared once in app.css (`appear`, `turn`, `rise`, `sweep`, `pan` and the rest) and a component names one;\n' +
			'    the reduced-motion rule and the named-motions gate can only reach a motion declared there',
		find: (code) => styleOf(code).match(/@keyframes\s/g)?.length ?? 0,
		only: () => true
	},
	{
		id: 'literal-duration',
		what: 'a transition or animation with a literal duration',
		instead:
			'the duration scale is `--dur-instant`, `--dur-fast`, `--dur-base`, `--dur-slow`, `--dur-ambient`',
		find: (code) =>
			styleOf(code).match(/(?:transition|animation)[^;:]*:[^;]*?\b\d+m?s\b/g)?.length ?? 0,
		only: () => true
	}
];

/**
 * The ratchets. `find` returns how many instances a file holds.
 */
const RATCHETS = [
	{
		id: 'svg',
		what: 'a raw <svg> outside lib/brand',
		instead:
			'an icon is `Icon` (a name from the sanctioned set); a picture is an <img>; only a plotted figure is a vector of its own',
		find: (code) => markupOf(code).match(/<svg\b/g)?.length ?? 0,
		only: (where) => !where.startsWith(BRAND)
	},
	{
		id: 'literal-length',
		what: 'a literal length where a token exists (radius, shadow, gap, padding, margin, font-size)',
		instead: 'the space scale, the radius scale, the elevation scale and the type scale are tokens',
		find: (code) =>
			styleOf(code).match(
				/(?:border-radius|box-shadow|gap|padding|margin|font-size)\s*:[^;]*?\b\d*\.?\d+(?:px|rem|em)\b/g
			)?.length ?? 0,
		only: () => true
	},
	{
		id: 'literal-size',
		what: 'a literal height or width on a component (block-size, inline-size, height, width and their min/max)',
		instead:
			'chrome is measured in tokens: `--control-height`, `--control-height-sm`, `--chip-height*`, `--rail-width`, `--page-footer-height`;\n' +
			'    content flexes, and a reading measure is a token too. A control drawn at 34px beside one drawn at 36 is what this counts.',
		// A media or container query's condition (`@media (max-width: 767px)`) is a breakpoint,
		// not a size on a component: it is taken out before the properties are counted.
		find: (code) =>
			styleOf(code)
				.replace(/@(?:media|container)[^{]*\{/g, '')
				.match(
					/(?:block-size|inline-size|height|width|min-block-size|min-inline-size|max-block-size|max-inline-size|min-height|max-height|min-width|max-width)\s*:[^;]*?\b\d*\.?\d+(?:px|rem|em)\b/g
				)?.length ?? 0,
		only: () => true
	},
	{
		id: 'box',
		what: 'a box drawn by hand: one rule with a radius, a ground, and an edge or an inset',
		instead:
			"import { Panel } from '$lib/components/common', or the primitive that owns the box (Chip, Badge, Button, FormCard, BarPanel)",
		find: (code) => {
			let found = 0;
			for (const rule of styleOf(code).matchAll(/\{([^{}]*)\}/g)) {
				const body = rule[1];
				if (
					/\bborder-radius\s*:/.test(body) &&
					/\bbackground(?:-color)?\s*:/.test(body) &&
					// An edge or an inset: `border`, `border-top` and the like, or a padding. Not
					// `border-radius`, which is the radius already counted above: a rounded ground with
					// no edge and no inset (a progress segment, a sliding highlight) is not a box.
					/\b(?:padding(?:-[a-z]+)?|border(?!-radius)(?:-[a-z]+)?)\s*:/.test(body)
				) {
					found += 1;
				}
			}
			return found;
		},
		only: (where) => !where.startsWith(PRIMITIVES)
	},
	{
		id: 'inline-style',
		what: 'an inline style= attribute',
		instead:
			'a `style:` directive for a value that is data, a class for a look; a look typed on the element is one nothing shares',
		find: (code) => markupOf(code).match(/\sstyle="/g)?.length ?? 0,
		only: () => true
	}
];

/**
 * A route reaches the frame if it, or any component it imports (however deep), renders `PageFrame`
 * or `DoorCard`: the two shapes a screen may wear. Followed through `$lib/` and
 * relative `.svelte` imports, the same way `check_bits_first.js` follows the gallery's.
 */
async function reachesFrame(path, seen = new Set()) {
	if (seen.has(path)) return false;
	seen.add(path);
	let source;
	try {
		source = await readFile(path, 'utf8');
	} catch {
		return false;
	}
	if (/<(?:PageFrame|DoorCard)\b/.test(source)) return true;
	const imports = [];
	for (const one of source.matchAll(/from\s+'(\$lib\/[^']+)'/g)) {
		const target = join(SOURCE, 'lib', one[1].slice('$lib/'.length));
		imports.push(target, `${target}.svelte`);
	}
	for (const one of source.matchAll(/from\s+'(\.\.?\/[^']+\.svelte)'/g)) {
		imports.push(normalize(join(dirname(path), one[1])));
	}
	for (const candidate of imports) {
		if (candidate.endsWith('.svelte') && (await reachesFrame(candidate, seen))) return true;
	}
	return false;
}

const complaints = [];
const counts = Object.fromEntries(RATCHETS.map((rule) => [rule.id, 0]));
const offenders = Object.fromEntries(RATCHETS.map((rule) => [rule.id, []]));
let unframed = 0;
const unframedWhere = [];
let scanned = 0;

for (const path of await everySvelteFile(SOURCE)) {
	const where = fromSource(path);
	if (where.startsWith(DRAWN_ON_PURPOSE) && !where.endsWith('+page.svelte')) continue;
	const source = await readFile(path, 'utf8');
	const code = withoutComments(source);
	scanned += 1;

	for (const ban of BANS) {
		if (!ban.only(where)) continue;
		const found = ban.find(code);
		if (found > 0) {
			complaints.push(`${where}: ${found} of ${ban.what}.\n    ${ban.instead}`);
		}
	}

	if (where.startsWith(DRAWN_ON_PURPOSE)) continue;

	for (const rule of RATCHETS) {
		if (!rule.only(where)) continue;
		const found = rule.find(code);
		if (found === 0) continue;
		counts[rule.id] += found;
		offenders[rule.id].push(`${found}x  ${where}`);
	}

	if (/^routes\/.*\+page\.svelte$/.test(where) && !source.includes(NOT_A_PAGE)) {
		if (!(await reachesFrame(path))) {
			unframed += 1;
			unframedWhere.push(where);
		}
	}
}

/* A scan that found nothing would pass, silently, forever. */
if (scanned < 200) {
	console.error(
		`\ndesign-fence: read only ${scanned} components under src/. That is too few to be right.\n`
	);
	process.exit(1);
}

for (const rule of RATCHETS) {
	const complaint = await ratchet('design-fence', rule.id, counts[rule.id], {
		what: rule.what,
		instead: `Instead: ${rule.instead}`,
		offenders: offenders[rule.id].sort().slice(0, 10),
		script: 'check_design_fence.js'
	});
	if (complaint) complaints.push(complaint);
}

const framed = await ratchet('design-fence', 'unframed-routes', unframed, {
	what: 'a route that never reaches PageFrame or DoorCard',
	instead:
		'Every route wears the frame. A screen that genuinely is not a page (the file view,\n' +
		`    the theater's wall) says so in the file, on one line, in this exact spelling: ${NOT_A_PAGE} <the reason>`,
	offenders: unframedWhere,
	script: 'check_design_fence.js'
});
if (framed) complaints.push(framed);

if (complaints.length > 0) {
	console.error('\nThe fence around the design system.\n');
	for (const complaint of complaints) console.error(`  ${complaint}\n`);
	process.exit(1);
}

console.log(
	`design-fence: ${scanned} components; ` +
		RATCHETS.map((rule) => `${rule.id} ${counts[rule.id]}`).join(', ') +
		`, unframed-routes ${unframed} (at the recorded numbers); no second library, no second mechanism`
);
