import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';
import { csrfOf, skipTheFirstFolderBenchmark } from './seed';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

/* The library, in a real browser, against the real server.
 *
 * The folders here are really made and the media in them is really indexed: these run against the
 * same machine the server is on, so a path typed into the form is a path the server can open. That
 * is the whole point: everything about this screen is a claim about a real disk, and a fixture that
 * pretended to be one would only ever confirm what the code already believed about it.
 */

// Serial, and it is not a workaround. Every test here adds a real folder to the one library the
// one server has, so they are not independent: run at once they collide over the same roots, and a
// locator for "the folder I just added" finds three of them. The suite is parallel across files,
// which is where the time is.
test.describe.configure({ mode: 'serial' });

//: A real, decodable video, borrowed from the corpus the ingress gate is tested against. Not drawn
//: with ffmpeg here: a test that had to build its own fixture would fail for reasons that have
//: nothing to do with the library.
const CLIP = fileURLToPath(
	new URL('../../src/sift/kernel/tests/fixtures/ingress/accepted.mp4', import.meta.url)
);

/* Where the picker looks.
 *
 * The server is started with its browse root pointed here (e2e/serve.mjs sets it), because the
 * picker will not show a folder outside it, which is the point of the picker, and means a test
 * cannot make a folder in /tmp and name it. The two have to agree; if this moves, that moves.
 */
const MEDIA = fileURLToPath(new URL('./.media', import.meta.url));

/** The media area's own name, which is how the picker's list of handed-over folders shows it. */
const MEDIA_NAME = '.media';

/**
 * Hand the media area to Sift, as the operating system's folder dialog does on the desktop.
 *
 * The picker in a browser walks only the folders that were handed over, and a fresh install has
 * none, so every test that adds a folder by clicking needs the media area granted first. Once is
 * enough: a second grant of the same folder is refused as an overlap.
 */
async function handOverTheMediaArea(page: Page): Promise<void> {
	const { grants } = (await (await page.request.get('/api/library/grants')).json()) as {
		grants: { path: string }[];
	};
	if (grants.some((grant) => grant.path.toLowerCase() === MEDIA.toLowerCase())) return;
	const granted = await page.request.post('/api/library/grants', {
		data: { path: MEDIA },
		headers: { 'x-csrf-token': await csrfOf(page) }
	});
	expect(granted.ok(), `could not hand over the media area: ${await granted.text()}`).toBeTruthy();
}

/*
 * Every folder this file makes in the media area, by name.
 *
 * The media area is shared: other spec files seed folders of their own into it (`seed.ts`) and
 * run beside this one. So what this file clears before it starts is this list and nothing else,
 * and `aFolderWith` takes only a name on it, which is what keeps the list whole.
 */
const OWN = [
	'Added',
	'clicked',
	'Private',
	'Outer',
	'Scanned',
	'Guarded',
	'Announced',
	'Doomed',
	'Kept',
	'Fragile',
	'Shelf'
] as const;

/** A real directory under the media area, with real media in it, that the picker will show. */
function aFolderWith(files: string[], as: (typeof OWN)[number]): string {
	/* Named exactly, not `mkdtemp`-suffixed, and that is load-bearing rather than tidy.
	 *
	 * A folder has no name of its own: it is read off the path, so the filesystem owns it
	 * and there is nothing to type in. Which means the directory's name IS what every assertion
	 * below looks for, on the row, in the toast, and in the confirmation. A random suffix would make
	 * every one of those a substring match, and a substring match is how a test comes to pass
	 * against the wrong folder. Each name here is used by one test, and the first test in this file
	 * removes whatever an earlier run left under them.
	 */
	const directory = join(MEDIA, as);
	mkdirSync(directory, { recursive: true });
	for (const name of files) {
		const target = join(directory, name);
		mkdirSync(join(target, '..'), { recursive: true });
		/* A tail after the clip's own data, named for where the copy is, so every copy is a file
		   of its own to the library: a file is known by its content, and one clip copied into
		   several folders would otherwise be one file found again, with nothing new to read. */
		writeFileSync(target, Buffer.concat([readFileSync(CLIP), Buffer.from(`${as}/${name}`)]));
	}
	return directory;
}

