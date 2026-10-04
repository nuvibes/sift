/*
 * The application, for the end-to-end tests to run against.
 *
 * The real server, serving the real built client, on the real port, because that is the only
 * arrangement where the policy, the fallback route and the fonts are the ones an install has. A dev
 * server would answer every one of these tests correctly while the shipped app served a blank page.
 *
 * The data directory is a fresh temporary one each run: the tests sign accounts in and out, and a
 * run must not touch a real library or leave anything behind.
 *
 * ## Why Node and not a shell script
 *
 * Two faults on Windows, either of which alone is fatal and neither of which looks like what it is:
 *
 * ONE: a virtual environment puts its interpreter in `bin/` on POSIX and in `Scripts/` with an
 * `.exe` on Windows; a script naming the POSIX path makes Playwright report "Process from
 * config.webServer was not able to start. Exit code: 127", which says nothing about a missing
 * interpreter.
 *
 * TWO, and worse: `bash` on the Windows PATH can resolve to WSL's bash, not to Git for Windows'.
 * WSL hands a Windows process only the variables `WSLENV` names, and it names none by default, so
 * every setting a shell script exported would be silently dropped, and the backend would come up on
 * its DEFAULT port and DEFAULT data directory: against a real library, looking like a passing run.
 *
 * Node removes both: there is no shell to resolve, and `spawn` is handed an explicit environment
 * that reaches the child on every platform. The same reasoning as `frontend/scripts/spawn-tool.js`.
 */

import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const ROOT = resolve(HERE, '../..');

/* Both layouts, the running platform's first, exactly as scripts/venv_tool.py does for the hooks. */
const CANDIDATES =
	process.platform === 'win32'
		? [join('.venv', 'Scripts', 'python.exe'), join('.venv', 'bin', 'python')]
		: [join('.venv', 'bin', 'python'), join('.venv', 'Scripts', 'python.exe')];

const python = CANDIDATES.map((rel) => join(ROOT, rel)).find(existsSync);
if (python === undefined) {
	console.error('e2e/serve.mjs: no interpreter in .venv.');
	console.error(`  Looked for: ${CANDIDATES.join(', ')}`);
	console.error('  Create it first (uv sync) so the tests run the version everything else does.');
	process.exit(1);
}

const scratch = mkdtempSync(join(tmpdir(), 'sift-e2e-'));

/* Where the tests put the folders they add to the library. A FIXED path rather than a temporary
 * one, because there is no way to tell the tests a directory named after they started. The tests
 * know this path: it is `MEDIA` in library.spec.ts and seed.ts, and the three have to agree. Emptied
 * each run so a previous run's folders are not still there. It is not a setting of the
 * server's: the picker browses wherever the library's roots are. */
const browseRoot = join(ROOT, 'frontend', 'e2e', '.media');
rmSync(browseRoot, { recursive: true, force: true });
mkdirSync(browseRoot, { recursive: true });

/* NOT 5171. That is the port a real install runs on, and a machine running this suite may be
 * running one, so binding it either collides with somebody's library or, worse, hands the whole
 * run to it. The tests never touch a port a person would have open. */
/* WARNING rather than the default INFO, and that is what makes the log worth forwarding at all.
 *
 * `playwright.config.ts` pipes this process's stdout into the run's output, because Playwright
 * drops a web server's stdout unless told not to, and Sift logs to stdout. At INFO the
 * application writes a line for every HTTP request, which over this suite is tens of thousands of
 * them. At WARNING what appears is a refusal, a rejected origin, a job that could not start: the
 * lines somebody chasing a failure actually wants.
 */
const settings = {
	SIFT_DATA_DIR: join(scratch, 'data'),
	SIFT_CACHE_DIR: join(scratch, 'cache'),
	SIFT_PORT: '5399',
	SIFT_LOG_LEVEL: 'WARNING'
};

const child = spawn(python, ['-m', 'sift.main'], {
	cwd: ROOT,
	stdio: 'inherit',
	env: { ...process.env, ...settings }
});

/*
 * Throwing away the run's data directory, and NEVER throwing while doing it.
 *
 * `force: true` swallows "it was not there"; it does not swallow EPERM, and on Windows EPERM is
 * what a directory answers while anything still holds a handle inside it. The venv's `python.exe`
 * is a trampoline that runs the real interpreter as a CHILD, so the moment this process sees an
 * exit the database files can still be open in a process that is on its way out: a race this
 * cannot win and does not need to: the directory is under the system temp root and the next run
 * makes its own.
 *
 * A throw here would print a `node:fs` stack over the one fact worth having (what the server
 * exited WITH), and every remaining spec would fail instantly against a port with nothing on it.
 */
function tidy() {
	try {
		rmSync(scratch, { recursive: true, force: true });
	} catch (reason) {
		console.error(`e2e/serve.mjs: could not remove ${scratch}: ${reason}`);
	}
}

child.on('exit', (code, signal) => {
	/*
	 * SAID OUT LOUD, because Playwright stops the whole run the moment this process ends and the
	 * report it writes says only that the web server exited. An orderly stop and a kill look the
	 * same from the browser's side (a port that answers nothing), and they want opposite
	 * repairs, so the code and the signal are printed where the run's output will carry them.
	 */
	console.error(`e2e/serve.mjs: the server exited (code ${code}, signal ${signal}).`);
	tidy();
	process.exit(signal !== null ? 1 : (code ?? 0));
});

for (const signal of ['SIGINT', 'SIGTERM']) {
	process.on(signal, () => {
		child.kill();
		tidy();
	});
}
