/*
 * Running one of npm's own commands from a gate script, on any platform.
 *
 * npm ships `npx.cmd` and `npm.cmd` on Windows, not `npx` and `npm`. `spawnSync` does not go
 * through a shell, so it looks for a file with the exact name and does not find one. And since
 * the fix for CVE-2024-27980, Node REFUSES to spawn a `.cmd` or `.bat` at all without `shell:
 * true`, answering `EINVAL` instead of running it.
 *
 * That is worse than a crash: `spawnSync` sets `error` rather than throwing, so a gate that reads
 * `stdout` sees an empty string, and a gate that treats empty output as "nothing to complain about"
 * passes on Windows while measuring nothing at all.
 *
 * So: one place that knows, used by every script that shells out to a node tool.
 */
import { spawnSync } from 'node:child_process';

const WINDOWS = process.platform === 'win32';

/**
 * Run `npx`, `npm` or another node-shipped command, and answer what it said.
 *
 * @param {string} tool  the command, without any extension: 'npx', 'npm'
 * @param {string[]} args
 * @param {import('node:child_process').SpawnSyncOptions} [options]
 */
export function spawnTool(tool, args, options = {}) {
	if (!WINDOWS) return spawnSync(tool, args, { encoding: 'utf8', ...options });

	/* One command STRING, not a command plus an args array.
	 *
	 * Node deprecates the second form under `shell: true` (DEP0190) because the arguments are
	 * concatenated rather than escaped, so the honest thing is to concatenate them here, where the
	 * refusal below can state what that is safe for. Every caller passes literals written in the
	 * script itself; nothing here ever carries a path, a filename or anything a person typed. */
	const unsafe = args.find((one) => /[\s"'`$&|<>;]/.test(one));
	if (unsafe !== undefined) {
		throw new Error(
			`spawnTool: refusing to run ${tool} with the argument ${JSON.stringify(unsafe)}. ` +
				'On Windows this goes through a shell as one string, so an argument holding a space or ' +
				'a shell character would be re-split or interpreted. Pass literals only, or spawn the ' +
				"tool's own entry point with node directly."
		);
	}
	return spawnSync(`${tool}.cmd ${args.join(' ')}`, {
		encoding: 'utf8',
		shell: true,
		...options
	});
}
