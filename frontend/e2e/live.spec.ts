import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* The one live connection, against the real server: only a real browser opens a WebSocket.
 * Every screen shares it, so exactly one is open. */

test('the application opens exactly one connection, whatever screen is on', async ({ page }) => {
	await signInAsAdmin(page);

	const sockets: string[] = [];
	const open = new Set<object>();
	page.on('websocket', (socket) => {
		sockets.push(socket.url());
		if (!socket.url().includes('/api/live/stream')) return;
		open.add(socket);
		socket.on('close', () => open.delete(socket));
	});

	await page.goto('/settings/tasks?show=now');
	await expect(page.getByRole('heading', { name: 'Tasks and Activity' })).toBeVisible();
	await expect.poll(() => open.size).toBe(1);
	await page.goto('/downloads');
	await expect(page).toHaveURL(/\/downloads/);
	await expect.poll(() => open.size, { timeout: 20_000 }).toBe(1);
	// The jobs-only feed, a second transport that must not exist.
	expect(sockets.filter((url) => url.includes('/api/jobs/stream'))).toHaveLength(0);
});

test('it says where the account stands before the connection is open', async ({ page }) => {
	// Read before connecting, or a change in that gap is never heard.
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
		// The request a guess would make: the server is what keeps it out.
		const state = await request.get('/api/live');

		expect(state.status()).toBe(401);
	});

	test('and the connection refuses them too', async ({ page }) => {
		// Signed out on the app's own origin, so only the question of who is asking remains.
		await page.goto('/login');

		const closed = await page.evaluate(async () => {
			const url = new URL('/api/live/stream', window.location.href);
			url.protocol = 'ws:';
			return await new Promise<string>((resolve) => {
				const socket = new WebSocket(url);
				socket.onmessage = () => resolve('it told us something');
				// Only `close` carries the code; `error` fires first.
				socket.onclose = (event) => resolve(`closed ${event.code}`);
			});
		});

		/* 1006, not the server's 1008: a refusal before the handshake carries no reason, so
		 * the client reads its standing from the plain route. */
		expect(closed).toBe('closed 1006');
	});
});

/* Two contexts share nothing but the server: a person created in one appears on the other's
 * open wall. A person is the smallest write that rings the bell. */
test('a person created in one browser appears on a wall open in another', async ({ browser }) => {
	const watching = await browser.newContext();
	const writing = await browser.newContext();
	const name = `Live Check ${Date.now()}`;
	let created: string | null = null;

	try {
		const watcher = await watching.newPage();
		const writer = await writing.newPage();
		await signInAsAdmin(watcher);
		await signInAsAdmin(writer);

		await watcher.goto('/people');
		await expect(watcher.getByRole('heading', { name: 'People' }).first()).toBeVisible();

		// Narrowed first: a new person with no files sorts to the end of a paged wall.
		await watcher.getByRole('searchbox', { name: 'Search people' }).fill('Live Check');
		// Read before the write, or the first request could carry the new person.
		await expect(watcher.getByText(name)).toHaveCount(0);

		const me = await writer.request.get('/api/auth/me');
		const token = (await me.json()).csrf_token as string;
		const made = await writer.request.post('/api/people', {
			data: { name, vault: false },
			headers: { 'x-csrf-token': token }
		});
		expect(made.ok(), await made.text()).toBeTruthy();
		created = ((await made.json()) as { id: string }).id;

		await expect(watcher.getByText(name)).toBeVisible({ timeout: 20_000 });
	} finally {
		// Removed: every spec shares one server.
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
