import {
	expect,
	type APIRequestContext,
	type APIResponse,
	type BrowserContext,
	type Cookie,
	type Page
} from '@playwright/test';

/* The one admin the end-to-end tests share.
 *
 * One account, defined once: Sift's first run creates an admin and every attempt after that is
 * answered 409. The tests share a server, so whichever spec file happens to go first creates it,
 * and if two files each named their own admin, the loser would be handed a 409 for an account
 * under a name and a password it does not know, and could never sign in. That fails only when the
 * whole suite runs, and reads as the login being broken.
 */

export const ADMIN = 'e2e-admin';

// The real policy, enforced by the real endpoint: ten characters, and an upper, a lower, a digit
// and a symbol. Nothing here is a fixture that waves it through.
export const PASSWORD = 'An-e2e-Passphrase-9';

/**
 * How long a sign-in may spend waiting its turn before the wait itself is the failure.
 *
 * Well inside the test's own sixty seconds, so a login that never gets in is reported as that
 * (with the status that kept it out) rather than as a test that timed out somewhere unrelated.
 */
const SIGN_IN_DEADLINE_MS = 30_000;

/**
 * Sessions this worker has signed in and no open browser is using, newest last.
 *
 * WHY THERE IS A POOL AT ALL. The server takes one sign-in at a time per username and address and
 * turns a second one away with 429 while the first is being checked: the ceiling a password
 * guesser runs into. Every spec here signs in as the SAME account from the SAME address, so a
 * fresh sign-in per test is, to the server, exactly that flood: at four workers nearly every login
 * met another one in flight. The durable answer is not to ask so often. A session is only a cookie,
 * and one signed in for the last test is as real for the next as a new one would be.
 *
 * WHY PER WORKER, AND LENT RATHER THAN SHARED. Module state is one Playwright worker's, and a
 * worker runs one test at a time, so a session handed to a browser is out of the pool until that
 * browser closes. Two browsers open at once (a test watching one window from another) therefore
 * never hold the same session: the second signs in afresh, as two people on two machines would.
 * One session shared across workers would let a test that signs out end every other test's
 * session in the middle of whatever it was doing.
 *
 * WHY EACH ONE IS CHECKED BEFORE IT IS LENT. A test may sign out, lock the screen, or open Hidden,
 * and each of those is state on the SESSION rather than on the account. A session is lent only
 * while it is signed in as the admin, not locked, and has Hidden shut (the state a fresh sign-in
 * starts in), and is otherwise dropped and replaced. So no test can inherit another's.
 */
const pool: Cookie[][] = [];

/** More spare sessions than one test ever opens browsers at once is only more to check. */
const POOL_LIMIT = 4;

/**
 * Sign in as the one admin.
 *
 * Through the API rather than the form, because most specs are about something else; the session
 * this leaves in the browser is real either way: a real cookie from a real login. `auth.spec.ts`
 * drives the form itself.
 *
 * First run creates an admin account; every run after that finds one. Both answers are correct, and
 * 409 is the instance saying it already has one.
 */
export async function signInAsAdmin(page: Page): Promise<void> {
	const context = page.context();
	while (pool.length > 0) {
		const session = pool.pop()!;
		if (await resumed(page, session)) {
			lend(context, session);
			return;
		}
	}

	const setup = await page.request.post('/api/auth/setup', {
		data: { username: ADMIN, password: PASSWORD }
	});
	if (setup.status() === 409) {
		await logIn(page);
	} else {
		expect(setup.ok(), 'could not create the admin').toBeTruthy();
	}
	lend(context, await context.cookies());
}

/** Hand the session to this browser, and take it back when the browser closes. */
function lend(context: BrowserContext, session: Cookie[]): void {
	context.once('close', () => {
		if (pool.length < POOL_LIMIT) pool.push(session);
	});
}

/**
 * Put a kept session into this browser, if it is still one a fresh sign-in would have given.
 *
 * Asked of the server rather than remembered: whether a session is signed out, locked or has
 * Hidden open is the server's to say, and a test that changed one of them did so through it.
 */
async function resumed(page: Page, session: Cookie[]): Promise<boolean> {
	const context = page.context();
	await context.addCookies(session);
	const me = await page.request.get('/api/auth/me');
	if (me.ok()) {
		const viewer = (await me.json()) as { username: string; role: string; locked: boolean };
		if (viewer.username === ADMIN && viewer.role === 'admin' && !viewer.locked) {
			const vault = await page.request.get('/api/vault');
			if (vault.ok() && !((await vault.json()) as { unlocked: boolean }).unlocked) return true;
		}
	}
	await context.clearCookies();
	return false;
}

/**
 * Sign in, waiting out the server's own answer when another sign-in is in the way.
 *
 * A 429 here is not a refusal: it says a sign-in for this name from this address is already being
 * checked, and its `Retry-After` says when to ask again. So that is exactly what this does: waits
 * the time the server named, plus a little spread so browsers turned away together do not come
 * back together. Nothing else is retried: any other answer is the answer, and fails with its
 * status named so a genuine refusal is never mistaken for a queue.
 *
 * Rare, because the pool above means a worker signs in about once; it is still needed, because
 * the workers all start at once and their first sign-ins meet.
 */
async function logIn(page: Page): Promise<void> {
	const login = await signIn(page.request, ADMIN, PASSWORD);
	expect(login.status(), `could not sign the admin in (answered ${login.status()})`).toBe(200);
}

/**
 * One sign-in, waiting out the server's "another sign-in is being checked" answer (a 429 with a
 * Retry-After) until the deadline. Every other answer is the answer, and the caller judges it.
 * The one place the rule lives: the suite's admin and the visual comparison both sign in this way.
 */
export async function signIn(
	request: APIRequestContext,
	username: string,
	password: string,
	deadlineMs = SIGN_IN_DEADLINE_MS
): Promise<APIResponse> {
	const deadline = Date.now() + deadlineMs;
	for (;;) {
		const login = await request.post('/api/auth/login', { data: { username, password } });
		const left = deadline - Date.now();
		if (login.status() !== 429 || left <= 0) return login;
		const named = Number(login.headers()['retry-after']);
		const wait = (Number.isFinite(named) && named > 0 ? named : 1) * 1000;
		await new Promise((done) => setTimeout(done, Math.min(wait + Math.random() * 250, left)));
	}
}
