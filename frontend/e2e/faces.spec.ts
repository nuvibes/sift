/* Recognizing faces, in a real browser.
 *
 * Nothing here can find a face: no model ships with Sift, so there is nothing to run. What these
 * check is everything around that: that the screens exist at their addresses, that a fresh
 * install reads as switched off rather than broken, and that the consent gate is drawn as a
 * consent gate rather than as an ordinary preference.
 *
 * The last of those is the one worth having in a browser at all. It is a claim about what somebody
 * READS before they turn something on, and the words are the feature: a switch that says "on/off"
 * and nothing about what it consents to would pass every unit test in the tree.
 */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
/* Phrases are matched with `\s+` between words: Playwright does not normalise whitespace for a
 * regular expression, and the formatter reflows the prose they are read from. */
import { signInAsAdmin } from './admin';
import { pressCrumb } from './trail';

/* Put the feature in a known position before the page is opened.
 *
 * Written through the settings endpoint rather than by pressing the toggle, and the distinction is
 * the point rather than a shortcut. Recognizing faces is install-wide, not per-account, and these
 * tests share one server. So a test that presses the switch to set up its precondition is
 * pressing whatever the last one left, and reads as a flaky toggle when it is really an ordering
 * assumption. Pressing the switch stays in the one test where the press IS the subject.
 */
async function setFaces(page: Page, on: boolean): Promise<void> {
	const me = await page.request.get('/api/auth/me');
	const token = (await me.json()).csrf_token as string;
	const written = await page.request.put('/api/settings', {
		data: { values: { 'faces.enabled': on } },
		headers: { 'x-csrf-token': token }
	});
	expect(written.ok(), await written.text()).toBeTruthy();
}

/* One at a time, and this file is the reason the rule exists.
 *
 * Recognizing faces is INSTALL-WIDE, not per-account, and every spec here shares one server. Half
 * these tests need it on and half need it off, so run in parallel they set it out from under each
 * other: the failure lands on whichever test happened to read the other one's value, moves between
 * runs, and reads as the switch being unreliable. Serial makes each test's own setup the thing it
 * gets.
 */
test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

/* Left as a fresh install has it. The switch is the whole server's, so left on it queues a face
 * scan for every file any later spec adds, and with no model none of them can ever run. */
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
	/*
	 * The face queues are reached from the doors that exist: the rail to the board, and the tabs
	 * inside one queue. Each is a different piece of wiring.
	 *
	 * Switched on first: the face queues are absent from the board entirely when recognition is
	 * off, which is deliberate (an empty panel reads as a feature that is broken rather than one
	 * that is not turned on).
	 */
	await setFaces(page, true);
	await page.goto('/browse');

	await page.locator('nav[aria-label="Main"] a[data-rail-row="organize"]').click();
	await expect(page).toHaveURL(/\/organize(\?|$)/);

	/* With no model there is nothing waiting in it, which is the ordinary state of such a library.
	   The pile keeps its card at nought and says so on it, and its button still opens its page; the
	   board page's own tests hold the rest. */
	await expect(page.getByText('Nothing needs you right now.', { exact: false })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Review Faces', exact: true })).toBeVisible();

	/* The group's first queue is named for the group (`/organize/faces`), and its page is there
	   whether or not the board is showing it. */
	await page.goto('/organize/faces');
	await expect(page).toHaveURL(/\/organize\/faces$/);

	/* And from inside one queue, the ones it shares a PAGE with are tabs beside the heading:
	   switching never means going back first. The row holds this queue's group
	   (`kernel/workbench.Queue.group`), so on the faces page it is To check and People Sift
	   knows, not every queue in the application. */
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

	// The empty state is the state every install with no model is permanently in, so it has to read
	// as an explanation rather than as an error: what would be here, and why answering it is worth it.
	await expect(page.getByText(/one\s+answer\s+names\s+all\s+of\s+them/i)).toBeVisible();

	// And what was discarded is a tab of the same page, which says it can be had back.
	await page.goto('/organize/discarded-faces');
	await expect(page.getByText(/Restore\s+one\s+at\s+any\s+time/i)).toBeVisible();
});

/**
 * The page the recognition switches stand on: `Settings > Tasks and Activity > Import tasks > Identify`, beside the other
 * recognition switches. The Faces pane names it and links there rather than drawing a second one.
 */
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

	// The promise the screen must make before anybody turns it on, beside the switch. Asserted as
	// text because it is the feature: this is what somebody reads to decide.
	await expect(page.getByText(/never\s+leave\s+this\s+device/i)).toBeVisible();

	// Off on a fresh install, and the controls on the Faces pane are absent rather than greyed out:
	// there is nothing to configure about a thing that is not running. They are one door, the
	// row that opens the tuning page, named for what is behind it.
	await page.goto('/settings/faces');
	await expect(page.getByText(/Turned\s+off\./)).toBeVisible();
	await expect(page.getByText(/lowest\s+face\s+quality/i)).toBeHidden();
	// Off is a state, not a fault: the list of who Sift can recognize says so rather than failing.
	await expect(page.getByText(/Recognizing\s+faces\s+is\s+switched\s+off/).first()).toBeVisible();
	await expect(page.getByText("Couldn't load this list.")).toHaveCount(0);
});

