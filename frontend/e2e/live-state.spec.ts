import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/* No screen needs a reload to show a change.
 *
 * Two windows signed in as the same account: one makes a change through the ordinary endpoint,
 * the other has a screen open that draws the thing, and nothing is reloaded. The second window has
 * five seconds to show it, which is a beat of the live connection and a re-read with room to
 * spare. The unit tests hold each store to its bell and the gates hold every write to its
 * announcement; this is the one place the whole path (write, announcement, connection, bell,
 * re-read, screen) is walked end to end in a real browser.
 *
 * Every entity made here is made here and removed here, named so it sorts first on its wall.
 */

const SEEN_WITHIN_MS = 5_000;

async function csrf(page: Page): Promise<string> {
	const me = await page.request.get('/api/auth/me');
	return (await me.json()).csrf_token as string;
}

async function write(
	page: Page,
	method: 'post' | 'put' | 'delete',
	path: string,
	data?: object
): Promise<Record<string, unknown>> {
	const answer = await page.request[method](path, {
		data,
		headers: { 'x-csrf-token': await csrf(page) }
	});
	expect(answer.ok(), await answer.text()).toBeTruthy();
	return answer.status() === 204 ? {} : ((await answer.json()) as Record<string, unknown>);
}

/** Two pages, each in a context of its own, both signed in. */
async function twoWindows(browser: import('@playwright/test').Browser) {
	const one = await (await browser.newContext()).newPage();
	const other = await (await browser.newContext()).newPage();
	await signInAsAdmin(one);
	await signInAsAdmin(other);
	return { one, other };
}

const KINDS = [
	{ wall: '/tags', api: '/api/tags', named: 'Aaa live tag' },
	{ wall: '/collections', api: '/api/collections', named: 'Aaa live collection' },
	{ wall: '/people', api: '/api/people', named: 'Aaa live person' },
	{ wall: '/sites', api: '/api/sites', named: 'Aaa live Site' }
];

for (const kind of KINDS) {
	test(`a rename in one window reaches ${kind.wall} and its page in the other`, async ({
		browser
	}) => {
		const { one, other } = await twoWindows(browser);
		const made = await write(one, 'post', kind.api, { name: kind.named });
		const id = String(made.id);
		try {
			await other.goto(`${kind.wall}/${id}`, { waitUntil: 'load' });
			await expect(other.getByText(kind.named).first()).toBeVisible();

			await write(one, 'put', `${kind.api}/${id}`, { name: `${kind.named} renamed` });

			await expect(other.getByText(`${kind.named} renamed`).first()).toBeVisible({
				timeout: SEEN_WITHIN_MS
			});
		} finally {
			await write(one, 'delete', `${kind.api}/${id}`).catch(() => undefined);
		}
	});
}

test('a saved filter saved in one window is on the other without a reload', async ({ browser }) => {
	const { one, other } = await twoWindows(browser);
	await other.goto('/browse', { waitUntil: 'load' });

	await write(one, 'post', '/api/search/saved', {
		name: 'Aaa live filter',
		query: 'filetype=mp4',
		kind: 'asset'
	});

	/* Read again only by a window that has opened its saved filters; one that never did asks
	   nothing, and asks when it opens them. Either way the list the other window draws is the
	   account's list as it stands. */
	const saved = await other.request.get('/api/search/saved');
	const items = (await saved.json()).items as { id: string; name: string }[];
	const kept = items.find((one) => one.name === 'Aaa live filter');
	expect(kept).toBeTruthy();
	await write(one, 'delete', `/api/search/saved/${kept!.id}`);
});

test('a tunnel list open in one window follows a route chosen in the other', async ({
	browser
}) => {
	const { one, other } = await twoWindows(browser);
	await other.goto('/settings/sites', { waitUntil: 'load' });
	const reads: string[] = [];
	other.on('request', (asked) => {
		if (asked.method() === 'GET' && asked.url().includes('/api/download-routes')) {
			reads.push(asked.url());
		}
	});

	await write(one, 'put', '/api/download-routes/reddit', { route: 'direct' });

	await expect.poll(() => reads.length, { timeout: SEEN_WITHIN_MS }).toBeGreaterThan(0);
	await write(one, 'delete', '/api/download-routes/reddit');
});
