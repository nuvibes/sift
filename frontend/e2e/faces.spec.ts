/* Recognizing faces with no model shipped: the screens exist, a fresh install reads as switched off
 * rather than broken, and the consent gate says what it consents to. */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
/* `\s+` between words: Playwright does not normalise whitespace for a regular expression. */
import { signInAsAdmin } from './admin';
import { pressCrumb } from './trail';

/* Set through the endpoint: the feature is install-wide and the server is shared, so pressing the
 * switch would toggle whatever the last test left. */
async function setFaces(page: Page, on: boolean): Promise<void> {
	const me = await page.request.get('/api/auth/me');
	const token = (await me.json()).csrf_token as string;
	const written = await page.request.put('/api/settings', {
		data: { values: { 'faces.enabled': on } },
		headers: { 'x-csrf-token': token }
	});
	expect(written.ok(), await written.text()).toBeTruthy();
}

/* Serial: the switch is install-wide, and half these tests need it on. */
test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

/* Left off, or every later spec's files queue a scan that can never run. */
test.afterAll(async ({ browser }) => {
	const page = await browser.newPage();
	try {
		await signInAsAdmin(page);
		await setFaces(page, false);
	} finally {
		await page.close();
	}
});

test('the face queues: the board leaves an empty pile off, and one page holds them as tabs', async ({
	page
}) => {
	/* The doors that exist: the rail to the board, and tabs inside one queue. The face queues are
	 * absent from the board while recognition is off. */
	await setFaces(page, true);
	await page.goto('/browse');

	await page.locator('nav[aria-label="Main"] a[data-rail-row="organize"]').click();
	await expect(page).toHaveURL(/\/organize(\?|$)/);

	/* With no model nothing waits; the pile's card says so and still opens its page. */
	await expect(page.getByText('Nothing needs you right now.', { exact: false })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Review Faces', exact: true })).toBeVisible();

	/* The group's first queue is named for it. */
	await page.goto('/organize/faces');
	await expect(page).toHaveURL(/\/organize\/faces$/);

	/* The queues sharing a page are tabs (`kernel/workbench.Queue.group`). */
	await page
		.getByRole('navigation', { name: 'Sections on this page' })
		.getByRole('link', { name: /People Sift can recognize/i })
		.click();
	await expect(page).toHaveURL(/\/organize\/known-people$/);
});

test('with nothing found, the sections say what they are for rather than looking broken', async ({
	page
}) => {
	await setFaces(page, true);
	await page.goto('/organize/faces');

	// Every install with no model sits here, so it explains rather than errs.
	await expect(page.getByText(/one\s+answer\s+names\s+all\s+of\s+them/i)).toBeVisible();

	// Discarded is a tab of the same page.
	await page.goto('/organize/discarded-faces');
	await expect(page.getByText(/Restore\s+one\s+at\s+any\s+time/i)).toBeVisible();
});

/** Where the recognition switches stand; the Faces pane links there. */
async function theIdentifyPage(page: Page): Promise<void> {
	await page.goto('/settings/tasks');
	await page.getByRole('button', { name: 'Edit Identify' }).click();
}

test('the switch is drawn as a consent gate, and says what stays on the machine', async ({
	page
}) => {
	await setFaces(page, false);
	await theIdentifyPage(page);

	await expect(
		page.getByRole('switch', { name: /Recognize faces in your library/i })
	).toBeVisible();

	// The promise is the feature: this is what somebody reads to decide.
	await expect(page.getByText(/never\s+leave\s+this\s+device/i)).toBeVisible();

	// Off on a fresh install: the pane's controls are absent, one row opens the tuning page.
	await page.goto('/settings/faces');
	await expect(page.getByText(/Turned\s+off\./)).toBeVisible();
	await expect(page.getByText(/lowest\s+face\s+quality/i)).toBeHidden();
	// Off is a state, not a fault.
	await expect(page.getByText(/Recognizing\s+faces\s+is\s+switched\s+off/).first()).toBeVisible();
	await expect(page.getByText("Couldn't load this list.")).toHaveCount(0);
});

test('switching it on does not claim it is ready, because no model ships with Sift', async ({
	page
}) => {
	// Pressed here, because the press is the subject.
	await setFaces(page, false);
	await theIdentifyPage(page);
	await page.getByRole('switch', { name: /Recognize faces in your library/i }).click();

	// What `ready` exists for: a fresh install must not read as broken.
	await expect(page.getByText(/models\s+aren't\s+downloaded\s+yet/i).first()).toBeVisible();
	await page.goto('/settings/faces');
	await expect(page.getByText(/lowest\s+face\s+quality/i)).toBeVisible();
});

test('the way to delete everything is separate from the switch, and says so', async ({ page }) => {
	await setFaces(page, false);
	await page.goto('/settings/faces');
	// The switch is named here and stands elsewhere.
	await expect(page.getByRole('switch', { name: /Recognize faces in your library/i })).toHaveCount(
		0
	);
	await expect(page.getByRole('link', { name: 'Change in Identify settings' })).toBeVisible();

	// What was collected is kept, so this must not be reachable by accident.
	await expect(page.getByText(/already\s+found\s+are\s+kept/i)).toBeVisible();
	await page.getByRole('button', { name: 'Delete face data' }).click();

	const dialog = page.getByRole('alertdialog');
	await expect(dialog).toBeVisible();
	await expect(dialog.getByText(/files\s+(are\s+not|aren't)\s+touched/i)).toBeVisible();
	await expect(dialog.getByText(/can't\s+be\s+undone/i)).toBeVisible();
});

test('a fresh install is offered the download, and told what it costs', async ({ page }) => {
	await setFaces(page, true);
	await page.goto('/settings/faces');

	// The one control that reaches the internet: what it fetches is licensed by others.
	const fetch = page.getByRole('button', { name: 'Download the models' });
	await expect(fetch).toBeVisible();

	// The consent gate is in front of the network call.
	await setFaces(page, false);
	await page.reload();
	await expect(fetch).toBeHidden();
});

/*
 * Back to the wall from a person's page, with tabs in between: each tab pushes history, so the
 * crumb goes to the wall's REMEMBERED address. `noteAddress` must treat a change of query as
 * standing still, and the wall must have written its row into the address.
 */
const FACE = {
	track_id: 't1',
	asset_id: 'a1',
	art: null,
	attribution: null,
	confidence: 0.9,
	started_ms: 0,
	ended_ms: 1000,
	is_reference: false,
	locked: false,
	person_id: 'idp1',
	person_name: 'Wren Halloway',
	pile_id: null,
	pile_status: null,
	teachable: true
};

/* The wall reads the people, the page one person's appearances; the patterns cannot collide. */
async function twoPeople(page: Page) {
	await page.route('**/api/faces/identified/people?*', (route) =>
		route.fulfill({
			json: {
				people: [
					{ person_id: 'idp1', person_name: 'Wren Halloway', size: 3, waiting: 0, faces: [FACE] },
					{
						person_id: 'idp2',
						person_name: 'Tobin Aske',
						size: 2,
						waiting: 0,
						faces: [{ ...FACE, track_id: 't2', person_id: 'idp2' }]
					}
				],
				total: 2,
				offset: 0
			}
		})
	);
	await page.route('**/api/faces/identified/people/*', (route) =>
		route.fulfill({
			json: { items: [FACE], total: 1, offset: 0, person_name: 'Wren Halloway' }
		})
	);
}

test('a tab pressed on a person does not lose the wall the trail comes back to', async ({
	page
}) => {
	await setFaces(page, true);
	await twoPeople(page);

	/* Matched by path: the tab it opens on is the query's. */
	const intoWren = page.locator('a[href^="/organize/known-people/idp1"]');
	await page.goto('/organize/known-people');
	await expect(intoWren).toBeVisible();

	/* The way back is FOR the row the wall names. */
	await expect.poll(() => new URL(page.url()).searchParams.get('from')).toBe('idp1');
	const address = new URL(page.url()).search;

	await intoWren.click();
	await expect(page).toHaveURL(/\/organize\/known-people\/idp1/);
	await expect(page.getByRole('heading', { name: 'Wren Halloway' })).toBeVisible();

	/* A tab: this same page with a different question. */
	await page
		.getByRole('navigation', { name: 'What to show' })
		.getByRole('link', { name: 'Confirmed' })
		.click();
	await expect(page).toHaveURL(/\/organize\/known-people\/idp1\?show=confirmed/);

	/* The queue's crumb, by name. */
	await pressCrumb(page, 'People Sift can recognize');

	await expect(page).toHaveURL(/\/organize\/known-people(\?|$)/);
	await expect(intoWren).toBeVisible();
	expect(
		new URL(page.url()).search,
		'the row the wall was showing was thrown away by the tab'
	).toBe(address);
});

test('three tabs pressed on a person are one history entry: Back leaves the page', async ({
	page
}) => {
	await setFaces(page, true);
	await twoPeople(page);

	await page.goto('/organize/known-people');
	await page.locator('a[href^="/organize/known-people/idp1"]').click();
	await expect(page).toHaveURL(/\/organize\/known-people\/idp1/);

	const tabs = page.getByRole('navigation', { name: 'What to show' });
	for (const name of ['Confirmed', 'Recognized by Sift', 'Needs your input']) {
		await tabs.getByRole('link', { name }).click();
		await expect(page).toHaveURL(new RegExp(`/organize/known-people/idp1\\?show=`));
	}

	await page.goBack();
	await expect(page).toHaveURL(/\/organize\/known-people(\?|$)/);
});
