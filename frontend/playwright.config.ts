import { defineConfig, devices } from '@playwright/test';
import { PRESSED_VIEWPORTS } from './e2e/widths';

/* The server the visual comparison photographs, when one is named. See `e2e/visual.spec.ts`. */
const VISUAL_URL = process.env.VISUAL_URL;

/* Against the real thing.
 *
 * Not a dev server: these run the Python application serving the built client, on one port, which is
 * the configuration every install runs and the only one where the policy, the fallback route and the
 * fonts are all the real ones. A dev server would pass while the shipped app served a blank page.
 */
export default defineConfig({
	testDir: './e2e',
	fullyParallel: true,
	forbidOnly: !!process.env.CI,
	retries: 0,
	reporter: process.env.CI ? 'github' : 'list',

	/*
	 * How many browsers at once, from the environment when it says.
	 *
	 * Unset, Playwright takes half the cores. That is a reasonable default for the only thing
	 * running and the wrong one here: several checkouts of this project can run at the same time,
	 * and the Python gates already share a core budget between them: two runs each taking half
	 * the machine contend and produce timeouts that look exactly like real failures.
	 *
	 * Capped at four when nothing says otherwise, which is about the server rather than about this
	 * machine. Sift is ONE process: the API, the job feed and every response share a single event
	 * loop, and verifying a password is deliberately expensive. A dozen browsers all signing in at
	 * once make the login they are waiting on take longer than the test's own timeout, which
	 * reads as the login being broken.
	 *
	 * The name carries no SIFT_ prefix deliberately. The server started below is the real
	 * application, which refuses to boot on an unknown SIFT_ variable, and it inherits this
	 * environment.
	 */
	workers: process.env.E2E_WORKERS ? Number(process.env.E2E_WORKERS) : 4,

	/*
	 * How long an assertion may wait. This suite does not assume a responsive machine: under load
	 * (a machine running several browsers beside a Python suite) a correct assertion can miss a
	 * five-second deadline and pass on its own immediately afterwards.
	 *
	 * Ten seconds costs nothing when the answer is already there: a poll that succeeds on the
	 * first look returns on the first look. What it costs is the time a genuine failure takes to be
	 * reported, which is the right thing to spend: a false failure costs a whole re-run and the
	 * doubt that goes with it.
	 *
	 * The per-test timeout is the same question one layer up: a test that opens the asset page (a
	 * player, its derivatives, a dialog) can take most of thirty seconds under load. Sixty, for
	 * the same reasons, and a genuinely hung test still fails, later, and no less certainly.
	 */
	expect: { timeout: 10_000 },
	timeout: 60_000,

	use: {
		baseURL: 'http://127.0.0.1:5399',
		/*
		 * Off, and the way to get one is below.
		 *
		 * `on-first-retry` cannot fire at all: retries are nought above, deliberately, so there is
		 * never a first retry. `retain-on-failure` records every test and throws the recording away
		 * when one passes, so every test carries a screencast and a DOM snapshot per action:
		 * enough load inside a full run to exhaust the machine's socket buffers
		 * (`net::ERR_NO_BUFFER_SPACE`).
		 *
		 * So nothing is recorded on an ordinary run, and a failure worth understanding is chased
		 * with a targeted one:
		 *
		 *     npx playwright test e2e/<spec> --trace on
		 */
		trace: 'off',
		/* One photograph of the page as a failed test left it, beside the record Playwright writes of
		   the failure. Taken only then, so a passing test carries nothing. */
		screenshot: 'only-on-failure'
	},

	projects: [
		{ name: 'chromium', use: { ...devices['Desktop Chrome'] }, testIgnore: 'visual.spec.ts' },
		/*
		 * The visual comparison, run on its own (`npm run visual`) against a server somebody names.
		 *
		 * One photograph per operating system (win32, linux): fonts and anti-aliasing differ, so a photograph
		 * taken on one never matches another. The folder comes from the environment because what is
		 * photographed is somebody's library. Motion is reduced so no photograph catches a frame
		 * of a transition.
		 */
		{
			name: 'visual',
			testMatch: 'visual.spec.ts',
			use: {
				...devices['Desktop Chrome'],
				baseURL: VISUAL_URL,
				// The first of the sizes the interface is checked at; each screen is then taken at the rest.
				viewport: { ...PRESSED_VIEWPORTS[0] },
				deviceScaleFactor: 1,
				contextOptions: { reducedMotion: 'reduce' }
			},
			// Playwright's own token for the operating system (win32, linux or darwin) names the folder.
			snapshotPathTemplate: `${process.env.VISUAL_BASELINES ?? '{snapshotDir}'}/{platform}/{arg}{ext}`, // win32, linux
			expect: { toHaveScreenshot: { animations: 'disabled', caret: 'hide', scale: 'css' } }
		}
	],

	/*
	 * The client has to be built first: the server hands back files, and there are none until it
	 * is. Its data lives in a throwaway directory, so a run never touches a real library.
	 *
	 * `reuseExistingServer` is false and the port is not 5171, and both are needed. 5171 is the
	 * port a real install runs on; on a machine that is also running Sift, reuse would point the
	 * suite at that install: a real library, and an account whose password these tests do not
	 * know. `signInAsAdmin` would then post `/auth/setup`, be answered 409, and post `/auth/login`
	 * with wrong credentials once per spec file: a burst of failed logins against somebody's own
	 * instance, enough to trip the login tarpit. Not reusing stops the suite adopting a server it
	 * did not start; moving off 5171 stops it colliding with one.
	 */
	/* None when the visual comparison names a server of its own: it photographs that one. */
	webServer: VISUAL_URL
		? undefined
		: {
				command: 'npm run build:ifstale && npm run e2e:server',
				url: 'http://127.0.0.1:5399/health',
				reuseExistingServer: false,
				timeout: 180_000,
				/*
				 * The server's own complaints, piped so they reach the run's output.
				 *
				 * Sift logs to stdout (`logging.StreamHandler(sys.stdout)`), and Playwright forwards a web
				 * server's stdout only when this says `pipe`: stderr is forwarded whether or not it is
				 * asked for, stdout is dropped in silence. Without it every warning the application makes
				 * during a run (a refused live connection, a rejected origin, a job that could not start)
				 * goes nowhere, and a failure has to be read off the browser alone.
				 *
				 * This is affordable because of `SIFT_LOG_LEVEL` in `e2e/serve.mjs`: the application logs
				 * every request at INFO, which over a suite of this size is tens of thousands of lines. At
				 * WARNING what comes through is what somebody would want to read.
				 */
				stdout: 'pipe'
			}
});
