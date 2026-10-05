import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/* Making a smaller copy, driven in a real browser.
 *
 * Whether a target can be met, what a copy will be called and what the ladder does are all the
 * server's and are tested against the real encoder elsewhere. What is left for a browser is what
 * the panel does with the answer: that it asks before it offers a button, that it counts only the
 * files that will really run, that a file it cannot help is named with its reason rather than
 * hidden, and that the box to go ahead regardless puts them back.
 *
 * The count is the part worth a test of its own: a button offering to compress four files that
 * the server then skips would be followed by a toast saying nothing was queued.
 */

type FileVerdict = {
	asset_id: string;
	filename: string | null;
	output_filename: string | null;
	reachable: boolean;
	predicted_bytes: number | null;
	smallest_reachable_bytes: number | null;
	reason: string | null;
	copy_only: boolean;
	rewrap_only: boolean;
	converts_audio: boolean;
	skip_reason: string | null;
};

function aFile(over: Partial<FileVerdict> = {}): FileVerdict {
	return {
		asset_id: 'c1',
		filename: 'holiday.mp4',
		output_filename: 'holiday-25MB.mp4',
		reachable: true,
		predicted_bytes: 24 * 1024 * 1024,
		smallest_reachable_bytes: null,
		reason: null,
		copy_only: false,
		rewrap_only: false,
		converts_audio: false,
		skip_reason: null,
		...over
	};
}

function aPreflight(files: FileVerdict[]) {
	return {
		target_bytes: 25 * 1024 * 1024,
		files,
		eligible_count: files.filter((one) => !one.skip_reason).length,
		unreachable_count: files.filter((one) => !one.reachable && !one.skip_reason).length,
		copy_only_count: files.filter((one) => one.copy_only).length,
		audio_conversion_count: files.filter((one) => one.converts_audio).length,
		suggested_target_bytes: files.some((one) => !one.reachable) ? 60 * 1024 * 1024 : null
	};
}

const CLIP = {
	id: 'c1',
	media_type: 'video',
	width: 1920,
	height: 1080,
	duration_ms: 40_000,
	favorite: false,
	rating: null,
	concealed: false,
	original_filename: 'holiday.mp4'
};

async function serve(
	page: Page,
	answer: (body: Record<string, unknown>) => ReturnType<typeof aPreflight>,
	options: { asset?: Record<string, unknown> } = {}
): Promise<{ asked: Record<string, unknown>[]; started: Record<string, unknown>[] }> {
	const asset = { ...CLIP, ...options.asset };
	const asked: Record<string, unknown>[] = [];
	const started: Record<string, unknown>[] = [];

	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [asset], total: 1, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/c1', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(asset) })
	);
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/c1/view', (route) => route.fulfill({ status: 204, body: '' }));
	// The record panel's History pane reads this the moment it is the pane the account left open,
	// which it is, once any earlier spec on the shared account has pressed it. Unanswered, it
	// draws a "could not be read" alert beside the one this file asserts on.
	await page.route('**/api/assets/c1/history*', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[],"total":0}' })
	);

	/* Compressing lands a new file beside the original, so it is offered only where the library
	   has a folder to write into; one is served here, or the row is drawn greyed. */
	await page.route('**/api/library/roots', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ roots: [{ id: 'r1', name: 'Library' }] })
		})
	);
	await page.route(
		(url) => url.pathname === '/api/library/folders',
		(route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					folders: [
						{
							id: 'f1',
							root_id: 'r1',
							parent_id: null,
							name: 'Clips',
							rel_path: 'Clips',
							writable: true
						}
					]
				})
			})
	);

	await page.route('**/api/compress/preflight', (route) => {
		const body = route.request().postDataJSON() as Record<string, unknown>;
		asked.push(body);
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(answer(body))
		});
	});
	await page.route('**/api/compress', (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		const body = route.request().postDataJSON() as Record<string, unknown>;
		started.push(body);
		route.fulfill({
			status: 202,
			contentType: 'application/json',
			body: JSON.stringify({ job_ids: ['j1'], started: 1, skipped: 0 })
		});
	});

	return { asked, started };
}

/**
 * Open the file's own Options menu.
 *
 * The verbs on this screen are rows behind one door, the three dots every other surface in Sift
 * opens its verbs from, so it has no word on it and is found by its accessible name.
 */
async function openTheOptions(page: Page): Promise<void> {
	await page.getByRole('button', { name: 'Options for this file', exact: true }).click();
}

