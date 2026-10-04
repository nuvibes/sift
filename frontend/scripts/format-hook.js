/*
 * Format the client files a commit is touching, from the client's own directory.
 *
 * Two things make this a script rather than one line in the hook config.
 *
 * The CLIENT'S directory, because that is where prettier's configuration and its Svelte plugin
 * live. Run from the repository root, prettier resolves the plugin against the root and answers
 * "Cannot find package 'prettier-plugin-svelte'": a failure about the tool rather than the code.
 *
 * And prettier's own entry point, spawned by node, rather than `npx`. On Windows `npx` is a `.cmd`
 * and Node refuses to spawn one without a shell, and a shell would re-split any filename holding
 * a space, which is a filename somebody is allowed to have. This runs a `.cjs` file with node,
 * which needs neither.
 */
import { spawnSync } from 'node:child_process';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const prettier = resolve(frontend, 'node_modules', 'prettier', 'bin', 'prettier.cjs');

const files = process.argv
	.slice(2)
	.map((given) => relative(frontend, resolve(process.cwd(), given)));

/* Nothing of the client's in this commit. pre-commit only runs this when its own file filter
 * matched, so this is the belt on top of that rather than the usual case. */
if (files.length === 0) process.exit(0);

const done = spawnSync(process.execPath, [prettier, '--write', ...files], {
	cwd: frontend,
	stdio: 'inherit'
});
process.exit(done.status ?? 1);
