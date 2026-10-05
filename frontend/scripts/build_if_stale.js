// Builds the client, unless the one already built is newer than everything it was built from.
//
// A production build is the most expensive step in a local gate run, and two stages need it: the
// Python suite asserts things about the built page, and the end-to-end run starts a real server in
// front of it.
//
// What makes skipping safe is being pessimistic about what counts as an input. Everything the build
// reads is listed below, the newest of them is compared against the moment the last build finished,
// and anything unreadable, missing or ambiguous is treated as stale. A wrong skip would be much
// worse than a wasted build: every gate downstream would be testing a client nobody built. The
// decision itself lives in `lib/build-freshness.js`, so a test can ask it questions.
//
// FORCE_WEB_BUILD=1 builds regardless. The name has no SIFT_ prefix on purpose: the application
// refuses to start when it finds an unknown SIFT_ variable, and the end-to-end run starts the real
// application with this in its environment.

import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { markerPath, reasonToBuild } from './lib/build-freshness.js';
import { spawnTool } from './spawn-tool.js';

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, '..');
const repo = join(frontend, '..');

// Where `vite build` puts the client: into the Python package, which is where the server looks.
const OUTPUT = join(repo, 'src', 'sift', 'web');

// Everything the build reads. Directories are walked; files are stat-ed. A path that is not there
// is not an error. An absent optional config simply has no timestamp to beat.
const INPUTS = [
	join(frontend, 'src'),
	join(frontend, 'static'),
	join(frontend, 'scripts'),
	join(frontend, 'package.json'),
	join(frontend, 'package-lock.json'),
	join(frontend, 'vite.config.ts'),
	join(frontend, 'tsconfig.json'),
	join(frontend, 'svelte.config.js'),
	join(frontend, '.npmrc'),
	// The Documentation pane draws the docs site's pages, built in.
	join(repo, 'docs-site', 'src'),
	join(repo, 'docs-site', 'generated'),
	join(repo, 'docs-site', 'astro.config.mjs')
];

async function whyBuild() {
	if (process.env.FORCE_WEB_BUILD === '1') return 'FORCE_WEB_BUILD=1';
	return await reasonToBuild({
		output: OUTPUT,
		marker: markerPath(frontend),
		inputs: INPUTS,
		name: (path) => path.replace(repo + '/', '')
	});
}

const reason = await whyBuild();
if (reason === null) {
	console.log('ok   the built client is current (FORCE_WEB_BUILD=1 to build anyway)');
	process.exit(0);
}

console.log(`building the client: ${reason}`);
// Through `spawnTool` for the same reason the dead-CSS gate is: a bare `spawnSync('npm', ...)`
// answers EINVAL on Windows, and this one would have looked like a build that failed for no
// stated reason.
const built = spawnTool('npm', ['run', 'build'], { cwd: frontend, stdio: 'inherit' });
process.exit(built.status ?? 1);
