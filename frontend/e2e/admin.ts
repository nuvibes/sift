import {
	expect,
	type APIRequestContext,
	type APIResponse,
	type BrowserContext,
	type Cookie,
	type Page
} from '@playwright/test';

/* The one admin the tests share: the first run creates it and every later attempt is a 409, so
 * two names would leave one spec unable to sign in. */

export const ADMIN = 'e2e-admin';

// The real policy, enforced by the real endpoint.
export const PASSWORD = 'An-e2e-Passphrase-9';

/** Inside the test's sixty seconds, so a login kept out says so with its status. */
const SIGN_IN_DEADLINE_MS = 30_000;

/**
 * Sessions this worker has signed in and no open browser is using, newest last. The server turns
 * away a second sign-in in flight for one name and address, so sessions are reused; per worker and
 * lent, so two open browsers never share one; and checked before lending, since signing out,
 * locking and opening Hidden are state on the session.
 */
const pool: Cookie[][] = [];

/** More spare sessions than one test opens browsers together is only more to check. */
const POOL_LIMIT = 4;

/** Sign in as the one admin through the API (`auth.spec.ts` drives the form). */
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

function lend(context: BrowserContext, session: Cookie[]): void {
	context.once('close', () => {
		if (pool.length < POOL_LIMIT) pool.push(session);
	});
}

/** Put a kept session into this browser if the server says it is still a fresh one. */
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

/** Sign in, waiting out a 429 for its `Retry-After` plus a spread; any other answer fails named. */
async function logIn(page: Page): Promise<void> {
	const login = await signIn(page.request, ADMIN, PASSWORD);
	expect(login.status(), `could not sign the admin in (answered ${login.status()})`).toBe(200);
}

/** One sign-in, waiting out a 429 until the deadline; the caller judges every other answer. */
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
