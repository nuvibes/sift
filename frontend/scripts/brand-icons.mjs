/* Builds the icon files in static/ from the one source vector in src/lib/brand/mark.svg.
 *
 * The icons are generated rather than drawn so there is one copy of the artwork. Re-run this
 * after changing the mark; the output is committed, because a build should not need a browser.
 *
 *   node scripts/brand-icons.mjs
 *
 * Chromium comes from the test runner's own install, which is already a dev dependency.
 *
 * The Windows icon is written from here too, into ../desktop/installer/icon.ico: the
 * application's icon in the taskbar, the Start menu, Add/Remove Programs and on the installer
 * itself. A second copy of the artwork drifts, and without an icon electron-builder silently
 * falls back to Electron's own logo.
 */

import { chromium } from 'playwright';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');

/* The artwork lives in the component, so there is one copy of it and no second file to drift. Pull
 * the drawing back out and drop the parts that only mean something inside a component. */
function sourceMark() {
	const file = readFileSync(join(root, 'src/lib/brand/Mark.svelte'), 'utf8');
	const found = file.match(/<svg[\s\S]*?<\/svg>/);
	if (!found) throw new Error('no <svg> in src/lib/brand/Mark.svelte: did the component change?');
	return found[0].replace(/\sstyle:height="[^"]*"/, '');
}

/* Every edit this script makes to the source drawing goes through here.
 *
 * The artwork is edited by find-and-replace, and JavaScript's `replace` does nothing at all when
 * what it is looking for is absent: no error, the original string handed straight back. A
 * reformatted source (prettier moving an attribute onto its own line) would then silently drop an
 * edit, and the output is generated fresh each run, so no diff would ever show it.
 *
 * A replacement that finds nothing is a build failure.
 */
function swap(text, find, put) {
	if (!text.includes(find)) {
		throw new Error(
			`brand-icons: nothing in the mark matches ${JSON.stringify(find)}. The drawing in ` +
				'src/lib/brand/Mark.svelte has changed shape. Update this script to match it.'
		);
	}
	return text.replace(find, put);
}

const source = sourceMark();

const INK_DARK = '#F1F3F6';
const INK_LIGHT = '#0F1115';
const CHARCOAL = '#0F1115';
const BLUE = '#2563EB';

/** The mark at a given size, with the two parts coloured separately. */
function mark({ size, ink, grain = BLUE }) {
	let out = swap(source, 'fill="currentColor"', `fill="${ink}"`);
	out = swap(out, '<svg', `<svg height="${size}" style="display:block"`);
	return swap(out, 'fill="var(--sift-accent)"', `fill="${grain}"`);
}

/* Each icon is a square tile with the mark centred in it. `inset` is the share of the tile the mark
 * is allowed to occupy: Android crops a maskable icon to a circle, so that one keeps well clear of
 * the edges. */
/* The sizes a Windows .ico has to carry.
 *
 * Windows picks the nearest one UP and shrinks it, so a missing size is not a missing icon. It is
 * a blurry one, in whichever place happens to ask for it. 16 is the title bar and the small taskbar,
 * 32 the ordinary desktop, 48 Explorer's medium view, 256 the large view and Add/Remove Programs. */
const ICO_SIZES = [16, 24, 32, 48, 64, 128, 256];

/* How much of the tile the mark takes in the Windows icon, and how round the tile is.
 *
 * Rounder and tighter than the web tiles on purpose. A hard-cornered dark square on a dark taskbar
 * has no edge at all, so the icon reads as a mark floating in nothing; the corner radius is what
 * gives it a shape of its own next to every other application's. */
const ICO_INSET = 0.62;
const ICO_RADIUS = 0.22;

const ICONS = [
	{ file: 'favicon-32.png', px: 32, inset: 0.78, bg: CHARCOAL, ink: INK_DARK },
	/* Dark like every other tile: this is the one a phone puts on its home screen (Safari's Add to
	   Home Screen reads it, not the manifest), and a white tile there is a light Sift on the phone. */
	{ file: 'apple-touch-icon.png', px: 180, inset: 0.66, bg: CHARCOAL, ink: INK_DARK },
	{ file: 'icon-192.png', px: 192, inset: 0.7, bg: CHARCOAL, ink: INK_DARK },
	{ file: 'icon-512.png', px: 512, inset: 0.7, bg: CHARCOAL, ink: INK_DARK },
	{ file: 'icon-maskable-512.png', px: 512, inset: 0.52, bg: CHARCOAL, ink: INK_DARK }
];

/* The favicon is the one icon that is not a raster. An SVG favicon carries its own stylesheet, so it
 * can answer the browser chrome's own light or dark setting, which no single PNG can do. */