test('switching it on does not claim it is ready, because no model ships with Sift', async ({
	page
}) => {
	// Switched on by pressing the switch, because here the press is the subject: what the screen
	// says the moment somebody consents is the thing being checked.
	await setFaces(page, false);
	await theIdentifyPage(page);
	await page.getByRole('switch', { name: /Recognize faces in your library/i }).click();

	// The distinction the whole `ready` field exists for. Collapsed into one, a fresh install would
	// read as broken and send somebody looking for a fault that is not there.
	await expect(page.getByText(/models\s+aren't\s+downloaded\s+yet/i).first()).toBeVisible();
	await page.goto('/settings/faces');
	await expect(page.getByText(/lowest\s+face\s+quality/i)).toBeVisible();
});

test('the way to delete everything is separate from the switch, and says so', async ({ page }) => {
	await setFaces(page, false);
	await page.goto('/settings/faces');
	// The switch is not on this pane at all: it is named, and the press goes to where it stands.
	await expect(page.getByRole('switch', { name: /Recognize faces in your library/i })).toHaveCount(
		0
	);
	await expect(page.getByRole('link', { name: 'Change in Identify settings' })).toBeVisible();

	// Turning it off leaves what was collected (deliberately), which is exactly why this control
	// exists and why it must not be reachable by accident. The switch's own note says so.
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

	// The one control on this screen that makes the machine reach the internet. Offered rather
	// than automatic: what it fetches is licensed by somebody else, on their own terms.
	const fetch = page.getByRole('button', { name: 'Download the models' });
	await expect(fetch).toBeVisible();

	// Not offered before the feature is on: the consent gate is in front of the network call,
	// not beside it.
	await setFaces(page, false);
	await page.reload();
	await expect(fetch).toBeHidden();
});

/*
 * COMING BACK TO THE WALL FROM A PERSON'S PAGE, WITH TABS IN BETWEEN.
 *
 * The journey is the one a person actually makes on this screen: open somebody, look at the other
 * two tabs, then press the trail to get back. Every one of those tab presses is a real link at the
 * same address, so each pushes a history entry. One step of history would land on the previous
 * TAB and leave the wall three presses away. The crumb goes to the wall's REMEMBERED address
 * instead, which needs no count of steps.
 *
 * Two things hold it up and both are asserted, because either alone would pass while the journey
 * was broken: `noteAddress` must treat a change of QUERY as standing still rather than as a move,
 * or the remembered address becomes the person's own page; and the wall must have written the row
 * it was showing into its address in the first place, or there is nothing worth coming back to.
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

test('a tab pressed on a person does not lose the wall the trail comes back to', async ({
	page
}) => {
	await setFaces(page, true);

	/* Both routes are needed and they are two questions: the wall reads the people, and the page
	   under it reads one person's appearances. The patterns cannot collide: one carries a query
	   string and the other a path segment. */
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

	/* The wall's way into one person: their faces, opened on a tab of that page. Matched by the
	   path, because which tab it opens on is the query's. */
	const intoWren = page.locator('a[href^="/organize/known-people/idp1"]');
	await page.goto('/organize/known-people');
	await expect(intoWren).toBeVisible();

	/* The wall names the row it is showing, and that is what the way back is FOR. */
	await expect.poll(() => new URL(page.url()).searchParams.get('from')).toBe('idp1');
	const address = new URL(page.url()).search;

	await intoWren.click();
	await expect(page).toHaveURL(/\/organize\/known-people\/idp1/);
	await expect(page.getByRole('heading', { name: 'Wren Halloway' })).toBeVisible();

	/* A tab, which is a link to this same page with a different question on it. */
	await page
		.getByRole('navigation', { name: 'What to show' })
		.getByRole('link', { name: 'Confirmed' })
		.click();
	await expect(page).toHaveURL(/\/organize\/known-people\/idp1\?show=confirmed/);

	/* The queue's crumb: the trail is Organize, the group, the queue, then the person, and the
	   queue is the one to press. By its name, since the group's crumb sits between. */
	await pressCrumb(page, 'People Sift can recognize');

	await expect(page).toHaveURL(/\/organize\/known-people(\?|$)/);
	await expect(intoWren).toBeVisible();
	expect(
		new URL(page.url()).search,
		'the row the wall was showing was thrown away by the tab'
	).toBe(address);
});
