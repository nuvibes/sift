// Puts every font Sift serves into static/fonts/, and writes the CSS that declares them.
//
// Nothing is fetched at runtime from anywhere but Sift's own origin. That is a privacy property
// before it is a performance one: a font CDN would hand a third party the address of everyone who
// opens the app, every time they open it, and Sift is meant to work with no internet at all.
//
// Two jobs:
//
// 1. The text faces, taken from their packages, latin and latin-ext only. The other subsets are
//    weight for scripts this interface is not written in. Their CSS is generated from the packages'
//    own, so the unicode ranges are never copied by hand and cannot drift from the files they
//    describe.
//
// 2. The icon font, subset. The full variable font is 5.2 MB (several thousand icons across four
//    axes), and the app names about thirty. The font addresses an icon two ways: its name
//    ("settings") is a ligature, and its glyph also sits at a codepoint (U+E8B8). Subsetting by NAME
//    does not work: the subsetter has to keep every ligature reachable from the letters it is given,
//    and thirty icon names reach most of the alphabet, so it keeps most of the font: 407 KB
//    measured, against 4 KB by codepoint. So each name is shaped once to find its glyph, the glyph
//    is traced back to its codepoint, and the font is subset by codepoint with ligature closure off.
//    With the ligatures gone the app renders the codepoint itself, which is why the map is written.
//
// Run by `npm run build` and `npm run dev`. Every output is generated; none is committed.

