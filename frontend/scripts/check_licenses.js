// Every package that ships has to be one Sift is allowed to ship.
//
// Sift is AGPL-3.0-or-later. That obliges it about what it combines with: a dependency under a
// licence that cannot be combined with the AGPL is not a bug that shows up at runtime. It never
// shows up at all, until it matters, and by then it is in every release that was ever published.
// The Python dependencies are already checked this way. This is the same check for the other half,
// which is the larger tree by far.
//
// Fails closed. An unrecognised licence stops the build rather than being waved through, because the
// failure mode of the other choice is silence.

import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { spawnTool } from './spawn-tool.js';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');

// Permissive licences, all combinable with the AGPL: they add conditions Sift already meets (keep
// the notice, keep the copyright) and none that conflict with copyleft.
const ALLOWED = new Set([
	'MIT',
	'ISC',
	'BSD-2-Clause',
	'BSD-3-Clause',
	'0BSD',
	'Apache-2.0',
	// Permissive, and the licence zlib itself uses. Reaches us through a compression library that
	// carries zlib-derived code and so declares "MIT AND Zlib". Its own licence file was read.
	'Zlib',
	'Python-2.0',
	'BlueOak-1.0.0',
	'CC0-1.0',
	'CC-BY-4.0',
	'Unlicense',
	'WTFPL',
	'OFL-1.1',
	'MIT-0',
	'MPL-2.0',
	// The AGPL and the GPL family it can combine with. Not GPL-2.0-only, which cannot.
	'AGPL-3.0-or-later',
	'AGPL-3.0-only',
	'GPL-3.0-or-later',
	'GPL-2.0-or-later',
	'LGPL-3.0-or-later',
	'LGPL-2.1-or-later'
]);

// Packages that ship a licence but never declare one in package.json. npm reports nothing for these,
// and nothing means no permission rather than permission by omission, so each was read from the
// LICENSE file in its own tarball before being written down here.
//
// Pinned to the exact version that was read. A later release can relicense, and this list must not
// carry a verdict forward to a version nobody checked: a bump lands back on the gate.
const READ_FROM_ITS_OWN_LICENSE_FILE = new Map([
	// MIT, full text, same authors as the component library that depends on it.
	['svelte-toolbelt@0.10.6', 'MIT']
]);

// An expression like "(MIT OR Apache-2.0)" is satisfied if any branch is allowed. "AND" needs all.
// Parsed rather than string-matched: "(MIT AND GPL-2.0-only)" contains "MIT" and is not acceptable.
function isAllowed(expression) {
	if (!expression) return false;
	const clean = expression.replace(/[()]/g, ' ').trim();
	if (ALLOWED.has(clean)) return true;

	if (/\bOR\b/i.test(clean)) {
		return clean.split(/\bOR\b/i).some((part) => isAllowed(part.trim()));
	}
	if (/\bAND\b/i.test(clean)) {
		return clean.split(/\bAND\b/i).every((part) => isAllowed(part.trim()));
	}
	return false;
}

/* Through the shared helper, because `npm` is `npm.cmd` on Windows and Node refuses to spawn a
 * `.cmd` without a shell (see frontend/scripts/spawn-tool.js). Straight to `execFileSync` this
 * gate would not run there at all, and would answer with a spawn error rather than a licence. */
const listed = spawnTool('npm', ['ls', '--all', '--json', '--long'], {
	cwd: root,
	encoding: 'utf8',
	maxBuffer: 64 * 1024 * 1024
});

const seen = new Map();

function visit(node) {
	for (const [name, dep] of Object.entries(node.dependencies ?? {})) {
		// No version means it is not installed: an optional peer a package declares and nobody
		// asked for, listed by name only. Nothing of it is on disk and nothing of it ships, so there
		// is no licence to be compatible with. Reporting them would give this gate a permanent list
		// of complaints nobody can act on, and a gate people learn to scroll past is not a gate.
		if (!dep.version) continue;

		const key = `${name}@${dep.version}`;
		if (!seen.has(key)) {
			seen.set(key, dep.license ?? dep.licenses ?? null);
			visit(dep);
		}
	}
}

/* `npm ls` exits non-zero on an unmet peer dependency while still printing the whole tree, so the
 * status is not the question: empty output is. A gate that read nothing and said nothing would
 * pass on a machine where it never ran. */
if (!listed.stdout || listed.stdout.trim() === '') {
	console.error(
		`could not list the installed packages: ${listed.error ?? listed.stderr ?? 'no output'}`
	);
	process.exit(1);
}

visit(JSON.parse(listed.stdout));

if (seen.size === 0) {
	console.error('no packages found to check. Run npm install first.');
	process.exit(1);
}

const refused = [];
for (const [pkg, license] of seen) {
	const declared = typeof license === 'object' && license !== null ? license.type : license;
	// Only when the package says nothing. A declared licence is never overridden by the list above.
	const expression = declared ?? READ_FROM_ITS_OWN_LICENSE_FILE.get(pkg) ?? null;
	if (!isAllowed(expression)) refused.push(`${pkg}  ${expression ?? '(no licence declared)'}`);
}

if (refused.length > 0) {
	console.error('These packages are not known to be compatible with the licence Sift ships:\n');
	for (const line of refused) console.error(`  ${line}`);
	console.error(
		'\nCheck the licence, and if it can be combined with the AGPL add it to the list in this ' +
			'file. A package with no declared licence has to be read before it can be trusted: the ' +
			'default is no permission at all, not permission by omission.'
	);
	process.exit(1);
}

console.log(`ok   ${seen.size} packages, every licence compatible`);