/**
 * Send the first-run dialog away, if this run has not added a folder yet.
 *
 * It is modal AND it draws its own copy of the folder picker, so with it up there are two pickers
 * on screen and the clicks below land on whichever is on top. Whether it is there at all depends on
 * which test in this serial file has already run. The first one deliberately empties the media
 * area, and every test after it adds a folder back. Conditional rather than unconditional for that
 * reason: both states are correct, and neither is this helper's business.
 */
async function dismissFirstRun(page: Page): Promise<void> {
	/* The close in the corner, which is the only control that ends the flow. The folder step's
	   own quiet button steps FORWARD. Pressing it here would leave the next question on
	   screen, still modal, still drawing over the picker these tests click. */
	const skip = page.getByRole('button', { name: 'Set this up later' });
	/* A bounded WAIT rather than an instant check. The dialog is drawn only once the roots request
	 * comes back, so asking whether it is visible the moment the page loads races it and almost
	 * always says no, which is a dismissal that silently does nothing. */
	const showed = await skip
		.waitFor({ state: 'visible', timeout: 2000 })
		.then(() => true)
		.catch(() => false);
	if (showed) await skip.click();
}

/**
 * Add a folder the way a person does: by clicking through to it, never by typing a path.
 *
 * The directory has to exist BEFORE the screen is opened. The picker lists what it found when it
 * mounted, so a folder created afterwards is simply not among the buttons, which reads as the
 * picker being broken rather than as the test having got ahead of it. Every caller below creates
 * its folder first and navigates second, for that reason.
 *
 * `path` has to be inside the media area, and what this walks is the segments between the two.
 * There is no text field to fall back on, and a test that could type one would be testing something
 * the app does not have.
 */
async function addFolder(page: Page, path: string): Promise<void> {
	await handOverTheMediaArea(page);
	await dismissFirstRun(page);
	// The form is a dialog rather than a block standing open under the list, so it has to be
	// opened before anything in it can be filled in.
	await page.getByRole('button', { name: 'Add a folder' }).click();

	/* Scoped to the Library screen's own picker. There can be a second one on screen (the
	 * first-run dialog draws a picker of its own, labelled "Your media folder"), and an unscoped
	 * click is then ambiguous between two buttons with the same folder name on them. */
	const picker = page.getByRole('dialog', { name: 'Add a folder' });
	// The top of the picker is the list of folders handed over, so the walk starts at the media area.
	await picker.getByRole('button', { name: MEDIA_NAME, exact: true }).click();
	const segments = relative(MEDIA, path).split(sep).filter(Boolean);
	for (const segment of segments) {
		await picker.getByRole('button', { name: segment, exact: true }).click();
	}
	const answered = page.waitForResponse(
		(response) =>
			response.url().endsWith('/api/library/roots') && response.request().method() === 'POST'
	);
	await page.getByRole('button', { name: 'Add folder' }).click();
	await answered;
	// The first folder of a library waits behind a benchmark of this device; these are not about it.
	await skipTheFirstFolderBenchmark(page);
}