import { readFile, writeFile, mkdir, copyFile, readdir, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as fontkit from 'fontkit';
import subsetFont from 'subset-font';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');

const FONT_DIR = join(root, 'static/fonts');
const GENERATED = join(root, 'src/lib/generated');
const ICON_SOURCE = join(root, 'node_modules/material-symbols/material-symbols-rounded.woff2');
const ICON_LIST = join(root, 'src/lib/design/icons.ts');
const ICON_FILE = 'material-symbols-rounded-subset.woff2';

// A tripwire, not a target: the real subset is an order of magnitude under this. Anything near it
// means an axis came unpinned or the subset fell back to keeping the whole font.
const MAX_ICON_BYTES = 150 * 1024;

// The scripts this interface is written in. A title in another one still shows (the browser falls
// back to the system face for it), which is the right trade against carrying every script.
const SUBSETS = ['latin', 'latin-ext'];

// Every face Sift offers, in either role: five for headings and numbers, six for everything read.
// See the [data-face-display] and [data-face-body] blocks in `src/app.css`, which are the only
// place a face is named for the page; a pairing is a preset of one of each, in `lib/theme/theme.svelte.ts`.
//
// ELEVEN FAMILIES DECLARED IS NOT ELEVEN FAMILIES DOWNLOADED. A browser fetches a @font-face file only
// when something on the page is actually set in it, so the families nobody has chosen cost about a
// kilobyte of CSS between them and nothing on the wire. The two in force cost what the two in force
// have always cost.
//
// Every one of these is checked for tabular figures by `src/lib/design/figures.test.ts`, which reads
// these same files. A face that cannot prove it carries them is not offered, because the property
// that keeps a column of durations from jittering does nothing at all (and says nothing at all)
// on a face without the figures to do it with.
//
// `figures` marks a face offered for headings and numbers. Each of those is declared a second time
// under the name `<Face> Figures`, covering the ten digits and nothing else, from the SAME file
// (fetched once). A text face with no figures of its own that will hold still (DM Sans) puts that
// name first in its stack, so its numerals are drawn in the Main face in force, which is the face
// for numbers already, and the column holds still with two families on screen as before.
const TEXT_FACES = [
	{ package: '@fontsource-variable/archivo', prefix: 'archivo', figures: true },
	{ package: '@fontsource-variable/instrument-sans', prefix: 'instrument-sans' },
	{ package: '@fontsource-variable/space-grotesk', prefix: 'space-grotesk', figures: true },
	{ package: '@fontsource-variable/inter', prefix: 'inter' },
	{ package: '@fontsource-variable/geist-mono', prefix: 'geist-mono', figures: true },
	{ package: '@fontsource-variable/geist', prefix: 'geist' },
	{ package: '@fontsource-variable/manrope', prefix: 'manrope', figures: true },
	{ package: '@fontsource-variable/public-sans', prefix: 'public-sans' },
	{ package: '@fontsource-variable/jetbrains-mono', prefix: 'jetbrains-mono', figures: true },
	{ package: '@fontsource-variable/sora', prefix: 'sora' },
	{ package: '@fontsource-variable/dm-sans', prefix: 'dm-sans' }
];

// The ten digits, U+0030 to U+0039: the whole of what a figures alias covers.
const DIGITS = 'U+30-39';

await rm(FONT_DIR, { recursive: true, force: true });
await mkdir(FONT_DIR, { recursive: true });
await mkdir(GENERATED, { recursive: true });

// A font ships with its licence text beside it. The Open Font License asks for its text to go
// with every copy of the font, and the Apache licence for its notice; a package without one, or
// one whose text is not the licence it is declared under, stops the build.
async function shipLicence(dir, prefix, mustName) {
	let text;
	try {
		text = await readFile(join(dir, 'LICENSE'), 'utf8');
	} catch {
		console.error(`${dir}: no LICENSE file to ship beside the font.`);
		process.exit(1);
	}
	if (!text.includes(mustName)) {
		console.error(`${dir}: its LICENSE does not read as the ${mustName}.`);
		process.exit(1);
	}
	await writeFile(join(FONT_DIR, `${prefix}-LICENSE.txt`), text);
}

// Every font file written has a licence file beside it, by its prefix. Read back from the folder
// rather than from the lists above, so a file that reaches the folder another way is held too.
async function everyFontHasItsLicence() {
	const names = await readdir(FONT_DIR);
	const licences = new Set(names.filter((name) => name.endsWith('-LICENSE.txt')));
	const orphans = names.filter(
		(name) =>
			name.endsWith('.woff2') &&
			![...licences].some((licence) => name.startsWith(licence.slice(0, -'LICENSE.txt'.length)))
	);
	if (orphans.length > 0) {
		console.error(`fonts shipped with no licence text beside them: ${orphans.join(', ')}`);
		process.exit(1);
	}
	return licences.size;
}

// --- the text faces -----------------------------------------------------------------------------

const blocks = [];

for (const face of TEXT_FACES) {
	const dir = join(root, 'node_modules', face.package);
	await shipLicence(dir, face.prefix, 'SIL Open Font License');
	const css = await readFile(join(dir, 'wght.css'), 'utf8');

	for (const block of css.split('@font-face').slice(1)) {
		const file = block.match(/url\(\.\/files\/([^)]+)\)/)?.[1];
		if (!file) continue;

		// Named `<face>-<subset>-wght-normal.woff2`. Keep only ours.
		const subset = file.slice(face.prefix.length + 1, file.indexOf('-wght-'));
		if (!SUBSETS.includes(subset)) continue;

		await copyFile(join(dir, 'files', file), join(FONT_DIR, file));
		const declared = ('@font-face' + block)
			.replace(/url\(\.\/files\/[^)]+\)/, `url(/fonts/${file})`)
			// The packages ship `swap`, which shows a fallback face and then reflows the page when
			// the real one arrives. These are served from the same origin as the page asking for
			// them, so the wait is not a wait, and the reflow buys nothing.
			.replace(/font-display:\s*\w+;/, 'font-display: block;')
			.trim();
		blocks.push(declared);

		// The digits are in the latin subset, so that file is the one the alias points at.
		if (face.figures && subset === 'latin') {
			const family = declared.match(/font-family:\s*'([^']+) Variable';/)?.[1];
			if (!family) {
				console.error(`${face.package}: no variable family name to alias its figures from.`);
				process.exit(1);
			}
			blocks.push(
				declared
					.replace(/font-family:\s*'[^']+';/, `font-family: '${family} Figures';`)
					.replace(/unicode-range:[^;]+;/, `unicode-range: ${DIGITS};`)
			);
		}
	}
}

