import { expect, test } from '@playwright/test';
import { signInAsAdmin } from './admin';

/* The one connection the application holds, in a real browser against the real server.
 *
 * Everything about it (the handshake, the cross-site check, the account being re-read before
 * every message) only happens when a real browser opens a real WebSocket. The unit tests drive
 * a fake one, which is the right way to test what a screen does with what arrives and cannot say
 * whether anything arrives at all.
 *
 * One connection per page: the Jobs screen and the busy indicator read the same feed rather than
 * opening their own, so the server does not build the identical page several times a second. This
 * is where that is checked.
 */

test('the application opens exactly one connection, whatever screen is on', async ({ page }) => {
	await signInAsAdmin(page);

	const sockets: string[] = [];
	page.on('websocket', (socket) => sockets.push(socket.url()));

	await page.goto('/settings/tasks?show=now');
	await expect(page.getByRole('heading', { name: 'Tasks and Activity' })).toBeVisible();
	await page.goto('/downloads');
	await expect(page).toHaveURL(/\/downloads/);

	expect(sockets.filter((url) => url.includes('/api/live/stream'))).toHaveLength(1);
	// The jobs-only feed address. A connection here would be the second transport this deliberately
	// does not have.
	expect(sockets.filter((url) => url.includes('/api/jobs/stream'))).toHaveLength(0);
});

test('it says where the account stands before the connection is open', async ({ page }) => {
	/* Read before connecting, so a change landing between the page loading and the connection
	   opening is one the connection can still find out about. Without it that gap is silent: the
	   change is stored, the message was published to nobody, and the screen stays as it was. */
	await signInAsAdmin(page);
	await page.goto('/browse');

	const state = await page.request.get('/api/live');

	expect(state.status()).toBe(200);
	const body = await state.json();
	expect(body).toHaveProperty('marker');
	expect(body).toHaveProperty('about');
	expect(body).toHaveProperty('opinions');
});

test.describe('who is turned away', () => {
	test('somebody signed out gets nothing from the plain read', async ({ request }) => {
		// No page, no navigation, nothing to not render. This is the request a stolen guess would
		// make, and the server answering it is the only thing that has ever kept anyone out.
		const state = await request.get('/api/live');

		expect(state.status()).toBe(401);
	});

	test('and the connection refuses them too', async ({ page }) => {
		/* The endpoint most likely to be forgotten, because the route table's own authorization
		 * check has to be taught to see it. A connection that opened here would be told about a
		 * library the person asking has not been let into.
		 *
		 * Opened from a page on the app's own origin, signed out. So the cross-site check passes
		 * and the thing being asked is only the question this is about: who is this.
		 */
		await page.goto('/login');

		const closed = await page.evaluate(async () => {
			const url = new URL('/api/live/stream', window.location.href);
			url.protocol = 'ws:';
			return await new Promise<string>((resolve) => {
				const socket = new WebSocket(url);
				socket.onmessage = () => resolve('it told us something');
				// Only `close` answers the question. A refused handshake fires `error` first and then
				// `close` with the code on it, so resolving on `error` would throw away the one piece
				// of information this test is about and report a refusal as a network problem.
				socket.onclose = (event) => resolve(`closed ${event.code}`);
			});
		});

		/* 1006, and NOT the 1008 the server passed to `close()`, which is the point of testing this
		 * in a browser rather than trusting the server's own view of it.
		 *
		 * The server refuses this one before accepting, so the handshake never completes and there is
		 * no socket for a close frame to travel on. The browser reports 1006 ("it ended and nobody
		 * said why"), which is also exactly what a network failure looks like. The refusal is real
		 * and total (nothing was sent), but the reason for it does not reach the client, and a
		 * client that comes back from everything except 1008 would therefore come back from this one
		 * forever. That is why the client asks the plain read what its standing is instead of
		 * guessing from the code. */
		expect(closed).toBe('closed 1006');
	});
});

/*
 * TWO BROWSERS, ONE LIBRARY.
 *
 * Everything else about the feed can be checked from one page: that a connection is opened, that it
 * is refused to the wrong person, that the account's standing is read before it opens. What none of
 * those can say is the thing the feed EXISTS for: that a change made somewhere else reaches a
 * screen that is already open, without anybody pressing anything.
 *
 * It has to be two contexts rather than two tabs of one, because a tab shares its session storage
 * and its in-memory stores with its sibling; two contexts are two browsers as far as this is
 * concerned, and the only thing they have in common is the server.
 *
 * A PERSON is what is created, and that is not arbitrary. The bell for "what this account may see
 * has changed" is deliberately not rung for a file arriving (those happen at a different rate and
 * only one of them changes what belongs in a list of people), so creating a person is the smallest
 * write that must ring it. It is also the one write this suite can make against an empty fixture
 * library with no media in it.
 */
test('a person created in one browser appears on a wall open in another', async ({ browser }) => {
	const watching = await browser.newContext();
	const writing = await browser.newContext();
	/* A name nothing else in the suite could have made, so a wall that simply had rows on it
	   already cannot pass this. */
	const name = `Live Check ${Date.now()}`;
	let created: string | null = null;

	try {
		const watcher = await watching.newPage();
		const writer = await writing.newPage();
		await signInAsAdmin(watcher);
		await signInAsAdmin(writer);

		await watcher.goto('/people');
		await expect(watcher.getByRole('heading', { name: 'People' }).first()).toBeVisible();

		/*
		 * NARROWED to the name first, and that is load-bearing rather than tidy. The wall is paged
		 * and ordered most-seen-first, so somebody created with no files on them sorts to the very
		 * end. On a library with more than one page of people the new row would be correct,
		 * present, and on a page nobody is looking at. Narrowing asks the server the same question
		 * the bell will make it ask again, so what arrives is what this is about.
		 */
		await watcher.getByRole('searchbox', { name: 'Search people' }).fill('Live Check');
		/* The wall has to be READ before the write, or this proves nothing: a page that had not
		   finished its first request would show the new person in that request and never hear a
		   thing from the connection. */
		await expect(watcher.getByText(name)).toHaveCount(0);

		const me = await writer.request.get('/api/auth/me');
		const token = (await me.json()).csrf_token as string;
		const made = await writer.request.post('/api/people', {
			data: { name, vault: false },
			headers: { 'x-csrf-token': token }
		});
		expect(made.ok(), await made.text()).toBeTruthy();
		created = ((await made.json()) as { id: string }).id;

		/* No reload, no navigation, no press. The only thing between the write and this assertion is
		   the connection the other test in this file counts. */
		await expect(watcher.getByText(name)).toBeVisible({ timeout: 20_000 });
	} finally {
		/* Put the library back. Every spec in this suite shares one server, and a person left behind
		   is a row on somebody else's wall and a number in somebody else's count. */
		if (created !== null) {
			const page = await writing.newPage();
			const me = await page.request.get('/api/auth/me');
			const token = (await me.json()).csrf_token as string;
			await page.request.delete(`/api/people/${created}`, {
				headers: { 'x-csrf-token': token }
			});
		}
		await watching.close();
		await writing.close();
	}
});