test('a fresh install, with nothing handed to Sift yet, explains what to do', async ({ page }) => {
	// FIRST IN THIS FILE, AND IT HAS TO BE: every test below puts a folder in the media area, and
	// this is the one state a brand-new install is in. Serial mode runs this file in order.
	//
	// It removes this file's own folders itself rather than trusting the server to have started
	// without them. Playwright reuses a running server between local runs, so on the second run the
	// folders the first run added would still be sitting there, and a test below would fail for a
	// reason that has nothing to do with what it is checking. Only its own: a folder another file
	// seeded is in use by a test running beside this one.
	for (const leftover of OWN) {
		rmSync(join(MEDIA, leftover), {
			recursive: true,
			force: true,
			maxRetries: 40,
			retryDelay: 250
		});
	}

	await signInAsAdmin(page);
	/* The library as a fresh install has it: no folders. Answered here rather than read off the
	   server, which the suite's other files share and seed photos into while this one runs. */
	await page.route('**/api/library/roots', (route) =>
		route.request().method() === 'GET'
			? route.fulfill({ status: 200, contentType: 'application/json', body: '{"roots":[]}' })
			: route.fallback()
	);
	await page.goto('/settings/library');

	/* Nothing is drawn over this screen, even on a first run: there is no first-run flow, so
	 * there is nothing to dismiss and only one of everything. The count guards against a second
	 * copy of this screen being drawn over the first, whatever draws it.
	 */
	await expect(page.locator('.veil')).toHaveCount(0);

	/* Not an empty picker, which would say nothing about what to do next. Still scoped to the pane:
	 * it says which screen these belong to, and it is what will fail loudly rather than ambiguously
	 * if a second copy is ever drawn over this one. */
	const pane = page.locator('.settings .pane');
	/* A REGION, not a heading. The screen's own title is the `<h1>Folders</h1>` above it, so a
	   second heading saying the same thing would be the same fact twice; it is this section's
	   accessible name instead. The assertion is that the list identifies itself rather than
	   being an unlabelled picker. */
	await expect(pane.getByRole('region', { name: 'Your folders' })).toBeVisible();
	await expect(
		pane.getByText('No folders yet. Add one and Sift will read what is in it.')
	).toBeVisible();

	/* And a way forward from here, which is the whole reason this screen says anything at all.
	 * A regex, not the exact name: the button carries an icon, and the icon font puts its ligature
	 * inside the button's text. */
	await expect(pane.getByRole('button', { name: /Add a folder/ })).toBeVisible();

	/* No install instructions on this screen.
	 *
	 * Whether this page can open a folder dialog is a fact about the browser, not about the
	 * server, so it cannot choose which instructions apply to an install. A person following an
	 * instruction that does not apply to their install ends up further from a working library
	 * than they started; the README is where install instructions live.
	 */
	for (const wrong of ['docker compose', 'container', 'volumes', 'MEDIA_DIR', 'compose file']) {
		await expect(pane.getByText(wrong)).toHaveCount(0);
	}
});

test('the library is a screen, not a placeholder', async ({ page }) => {
	await signInAsAdmin(page);
	await page.goto('/settings/library');

	await expect(page.getByRole('heading', { name: 'Folders', level: 1 })).toBeVisible();
	// Exact, because the screen's own title contains these words too.
	await expect(page.getByRole('region', { name: 'Your folders' })).toBeVisible();
});

test('adding a folder shows it, watched, and says what is inside it', async ({ page }) => {
	await signInAsAdmin(page);
	const directory = aFolderWith(['holiday.mp4'], 'Added');

	await page.goto('/settings/library');
	await addFolder(page, directory);

	await expect(rootRow(page, 'Added')).toBeVisible();

	// A folder with files in it and no folders inside it says so, rather than showing an empty
	// panel. That sentence is the ordinary case (most people hand over one folder of files), and
	// it is the difference between "nothing here" and "something is broken".
	await rootRow(page, 'Added')
		.getByRole('button', { name: /what is inside Added/ })
		.click();
	await expect(page.getByText('No folders inside this one.')).toBeVisible();
});

test('a folder is added by clicking, and there is nothing left to type a path into', async ({
	page
}) => {
	// The whole point of the picker. Somebody running Sift in a container has no way to know the
	// path their folder has inside it, so the app does not ask: it shows what it can see and they
	// click. The second half matters as much as the first: a text box left behind would go on
	// being the thing people reach for, and it cannot work.
	await signInAsAdmin(page);
	const directory = aFolderWith(['holiday.mp4'], 'clicked');
	await handOverTheMediaArea(page);

	await page.goto('/settings/library');
	const folder = relative(MEDIA, directory);

	/* NOTHING is typed: no path, and no name. A folder's name is read off its path, so the
	 * filesystem owns it and a directory renamed on the host cannot drift from a name typed on
	 * the day it was added.
	 *
	 * Counted across the whole form rather than looked for by label: a locator that finds nothing
	 * passes a "there is no box" assertion for the wrong reason.
	 */
	await page.getByRole('button', { name: 'Add a folder' }).click();

	const form = page.locator('form.add');
	await expect(form.getByRole('textbox')).toHaveCount(0);
	await expect(page.getByPlaceholder('/media/videos')).toHaveCount(0);

	// Clicking a folder both enters it and chooses it.
	await page.getByRole('button', { name: MEDIA_NAME, exact: true }).click();
	await page.getByRole('button', { name: folder, exact: true }).click();
	await page.getByRole('button', { name: 'Add folder' }).click();

	// And it comes back under the directory's own name.
	await expect(rootRow(page, folder)).toBeVisible();
});