// --- the icon font ------------------------------------------------------------------------------

// Read out of the source file rather than a data file beside it. The list has to be a literal in
// TypeScript for the icon name to be a checked type rather than any old string, and having a second
// copy here as data is how the two drift apart.
const declaration = await readFile(ICON_LIST, 'utf8');
const names = [...declaration.matchAll(/^\t'([a-z0-9_]+)',?$/gm)].map((match) => match[1]);

if (names.length === 0) {
	console.error(
		`no icon names found in ${ICON_LIST}.\n` +
			'They are read out of the file by shape, so reformatting the list can hide it from this. ' +
			'Failing here rather than quietly subsetting an empty font.'
	);
	process.exit(1);
}

const source = await readFile(ICON_SOURCE);
const font = fontkit.create(source);

// Glyph id -> the codepoint that reaches it: the font's character map read backwards, so a glyph
// found by shaping a name can be asked for as a character instead.
const codepointOfGlyph = new Map();
for (const codepoint of font.characterSet) {
	const glyph = font.glyphForCodePoint(codepoint);
	if (glyph && !codepointOfGlyph.has(glyph.id)) codepointOfGlyph.set(glyph.id, codepoint);
}

const codepoints = {};
const unresolved = [];

for (const name of names) {
	const run = font.layout(name);
	// One glyph means the ligature fired and the name is a real icon. More than one means the font
	// spelled it out letter by letter, which is what a misspelt or withdrawn name looks like.
	const codepoint = run.glyphs.length === 1 ? codepointOfGlyph.get(run.glyphs[0].id) : undefined;
	if (codepoint === undefined) {
		unresolved.push(name);
		continue;
	}
	// Hex, not the character. The characters are in a private-use range and show as nothing at all
	// in an editor or a diff, so a map full of them is unreviewable. "e8b8" is not.
	codepoints[name] = codepoint.toString(16);
}

if (unresolved.length > 0) {
	console.error(
		`these icon names are not in the font: ${unresolved.join(', ')}.\n` +
			'A name that does not resolve renders as its own letters, which is how a typo ships ' +
			'looking like a design choice. Check the spelling against the icon set.'
	);
	process.exit(1);
}

// The icons that also have to be drawable as a PICTURE rather than as text.
//
// An icon is a font glyph, which is only reachable from a text node, and there are places that
// need one as an image address instead, where an `<img>` is already sized and positioned by its
// parent and swapping it for an element would take it out of that parent's own style rules. A face
// on a file in the vault is one: the picture is refused, and what stands in for it has to be a
// picture too.
//
// Taken from the same font every other icon is rendered from, at the same pinned axes, so the drawn
// one and the written one cannot come out as different marks. Anything drawn by hand here would be
// a second copy of an icon and would drift from the set it is not part of.
const AS_A_PICTURE = ['visibility_off'];

for (const name of AS_A_PICTURE) {
	if (codepoints[name] === undefined) {
		console.error(`${name} has to be in the icon list before its outline can be taken.`);
		process.exit(1);
	}
}

const wanted = Object.values(codepoints)
	.map((hex) => String.fromCodePoint(parseInt(hex, 16)))
	.join('');

const subset = await subsetFont(source, wanted, {
	targetFormat: 'woff2',
	// Only codepoints are asked for, so following the ligature rules would drag back in every icon
	// whose name shares a letter with these.
	noLayoutClosure: true,
	variationAxes: {
		// One weight, pinned. The design uses 300 throughout; keeping the axis would carry every
		// other weight's outlines for nothing.
		wght: 300,
		// Grade, a contrast adjustment for light-on-dark. 0 is the design's value.
		GRAD: 0,
		// Optical size, pinned to the axis floor. Icons render at 16-20px and the axis starts at 20,
		// so there is one value in play.
		opsz: 20,
		// The one axis that stays: an icon is outlined normally and filled when its nav item is the
		// current one, and that is this axis moving 0 -> 1.
		FILL: { min: 0, max: 1, default: 0 }
	}
});

