/* A smoke run: prove this executable starts, and stop before it does anything else.
 *
 * The fuses `OnlyLoadAppFromAsar` and `EnableEmbeddedAsarIntegrityValidation` decide whether the
 * application runs at all; a bundle that breaks either exits 1 with no dialog, so a build asks
 * here and a broken one refuses to ship. It stops short of the backend because the port and the
 * data folder are the machine's real ones. It must run before main.ts's single-instance lock,
 * which quits with 0 while Sift is open.
 */

/** What a smoke run writes to standard output. Read by scripts/release.py, never copied there. */
export const SMOKE_MARKER = 'sift-shell-smoke-ok';

/**
 * Answer a smoke run if this is one: say so, stop the process, and tell the caller it is over.
 *
 * All three here because the order is the behaviour and this file is tested directly. The marker,
 * not the exit code, because several ordinary paths through the shell also exit 0.
 */
export function answerSmokeRun(
	argv: readonly string[],
	write: (line: string) => void,
	stop: (code: number) => void
): boolean {
	if (!argv.includes('--smoke')) return false;
	write(`${SMOKE_MARKER}\n`);
	stop(0);
	return true;
}