test('a folder the picker will not show cannot be reached by asking for it', async ({ page }) => {
	// The confinement, from outside. The browser is the only way in for a person, and the endpoint
	// behind it refuses anywhere that is not inside the media area, whether it is asked for by
	// climbing out, by naming somewhere else, or for the root of the filesystem itself.
	await signInAsAdmin(page);
	await page.goto('/settings/library');

	for (const path of ['/etc', '/', `${MEDIA}/../..`]) {
		const refused = await page.request.get(`/api/library/browse?path=${encodeURIComponent(path)}`);
		expect(refused.status(), `browsing ${path} should be refused`).toBe(400);
	}

	/* And what it does answer is inside the media area, every time.
	 *
	 * The top level has NO PATH OF ITS OWN, and that is the shape rather than an omission: it is
	 * the list of folders that were handed over, which can be several places on several drives, so
	 * there is no one directory it is "in". Where each of them is is on the rows, and every row
	 * here is inside the media area, which is the confinement this is checking. */
	const allowed = await (await page.request.get('/api/library/browse')).json();
	expect(allowed.path).toBe('');
	expect(allowed.entries.length).toBeGreaterThan(0);
	for (const entry of allowed.entries) {
		expect(entry.path.startsWith(MEDIA)).toBe(true);
	}
});

test('the path is on the row, and only an admin can ask for it', async ({ page }) => {
	/* A root's path reaches the browser: without it, six folders on one machine all read "A
	 * folder on this computer" and nothing tells two similarly-named folders apart.
	 *
	 * What makes it safe is the route, not the field being absent: listing folders is admin-only,
	 * so the only account that ever sees a path is the one that chose it. The guest half of that
	 * is asserted by the test below, which is the half doing the work.
	 */
	await signInAsAdmin(page);
	const directory = aFolderWith(['holiday.mp4'], 'Private');

	await page.goto('/settings/library');
	await addFolder(page, directory);
	await expect(rootRow(page, 'Private')).toBeVisible();

	/* Compared as a FIELD rather than by searching the serialised body. A path here holds
	 * backslashes on Windows, and JSON doubles every one of them. So a search for the path the
	 * test created would never match the path the response carried, on the operating system where
	 * they differ and nowhere else. */
	const roots = await (await page.request.get('/api/library/roots')).json();
	expect(roots.roots.map((root: { path: string }) => root.path)).toContain(directory);
	await expect(rootRow(page, 'Private')).toContainText(directory);
});

test('a folder inside a folder Sift already reads is refused, in words', async ({ page }) => {
	await signInAsAdmin(page);
	const directory = aFolderWith(['clips/holiday.mp4'], 'Outer');

	await page.goto('/settings/library');
	await addFolder(page, directory);
	await expect(rootRow(page, 'Outer')).toBeVisible();

	await addFolder(page, join(directory, 'clips'));

	// Under the box they typed it in, and it names the folder it clashes with.
	const refusal = page.getByText(/already watching/);
	await expect(refusal).toBeVisible();
	await expect(refusal).toContainText('Outer');
});

test('the jobs dashboard updates live while a folder is being read', async ({ page }) => {
	// Nothing in Sift enqueues a probe except a scan, so this is the only place the dashboard can be
	// watched doing what it is for: real work, appearing while somebody looks at it, pushed down the
	// socket rather than found by refreshing.
	await signInAsAdmin(page);
	const directory = aFolderWith(['one.mp4', 'clips/two.mp4'], 'Scanned');

	await page.goto('/settings/library');
	await addFolder(page, directory);
	await expect(rootRow(page, 'Scanned')).toBeVisible();

	await page.goto('/settings/tasks?show=now');
	await expect(page.getByRole('heading', { name: 'Tasks and Activity' })).toBeVisible();

	// Adding the folder did not scan it: watching it does that when something changes, and nothing
	// has. So the scan is asked for, and the dashboard is already open when it is.
	await page.goto('/settings/library');
	await rowAction(page, 'Scanned', 'Scan now');
	await page.goto('/settings/tasks?show=now');

	// What a person sees, which is the whole reason to drive a browser. The dashboard has its own
	// word for a probe (nobody wants to learn what "probe" means to find out what their library is
	// doing), so this looks for the word on the screen rather than the one in the queue.
	//
	// Under the scan that asked for it. The queue folds a family into its top row (a folder's scan
	// is one row with its steps under it), so a probe is a step inside the scan's row. Read on the
	// list of everything, not the Done tab: a family is done only when its last step is, and a
	// file's later steps can wait on things this library does not have (recognition switched on
	// with no models keeps one queued), which is no part of what is watched here.
	const opener = page.getByRole('button', { name: 'Show more: the steps of Scanned' }).first();
	await expect(opener).toBeVisible({ timeout: 20_000 });
	await opener.click();
	await expect(page.getByText('Probing file').first()).toBeVisible({ timeout: 20_000 });

	// And that it is there at all is the fan-out contract holding across a boundary where the two
	// slices share nothing but a string: the scan asked for a probe by name, and something answered.
	const rows = page.getByRole('listitem');
	await expect(rows.filter({ hasText: 'Probing file' }).first()).toBeVisible();
});

