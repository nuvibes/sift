// Sift works with no internet, and never tells anyone else who is using it.
//
// One <link> to a font CDN would undo both: the page would need that host to be reachable, and that
// host would learn the address of every person who opens Sift, every time they open it, without
// anyone choosing it. The same is true of an icon set, a script, an analytics beacon: any of them,
// added once, by someone who only wanted the font to look right.
//
// So the built output is checked rather than the intention. This reads what actually shipped.
//
// What it looks at, and what it does not: an absolute URL in a *reference* (a stylesheet's url(),
// a tag's src or href) is a fetch, and is refused. A URL sitting in a string in a script is not a
// fetch; the framework puts its own documentation links into error messages, and failing the build
// over those would teach everyone to ignore this. The runtime control on the rest is the policy the
// server sends, which confines the page to Sift's own origin whatever the code tries.

import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const OUTPUT = join(here, '../../src/sift/web');

// A reference position: something the browser will go and get.
const REFERENCES = [
	/url\(\s*['"]?(https?:)?\/\/[^)'"]+/gi, // css url()
	/\bsrc\s*=\s*['"](https?:)?\/\/[^'"]+/gi, // <script src>, <img src>
	/\bhref\s*=\s*['"](https?:)?\/\/[^'"]+/gi, // <link href>
	/@import\s+(url\()?\s*['"](https?:)?\/\/[^'")]+/gi
];

// Hosts that exist to serve someone else's assets. Refused wherever they appear, including inside a
// script, because there is no innocent reason for one of these to be in the bundle at all.
const OFFSITE_HOSTS = [
	'fonts.googleapis.com',
	'fonts.gstatic.com',
	'cdn.jsdelivr.net',
	'unpkg.com',
	'cdnjs.cloudflare.com',
	'ajax.googleapis.com',
	'use.typekit.net',
	'google-analytics.com',
	'googletagmanager.com'
];

async function* walk(dir) {
	let entries;
	try {
		entries = await readdir(dir, { withFileTypes: true });
	} catch {
		return;
	}
	for (const entry of entries) {
		const path = join(dir, entry.name);
		if (entry.isDirectory()) yield* walk(path);
		else yield path;
	}
}

const READS = ['.html', '.css', '.js', '.json', '.webmanifest'];
const offences = [];
let checked = 0;

for await (const path of walk(OUTPUT)) {
	if (!READS.some((ext) => path.endsWith(ext))) continue;
	checked += 1;
	const text = await readFile(path, 'utf8');
	const where = relative(OUTPUT, path);

	for (const pattern of REFERENCES) {
		for (const match of text.matchAll(pattern)) {
			offences.push(`${where}  fetches  ${match[0].slice(0, 90)}`);
		}
	}

	for (const host of OFFSITE_HOSTS) {
		if (text.includes(host)) offences.push(`${where}  mentions  ${host}`);
	}
}

if (checked === 0) {
	console.error(
		`nothing to check in ${OUTPUT}.\n` +
			'This gate reads the built client, so it has to run after the build. Passing because there ' +
			'is nothing there would be the one result it must never give.'
	);
	process.exit(1);
}

if (offences.length > 0) {
	console.error('The built client reaches off this machine:\n');
	for (const offence of offences) console.error(`  ${offence}`);
	console.error(
		'\nEverything Sift serves is bundled and served from its own origin. Add the asset to the ' +
			'build instead of linking it.'
	);
	process.exit(1);
}

console.log(`ok   ${checked} built files, nothing fetched from off this machine`);