async function openThePanel(page: Page): Promise<void> {
	await page.goto('/asset/c1');
	await openTheOptions(page);
	await page.getByRole('menuitem', { name: 'Compress' }).click();
	await expect(page.getByRole('alertdialog')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('it asks the server before it offers a button', async ({ page }) => {
	const traffic = await serve(page, () => aPreflight([aFile()]));
	await openThePanel(page);

	await expect.poll(() => traffic.asked.length).toBeGreaterThan(0);
	expect(traffic.asked[0]).toMatchObject({ asset_ids: ['c1'], preset: 'standard' });
	await expect(page.getByRole('button', { name: 'Compress 1 file' })).toBeEnabled();
});

test('changing the target asks again', async ({ page }) => {
	const traffic = await serve(page, () => aPreflight([aFile()]));
	await openThePanel(page);
	await expect.poll(() => traffic.asked.length).toBeGreaterThan(0);

	/*
	 * A pressed row, not a checkbox. A 16-pixel box beside two lines of text is a thing to aim at
	 * rather than a thing to press, so the whole ROW is the control: a `Pressable` carrying
	 * `aria-pressed`, with the tick drawn inside it as a mark a screen reader is not told about. So
	 * the role is `button`, the state is `aria-pressed`, and the name is the words.
	 */
	const anywhere = page.getByRole('button', { name: /play it anywhere/i });
	await expect(anywhere).toHaveAttribute('aria-pressed', 'false');
	await anywhere.click();
	await expect(anywhere).toHaveAttribute('aria-pressed', 'true');

	await expect.poll(() => traffic.asked.at(-1)?.compatibility).toBe(true);
});

test('a file that already meets the target is taken off the count, not queued and skipped', async ({
	page
}) => {
	/* The panel must not offer to compress a file the server then passes over: the button would
	 * say one thing and the toast afterwards that nothing was queued.
	 */
	await serve(page, () => aPreflight([aFile({ copy_only: true })]));
	await openThePanel(page);

	await expect(page.getByText('1 file already meet this')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Nothing to compress' })).toBeDisabled();
});

test('a file that cannot be made that small is named, with its reason and a size that would work', async ({
	page
}) => {
	await serve(page, () =>
		aPreflight([
			aFile({
				reachable: false,
				predicted_bytes: null,
				smallest_reachable_bytes: 60 * 1024 * 1024,
				reason: '40s of 1920x1080 video cannot be worked down that far.'
			})
		])
	);
	await openThePanel(page);

	/* The compress panel's own warning, not any alert on the page. The alert ROLE sits on the
	   SENTENCE rather than on the box around it (a region role on the panel would have a
	   screen reader read the whole file list out with the announcement), so the alert carries
	   the count and the names sit beside it, inside the same advisory. */
	const warning = page.getByRole('alert').filter({ hasText: "can't be made that small" });
	await expect(warning).toContainText("1 file can't be made that small");

	const advisory = page.locator('.advisory');
	await expect(advisory).toContainText('holiday.mp4');
	await expect(advisory).toContainText('cannot be worked down that far');
	await expect(advisory).toContainText('60');
});

test('going ahead regardless is never pre-selected, and puts the refused files back', async ({
	page
}) => {
	const traffic = await serve(page, () =>
		aPreflight([
			aFile({
				reachable: false,
				predicted_bytes: null,
				smallest_reachable_bytes: 60 * 1024 * 1024,
				reason: '40s of 1920x1080 video cannot be worked down that far.'
			})
		])
	);
	await openThePanel(page);

	/* A pressed ROW rather than a checkbox, for the reason given on the test above. `aria-pressed`
	   is what carries the state, so "never pre-selected" is that attribute reading false. */
	const anyway = page.getByRole('button', { name: /anyway/i });
	await expect(anyway).toHaveAttribute('aria-pressed', 'false');
	await expect(page.getByRole('button', { name: 'Nothing to compress' })).toBeDisabled();

	await anyway.click();

	const go = page.getByRole('button', { name: 'Compress 1 file' });
	await expect(go).toBeEnabled();
	await go.click();

	await expect.poll(() => traffic.started.length).toBe(1);
	expect(traffic.started[0]).toMatchObject({ force: true });
});

test('a file the verb should never have run on is listed as left alone', async ({ page }) => {
	await serve(page, () =>
		aPreflight([aFile({ skip_reason: 'A photograph is resized rather than compressed.' })])
	);
	await openThePanel(page);

	await expect(page.getByText('1 file will be left alone')).toBeVisible();
	await expect(page.getByText('A photograph is resized rather than compressed.')).toBeVisible();
});

test('starting it sends what was asked about and says it is running', async ({ page }) => {
	const traffic = await serve(page, () => aPreflight([aFile()]));
	await openThePanel(page);

	await page.getByRole('button', { name: 'Compress 1 file' }).click();

	await expect.poll(() => traffic.started.length).toBe(1);
	expect(traffic.started[0]).toMatchObject({ asset_ids: ['c1'], preset: 'standard', force: false });
	await expect(page.getByText(/it runs in the background/)).toBeVisible();
});

test('a photograph is not offered compression at all', async ({ page }) => {
	/* Its answer is to resize it, which is the editor's. Offering both would be two buttons for one
	 * question with different answers. */
	await serve(page, () => aPreflight([aFile()]), {
		asset: { media_type: 'image', duration_ms: null, original_filename: 'holiday.jpg' }
	});
	await page.goto('/asset/c1');
	await openTheOptions(page);
	await expect(page.getByRole('menuitem', { name: 'Modify' })).toBeVisible();

	await expect(page.getByRole('menuitem', { name: 'Compress' })).toHaveCount(0);
});

test('a button that cannot be pressed is drawn as one', async ({ page }) => {
	/* Without the shared sheet chrome's disabled style, every dialog in the app would draw
	 * an unpressable Apply identically to a working one, which reads as the button being broken
	 * rather than as there being nothing to do. */
	await serve(page, () => aPreflight([aFile({ copy_only: true })]));
	await openThePanel(page);

	const dead = page.getByRole('button', { name: 'Nothing to compress' });
	await expect(dead).toBeDisabled();

	const live = page.getByRole('button', { name: 'Cancel' });
	const [deadLook, liveLook] = await Promise.all([
		dead.evaluate((el) => {
			const style = getComputedStyle(el);
			return `${style.opacity}|${style.backgroundColor}|${style.cursor}`;
		}),
		live.evaluate((el) => {
			const style = getComputedStyle(el);
			return `${style.opacity}|${style.backgroundColor}|${style.cursor}`;
		})
	]);

	expect(deadLook, 'a dead button looks exactly like a live one').not.toBe(liveLook);
});