test('a guest is not shown the folders, and cannot ask for them either', async ({ page }) => {
	await signInAsAdmin(page);
	const directory = aFolderWith(['holiday.mp4'], 'Guarded');
	await page.goto('/settings/library');
	await addFolder(page, directory);
	await expect(rootRow(page, 'Guarded')).toBeVisible();

	// A guest account, made the way an admin makes one, and then asked directly. The screen not
	// drawing the settings is tidiness; this is the part that is not.
	const refused = await page.request.get('/api/library/roots', {
		headers: { Cookie: 'sift_session=not-a-real-session' }
	});
	expect(refused.ok()).toBeFalsy();
});

/**
 * The row for a named library folder.
 *
 * Scoped to the folder list by name, so it cannot pick up a toast, a menu item or one of the rows
 * the folder tree draws inside this one. A library folder is a row here, not an item in a tree
 * beside the list.
 */
function rootRow(page: Page, name: string) {
	return page
		.getByRole('list', { name: 'Library folders' })
		.getByRole('listitem')
		.filter({ hasText: name });
}

/**
 * One of a folder row's actions.
 *
 * They are behind one control (six words appearing on hover would wrap under the name and make
 * every row three lines tall), so reaching one means opening it first, and they are menu items
 * rather than buttons, which is what the roles below say.
 */
async function rowAction(page: Page, root: string, action: string | RegExp) {
	/*
	 * OPENED AGAIN RATHER THAN CLICKED HARDER, and the difference is the whole of this.
	 *
	 * This screen re-reads on every library announcement, and adding a folder starts a scan, so a
	 * menu opened here can be rebuilt underneath the pointer a moment later. Playwright retries a
	 * click, but it retries against the element it already resolved: once that has been detached
	 * there is nothing to retry, and it waits out the whole test timeout reporting "not stable" and
	 * then "detached from the DOM".
	 *
	 * What has to be retried is the OPENING. Three goes, each with a short deadline of its own, and
	 * the menu dismissed in between so the next go starts from a closed one.
	 */
	for (let go = 1; ; go += 1) {
		await takingClicksAgain(page);
		try {
			await rootRow(page, root)
				.getByRole('button', { name: `More for ${root}` })
				.click({ timeout: 5_000 });
			await page.getByRole('menuitem', { name: action }).click({ timeout: 5_000 });
			return;
		} catch (refused) {
			if (go === 3) throw refused;
			await page.keyboard.press('Escape');
		}
	}
}

/**
 * Wait until the page is answering the pointer again.
 *
 * A dialog closing is not over when it is hidden. For ~200ms after the alert goes, the sheet behind
 * it is still `inert` and the body still carries `pointer-events: none`, which is the layer keeping
 * clicks off things that are sliding away. A menu opened inside that window is a menu whose item is
 * never clickable: Playwright reports it as "not stable", then as detached when the inert lifts
 * and the tree is rebuilt underneath, and retries until the test times out.
 *
 * `toBeHidden()` is satisfied at the first frame of that window, which is why waiting on the dialog
 * is not enough and this waits on the CONSEQUENCE instead: is the page taking clicks.
 *
 * Asked of the folder list, not of the whole document: a folded bar or a closed drawer stays
 * `inert` for as long as it is folded, which is not something closing.
 */
async function takingClicksAgain(page: Page) {
	await expect
		.poll(
			() =>
				page.evaluate(
					() =>
						document.querySelector('[aria-label="Library folders"]')?.closest('[inert]') == null &&
						getComputedStyle(document.body).pointerEvents !== 'none'
				),
			{ message: 'the page is still refusing clicks from something that is closing' }
		)
		.toBe(true);
}