function faviconSvg() {
	let out = swap(source, 'fill="currentColor"', `fill="${INK_LIGHT}"`);
	out = swap(
		out,
		'>',
		`><style>@media (prefers-color-scheme: dark){ .body { fill: ${INK_DARK} } }</style>`
	);
	/* `<path`, not `<path d`: the attribute after the tag name is whatever the formatter last
	   decided, and only the tag name is the script's to depend on. `replace` with a string changes
	   the FIRST match, which is the body of the mark; the grain is the one after it. */
	out = swap(out, '<path', '<path class="body"');
	return swap(out, 'fill="var(--sift-accent)"', `fill="${BLUE}"`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 1 });

for (const icon of ICONS) {
	const inner = Math.round(icon.px * icon.inset);
	await page.setViewportSize({ width: icon.px, height: icon.px });
	await page.setContent(
		`<body style="margin:0;width:${icon.px}px;height:${icon.px}px;background:${icon.bg};display:flex;align-items:center;justify-content:center">
			${mark({ size: inner, ink: icon.ink })}
		</body>`
	);
	await page.screenshot({ path: join(root, 'static/brand', icon.file) });
	console.log(`wrote static/brand/${icon.file} (${icon.px}px)`);
}

/* --- The Windows icon ---------------------------------------------------------------------- */

/* A .ico is a tiny directory followed by its images, and since Vista each image may be a PNG held
 * whole, so the sizes already rendered above are simply concatenated with a header describing
 * them. Written by hand because the alternative is a dependency whose only job is this, and the
 * format is fourteen bytes of arithmetic per entry. */
function icoFrom(images) {
	const HEADER = 6;
	const ENTRY = 16;
	const header = Buffer.alloc(HEADER);
	header.writeUInt16LE(0, 0); // reserved
	header.writeUInt16LE(1, 2); // 1 = icon, 2 = cursor
	header.writeUInt16LE(images.length, 4);

	const directory = Buffer.alloc(ENTRY * images.length);
	let offset = HEADER + ENTRY * images.length;
	images.forEach(({ size, png }, index) => {
		const at = ENTRY * index;
		/* 256 is written as 0. The field is one byte, so the largest size a .ico can name is the
		   one that does not fit in it, and every reader knows to read the zero as 256. */
		directory.writeUInt8(size >= 256 ? 0 : size, at);
		directory.writeUInt8(size >= 256 ? 0 : size, at + 1);
		directory.writeUInt8(0, at + 2); // colours in the palette: none, it is truecolour
		directory.writeUInt8(0, at + 3); // reserved
		directory.writeUInt16LE(1, at + 4); // colour planes
		directory.writeUInt16LE(32, at + 6); // bits per pixel
		directory.writeUInt32LE(png.length, at + 8);
		directory.writeUInt32LE(offset, at + 12);
		offset += png.length;
	});

	return Buffer.concat([header, directory, ...images.map((one) => one.png)]);
}

const icoImages = [];
for (const size of ICO_SIZES) {
	const inner = Math.round(size * ICO_INSET);
	await page.setViewportSize({ width: size, height: size });
	/* The tile is a DIV, not <body>. A background set on <body> when <html> has none is
	 * propagated by CSS to the page canvas and painted across the whole viewport, corners
	 * included, so a `border-radius` on the body box changes nothing and the icon comes out
	 * square.
	 */
	await page.setContent(
		`<body style="margin:0;width:${size}px;height:${size}px">
			<div style="width:${size}px;height:${size}px;display:flex;align-items:center;justify-content:center;background:${CHARCOAL};border-radius:${Math.round(size * ICO_RADIUS)}px;overflow:hidden">
				${mark({ size: inner, ink: INK_DARK })}
			</div>
		</body>`
	);
	/* `omitBackground`, so the rounded corners are actually transparent rather than white. Without
	   it the tile keeps a square white halo that only shows up against a dark taskbar. */
	icoImages.push({ size, png: await page.screenshot({ omitBackground: true }) });
	console.log(`rendered ${size}px for the Windows icon`);
}

const icoPath = join(root, '..', 'desktop', 'installer', 'icon.ico');
mkdirSync(dirname(icoPath), { recursive: true });
writeFileSync(icoPath, icoFrom(icoImages));
console.log(`wrote desktop/installer/icon.ico (${ICO_SIZES.join(', ')})`);

await browser.close();

/* The trailing newline is not cosmetic: the repository's end-of-file hook adds one, so a file
   written without it is rewritten by the next commit and the generator's output never matches
   what is checked in. */
writeFileSync(join(root, 'static/brand/favicon.svg'), `${faviconSvg()}\n`);
console.log('wrote static/brand/favicon.svg');
