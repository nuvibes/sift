#!/usr/bin/env node
/*
 * A style rule that matches nothing fails the build.
 *
 * This is not a tidiness check. A control whose rules stopped matching does not come out plain:
 * it comes out wearing the browser's own chrome, a grey 3D button in the middle of a dark
 * interface. Rename a class on one element and every rule keyed on the old name as an ancestor goes
 * quiet at once, while the markup and the stylesheet each still read correctly on their own.
 *
 * The compiler already knows, in a warning on every build. This listens, and it is the only check
 * of its kind that runs before anything is drawn. Every other one measures a page, which means it
 * can only see the screens somebody remembered to open.
 *
 * Run: node scripts/check_no_dead_css.js
 */
import { fileURLToPath } from 'node:url';

import { spawnTool } from './spawn-tool.js';

const CODE = 'css_unused_selector';

/* Through `spawnTool`, because a bare `spawnSync('npx', ...)` cannot run on Windows at all. See
 * that module for what Node answers instead. The gate refuses to pass on empty output, so that
 * failure cannot read as a clean run.
 */
const checked = spawnTool(
	'npx',
	['svelte-check', '--output', 'machine', '--threshold', 'warning'],
	// fileURLToPath, not `.pathname`: on Windows a file URL's pathname is "/C:/work/...", with a
	// leading slash that no filesystem call accepts, so the checker would run in the wrong place.
	{ cwd: fileURLToPath(new URL('..', import.meta.url)) }
);

const output = `${checked.stdout ?? ''}${checked.stderr ?? ''}`;
if (!output.trim()) {
	console.error('check_no_dead_css: the checker produced no output, so it proved nothing.');
	process.exit(2);
}

const dead = output
	.split('\n')
	.filter((line) => line.includes(CODE))
	.map((line) => line.trim());

if (dead.length > 0) {
	console.error(`These style rules match nothing, so whatever they were dressing is undressed:\n`);
	for (const line of dead) console.error(`  ${line}`);
	console.error(
		`\nA selector that reaches no element leaves its control wearing the browser default.` +
			`\nFix the selector, or delete the rule if the thing it dressed is gone.\n`
	);
	process.exit(1);
}

console.log('No dead style rules.');