test('a toast says the folder was taken, and then takes itself away', async ({ page }) => {
	// Two things a fake timer and a fake DOM cannot show. That the strip is rendered at all: a
	// store that is full and correct with nothing rendering it passes every unit test. And that a
	// message that is not an error leaves on its own, on the real four-second clock rather than one
	// a test advanced by hand.
	await signInAsAdmin(page);
	const directory = aFolderWith(['holiday.mp4'], 'Announced');

	await page.goto('/settings/library');
	await addFolder(page, directory);

	// No full stop: a one-sentence toast carries none.
	const toast = page.getByText('Sift is reading Announced');
	await expect(toast).toBeVisible();
	await expect(toast).toBeHidden({ timeout: 8000 });
});

test('a toast about a failure stays until it is dismissed', async ({ page }) => {
	// The other half of the rule: an error does not take itself away, because a failure the app
	// mentioned to nobody is a failure it did not report. Driven by a real refusal, not a planted
	// one. The root is deleted out from under the open screen (two tabs, two admins), so the next
	// thing the stale row is asked to do is answered "gone".
	await signInAsAdmin(page);
	const directory = aFolderWith(['holiday.mp4'], 'Doomed');

	/*
	 * THE LIVE FEED IS HELD OFF, and that is what makes this scenario reachable at all.
	 *
	 * The premise is a STALE screen (the comment above says two tabs and two admins), and the
	 * live feed is exactly what stops a screen being stale: the deletion below announces, this
	 * screen re-reads, and the row is gone before anything can be asked of it. That is the feature
	 * working, and it is not what this test is about.
	 *
	 * `routeWebSocket` and not `route`: the feed is a WEBSOCKET, and `page.route` does not see one.
	 * Held open and silent rather than refused: a refused connection is retried, and a client
	 * reconnecting in a loop is a screen that never settles under the pointer.
	 */
	await page.routeWebSocket('**/api/live/stream*', () => {});

	await page.goto('/settings/library');
	await addFolder(page, directory);
	await expect(rootRow(page, 'Doomed')).toBeVisible();

	const { roots } = await (await page.request.get('/api/library/roots')).json();
	const doomed = roots.find((root: { name: string }) => root.name === 'Doomed');
	// The token the client itself carries on a state-changing request, from where the client gets it.
	const { csrf_token } = await (await page.request.get('/api/auth/me')).json();
	const deleted = await page.request.delete(`/api/library/roots/${doomed.id}`, {
		headers: { 'x-csrf-token': csrf_token }
	});
	expect(deleted.ok()).toBeTruthy();

	// The screen still shows the row; ask it to do something and the server refuses.
	await rowAction(page, 'Doomed', 'Scan now');

	const failed = page.locator('.toast.error');
	await expect(failed).toBeVisible();

	// Still there well past the four seconds a success toast is given. Real wall-clock, because the
	// thing under test is that the timer does not fire for this one.
	await page.waitForTimeout(5000);
	await expect(failed).toBeVisible();

	// And it goes when, and only when, it is dismissed by hand.
	await failed.getByRole('button', { name: /^Dismiss:/ }).click();
	await expect(failed).toBeHidden();
});

