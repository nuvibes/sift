/* A smoke run: prove this executable starts, and stop before it does anything else. */

/** What a smoke run writes to standard output. Read by scripts/release.py, never copied there. */
export const SMOKE_MARKER = 'sift-shell-smoke-ok';

/** Answer a smoke run if this is one: say so, stop the process, and tell the caller it is over. */
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
