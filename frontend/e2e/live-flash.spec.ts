import { type Browser, type BrowserContext, type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { csrfOf, removePhotos, seedPhotos } from './seed';

/* A change made in one window shows in the other in place: nothing on the page moves, nothing is
 * taken off the screen and drawn again, and no loading state stands over what was drawn.
 *
 * The watching window is armed before the change: a layout-shift observer, a mutation observer
 * counting what is removed and added under the page, and a poll for the change itself. Each case
 * annotates its figures (ms from the writing window's answer to the change on screen, the shift,
 * the largest subtree removed) so a record can be kept from the hosted suite's own runs.
 */

const SHOWN_WITHIN_MS = 5_000;
/** The most a page may shift when the change itself inserts nothing above what is read. */
const STILL = 0.001;
/** A re-read that swaps more than this many elements out and back is a remount. */
const REMOUNT = 20;

// One case at a time: a row another case makes would land inside this one's watch.
test.describe.configure({ mode: 'serial' });

const opened: BrowserContext[] = [];
test.afterEach(async () => {
	for (const context of opened.splice(0)) await context.close();
});

async function twoWindows(browser: Browser) {
	const [first, second] = [await browser.newContext(), await browser.newContext()];
	opened.push(first, second);
	const [writer, watcher] = [await first.newPage(), await second.newPage()];
	await signInAsAdmin(writer);
	await signInAsAdmin(watcher);
	return { writer, watcher };
}

async function write(page: Page, method: 'post' | 'put' | 'delete', path: string, data?: object) {
	const answer = await page.request[method](path, {
		data,
		headers: { 'x-csrf-token': await csrfOf(page) }
	});
	expect(answer.ok(), await answer.text()).toBeTruthy();
	return answer.status() === 204 ? {} : ((await answer.json()) as Record<string, unknown>);
}

/** What `arm` waits to see: a text on the page, a tile by its id, or an attribute's value. */
type Shown = { text: string } | { tile: string } | { id: string; attribute: string; value: string };

/** Watch `page` for `shown` and for anything moving meanwhile. */
async function arm(page: Page, shown: Shown): Promise<void> {
	await page.evaluate((condition) => {
		const seen = { at: 0, shift: 0, removed: 0, biggest: 0, loading: 0 };
		(window as unknown as { __flash: typeof seen }).__flash = seen;
		new PerformanceObserver((list) => {
			for (const entry of list.getEntries() as unknown as {
				value: number;
				hadRecentInput: boolean;
			}[])
				if (!entry.hadRecentInput) seen.shift += entry.value;
		}).observe({ type: 'layout-shift' });
		new MutationObserver((records) => {
			for (const record of records) {
				for (const node of record.removedNodes)
					if (node instanceof Element) {
						const size = 1 + node.getElementsByTagName('*').length;
						seen.removed += size;
						seen.biggest = Math.max(seen.biggest, size);
					}
				for (const node of record.addedNodes)
					if (
						node instanceof Element &&
						node.matches('[aria-busy=true], .skeleton, [role=progressbar]')
					)
						seen.loading += 1;
			}
		}).observe(document.body, { childList: true, subtree: true });
		const check = () =>
			'text' in condition
				? document.body.innerText.includes(condition.text)
				: 'tile' in condition
					? [...document.querySelectorAll('[data-tile-id]')].some(
							(tile) => tile.getAttribute('data-tile-id') === condition.tile
						)
					: document.getElementById(condition.id)?.getAttribute(condition.attribute) ===
						condition.value;
		const poll = setInterval(() => {
			if (!seen.at && check()) {
				seen.at = Date.now();
				clearInterval(poll);
			}
		}, 50);
	}, shown);
}

/** The figures since `arm`, once the change has shown and a second has passed for anything after. */
async function figures(page: Page, answeredAt: number) {
	await expect
		.poll(
			() => page.evaluate(() => (window as unknown as { __flash: { at: number } }).__flash.at),
			{
				timeout: SHOWN_WITHIN_MS
			}
		)
		.toBeGreaterThan(0);
	await page.waitForTimeout(1_000);
	const seen = await page.evaluate(
		() => (window as unknown as { __flash: Record<string, number> }).__flash
	);
	const result = {
		ms: seen.at - answeredAt,
		shift: seen.shift,
		biggest: seen.biggest,
		loading: seen.loading
	};
	test.info().annotations.push({ type: 'live-flash', description: JSON.stringify(result) });
	return result;
}

test('a setting changed elsewhere moves its switch and the pane stays where it is', async ({
	browser
}) => {
	const { writer, watcher } = await twoWindows(browser);
	const key = 'theater.autoplay';
	const was = (
		(await (await writer.request.get('/api/settings')).json()) as {
			sections: { settings?: { key: string; value: unknown }[] }[];
		}
	).sections
		.flatMap((one) => one.settings ?? [])
		.find((one) => one.key === key)?.value;
	await watcher.goto('/settings/playback');
	const control = watcher.locator(`[id="${key}-control"]`);
	await expect(control).toBeVisible();
	await arm(watcher, { id: `${key}-control`, attribute: 'aria-checked', value: String(!was) });

	await write(writer, 'put', '/api/settings', { values: { [key]: !was } });
	const seen = await figures(watcher, Date.now());

	expect(seen.loading, 'the pane went back to its loading state').toBe(0);
	expect(seen.biggest, 'the pane was taken off the screen and drawn again').toBeLessThanOrEqual(
		REMOUNT
	);
	expect(seen.shift).toBeLessThanOrEqual(STILL);
	await write(writer, 'put', '/api/settings', { values: { [key]: was } });
});

for (const [list, path] of [
	['Tags', '/api/tags'],
	['Collections', '/api/collections']
] as const) {
	test(`a ${list} row made elsewhere slides in at the top and is held lower down`, async ({
		browser
	}) => {
		const { writer, watcher } = await twoWindows(browser);
		const screen = path.replace('/api', '');
		// A row below for the newcomer to slide past: an empty wall becoming a list is the screen
		// changing state, not a row arriving, and it moves by design.
		const below = await write(writer, 'post', path, { name: `Zzz below ${list} ${Date.now()}` });
		await watcher.addInitScript(
			(key) => localStorage.setItem(key, 'name_az'),
			`sift${screen.replace('/', '.')}.sort`
		);
		await watcher.goto(screen);
		// The placeholders giving way to the first cards is a shift of the page's own, not the row's.
		await expect(watcher.getByRole('status', { name: 'Loading' })).toHaveCount(0);
		await expect(watcher.getByText(`Zzz below ${list}`, { exact: false })).toBeVisible();
		await watcher.evaluate(() => document.fonts.ready);
		const name = `Aaa flash ${list} ${Date.now()}`;
		await arm(watcher, { text: name });

		const made = await write(writer, 'post', path, { name });
		const seen = await figures(watcher, Date.now());

		expect(seen.shift, 'the rows below jumped rather than slid').toBeLessThanOrEqual(STILL);
		await write(writer, 'delete', `${path}/${String(made.id)}`);
		await write(writer, 'delete', `${path}/${String(below.id)}`);
	});
}

test('a file hearted elsewhere joins Favorites without a reload', async ({ browser }) => {
	const { writer, watcher } = await twoWindows(browser);
	const seeded = await seedPhotos(writer, 'live-flash-favorites', 1);
	const file = seeded.assetIds[0];
	await watcher.goto('/favorites');
	await expect(watcher.getByRole('status', { name: 'Loading' })).toHaveCount(0);
	await arm(watcher, { tile: file });

	await write(writer, 'put', `/api/assets/${file}/favorite`, { favorite: true });
	const seen = await figures(watcher, Date.now());

	expect(seen.loading).toBe(0);
	await write(writer, 'put', `/api/assets/${file}/favorite`, { favorite: false });
	await removePhotos(writer, seeded);
});

test("a file's page says the file is gone when it is deleted elsewhere", async ({ browser }) => {
	const { writer, watcher } = await twoWindows(browser);
	const seeded = await seedPhotos(writer, 'live-flash-gone', 1);
	const file = seeded.assetIds[0];
	await watcher.goto(`/asset/${file}`);
	await expect(watcher.locator('.acts')).toBeVisible();
	await arm(watcher, { text: 'Not found.' });

	await write(writer, 'post', '/api/assets/delete', { asset_ids: [file], mode: 'sift' });
	await figures(watcher, Date.now());
	await removePhotos(writer, seeded);
});