test('a destructive confirm keeps focus, and both ways out decline', async ({ page }) => {
	/*
	 * The alert flavour, proven where it is real: focus is moved into the dialog rather than left
	 * on the page behind it, and both ways out of it (Escape, and a click beside it) decline
	 * rather than answer. Neither survives being checked without a real browser.
	 *
	 * A click beside an alert dialog closes it here, unlike the library's default: ignoring it
	 * reads as stuck, and it would be the one thing on the screen that does not close the way
	 * everything else does. Cancel is the safe half of every question Sift asks, so dismissing can
	 * only ever decline, which is what the last assertion here checks.
	 */
	await signInAsAdmin(page);
	const directory = aFolderWith(['holiday.mp4'], 'Kept');

	await page.goto('/settings/library');
	await addFolder(page, directory);
	await expect(rootRow(page, 'Kept')).toBeVisible();

	await rowAction(page, 'Kept', 'Remove');

	const dialog = page.getByRole('alertdialog');
	await expect(dialog).toBeVisible();
	await expect(dialog).toContainText('Remove Kept from Sift?');

	// Focus is on the dialog or inside it, not left behind on the page.
	await expect
		.poll(() =>
			page.evaluate(() => {
				const sheet = document.querySelector('[role="alertdialog"]');
				return !!sheet && sheet.contains(document.activeElement);
			})
		)
		.toBe(true);

	// Escape declines.
	await page.keyboard.press('Escape');
	await expect(dialog).toBeHidden();
	await expect(rootRow(page, 'Kept')).toBeVisible();

	// And so does a click beside it.
	//
	// Waited for first, and that is not politeness: the layer watching for a click outside is armed
	// after the dialog opens, so a click fired in the same millisecond lands before anything is
	// listening and is swallowed, and this test would pass while asserting the opposite.
	await rowAction(page, 'Kept', 'Remove');
	await expect(dialog).toBeVisible();
	await dialog.evaluate(async (el) => {
		await Promise.all(el.getAnimations({ subtree: true }).map((one) => one.finished));
		await new Promise(requestAnimationFrame);
	});

	await page.mouse.click(4, 4);

	await expect(dialog).toBeHidden();
	// Dismissing is not an answer: the folder is still here.
	await expect(rootRow(page, 'Kept')).toBeVisible();
});

test('removing a folder asks first: Cancel keeps it, Remove takes it', async ({ page }) => {
	await signInAsAdmin(page);
	const directory = aFolderWith(['holiday.mp4'], 'Fragile');

	await page.goto('/settings/library');
	await addFolder(page, directory);
	await expect(rootRow(page, 'Fragile')).toBeVisible();

	// Ask, then back out. Nothing happens.
	await rowAction(page, 'Fragile', 'Remove');
	await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel' }).click();
	await expect(page.getByRole('alertdialog')).toBeHidden();
	await expect(rootRow(page, 'Fragile')).toBeVisible();

	// Ask again and mean it. Now it is gone, and the app says so.
	await rowAction(page, 'Fragile', 'Remove');
	await page.getByRole('alertdialog').getByRole('button', { name: 'Remove' }).click();
	await expect(page.getByText(/Sift has forgotten Fragile/)).toBeVisible();
	await expect(rootRow(page, 'Fragile')).toBeHidden();
});

test('a library folder opens out to show the folders inside it', async ({ page }) => {
	// A library folder is a row here and the folders inside it are one press away, on the row they
	// belong to, rather than named a second time in a separate tree.
	//
	// The rows are not draggable and there is nothing to drop on: moving files is done in the grid,
	// where the files are, and `sheets.spec.ts` is what drives it.
	await signInAsAdmin(page);
	const directory = aFolderWith(['left/a.mp4', 'right/b.mp4'], 'Shelf');

	await page.goto('/settings/library');
	await addFolder(page, directory);
	await expect(rootRow(page, 'Shelf')).toBeVisible();

	// Adding does not scan, and it is the scan that turns the two subdirectories into folders at all.
	// Ask for it and wait for both, then reload: the screen does not poll.
	await rowAction(page, 'Shelf', 'Scan now');
	await expect
		.poll(
			async () => {
				const { folders } = await (await page.request.get('/api/library/folders')).json();
				return folders.map((folder: { name: string }) => folder.name);
			},
			{ timeout: 30_000 }
		)
		.toEqual(expect.arrayContaining(['left', 'right']));

	await page.goto('/settings/library');

	/* Named by their own TEXT rather than by the row's accessible name.

	   !! `{ name: 'left', exact: true }` matches nothing, and reads as the tree never opening.
	   Each row carries an Actions button, and a button's label is part of
	   the name computed for the element that CONTAINS it, so the row is called
	   "left Actions for left", and an exact match on "left" finds nothing while a loose one
	   finds it. The row's accessible name changes whenever a verb is added to the row; the folder's
	   name is what this test is about. */
	const folderRow = (name: string) =>
		page.getByRole('treeitem').filter({ has: page.getByText(name, { exact: true }) });

	// Closed to begin with, which is the point of it: the list is about the folders somebody handed
	// over, and what is inside one is asked for rather than always on screen.
	await expect(folderRow('left')).toHaveCount(0);

	await rootRow(page, 'Shelf')
		.getByRole('button', { name: /what is inside Shelf/ })
		.click();

	await expect(folderRow('left')).toBeVisible();
	await expect(folderRow('right')).toBeVisible();
});