if (subset.length > MAX_ICON_BYTES) {
	console.error(
		`icon subset is ${subset.length} bytes, over the ${MAX_ICON_BYTES} limit.\n` +
			'Either the icon list grew a great deal, or an axis is no longer pinned and the font is ' +
			'carrying variations nothing renders.'
	);
	process.exit(1);
}

await writeFile(join(FONT_DIR, ICON_FILE), subset);
await shipLicence(
	dirname(ICON_SOURCE),
	ICON_FILE.slice(0, ICON_FILE.indexOf('-subset')),
	'Apache License'
);

// The outlines, from a second subset with every axis pinned, FILL included.
//
// Through a subset rather than by asking the font for a variation directly: fontkit can build a
// variation instance but cannot hand back a glyph from one, so the only way to get the varied
// outline is to bake the axes into a font and read that. Pinning FILL to 1 matches how these are
// drawn everywhere they mean "hidden". TrueType because nothing serves this one: it exists for
// the length of this script.
const outlines = {};
for (const name of AS_A_PICTURE) {
	const codepoint = String.fromCodePoint(parseInt(codepoints[name], 16));
	const baked = fontkit.create(
		await subsetFont(source, codepoint, {
			targetFormat: 'truetype',
			noLayoutClosure: true,
			variationAxes: { wght: 300, GRAD: 0, opsz: 20, FILL: 1 }
		})
	);
	const glyph = baked.glyphForCodePoint(codepoint.codePointAt(0));
	const box = glyph.bbox;
	// A SQUARE with room around the mark, rather than the glyph's own bounds.
	//
	// Two reasons, and the tight bounds get both wrong. The picture stands in for a face crop, which
	// is square and drawn with `object-fit: cover`, so a picture of another shape is CROPPED to
	// fit, and an icon that reaches its own edges is cropped at the edges. And an icon drawn corner
	// to corner reads as blown up beside anything else in the interface, because everywhere else it
	// sits inside a control with space around it.
	//
	// So: a square centred on the mark, sized so the mark takes this much of it. Roughly what an
	// icon occupies inside a button, which is the proportion the eye expects.
	const SHARE_OF_THE_PICTURE = 0.58;
	const side = Math.max(box.maxX - box.minX, box.maxY - box.minY) / SHARE_OF_THE_PICTURE;
	const middleX = (box.minX + box.maxX) / 2;
	const middleY = (box.minY + box.maxY) / 2;
	outlines[name] = {
		path: glyph.path.toSVG(),
		// A font draws upwards from the baseline and an SVG downwards from the top, so the vertical
		// axis is turned over here and whatever draws it has to turn the drawing over to match.
		view: [middleX - side / 2, -middleY - side / 2, side, side].join(' ')
	};
}

blocks.push(
	'@font-face {\n' +
		"  font-family: 'Material Symbols Rounded';\n" +
		'  font-style: normal;\n' +
		'  font-display: block;\n' +
		`  src: url(/fonts/${ICON_FILE}) format('woff2-variations');\n` +
		'}'
);

// --- outputs ------------------------------------------------------------------------------------

await writeFile(
	join(GENERATED, 'fonts.css'),
	'/* Generated by scripts/prepare_fonts.js. Do not edit: run `npm run fonts`. */\n\n' +
		blocks.join('\n\n') +
		'\n'
);
await writeFile(
	join(GENERATED, 'icon-codepoints.json'),
	JSON.stringify(codepoints, null, '\t') + '\n'
);
await writeFile(join(GENERATED, 'icon-outlines.json'), JSON.stringify(outlines, null, '\t') + '\n');

const licences = await everyFontHasItsLicence();

console.log(
	`fonts: ${blocks.length} faces staged, ${licences} licence texts beside them; ` +
		`icons ${names.length} glyphs, ${source.length} -> ${subset.length} bytes ` +
		`(${((subset.length / source.length) * 100).toFixed(1)}% of the full font)`
);
