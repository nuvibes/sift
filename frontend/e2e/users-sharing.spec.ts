import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * A guest coming into existence, and being shown something.
 *
 * The check worth having is the whole arc rather than any one screen: an admin adds a guest through
 * the interface, the guest turns up in the sharing panel, and the words the panel uses say what the
 * server would say. The resolution rules themselves are proved server-side; what a browser proves
 * that a request cannot is that the screens are wired to each other.
 *
 * **Serial, and one guest for the whole file.** Creating an account is a real Argon2 hash at the
 * install's tuned parameters, which is deliberately expensive and is capped in how many can run at
 * once. Four tests each making their own guest, in parallel, alongside every other spec file
 * signing in, queue behind that cap and time out, which reads exactly like a broken feature. One
 * account, made once, costs one hash.
 */

test.describe.configure({ mode: 'serial' });

const GUEST_PASSWORD = 'A-Guest-Passphrase-9';

/* Named once per run rather than per test, so the file's tests share the account they make. A fixed
 * name would be created by whichever run went first and then be in a state the next did not set. */
const GUEST = `e2e-guest-${Date.now()}`;

let made = false;

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

/** The guest rows on the Users screen, scoped so a name cannot also match an ancestor. */
function guestRows(page: Page) {
	return page.getByRole('list', { name: 'Guests' }).getByRole('listitem');
}

async function openUsers(page: Page) {
	await page.goto('/settings/users');
	await expect(page.getByRole('heading', { name: 'User Management', level: 1 })).toBeVisible();
}

/** Add the one guest this file shares, through the screen, the first time it is asked for. */
async function theGuest(page: Page) {
	await openUsers(page);
	if (!made) {
		await page.getByLabel('Username').fill(GUEST);
		// Twice, exactly as a person types it. An admin says this password out loud to somebody
		// else, so a typo here is an account whose password nobody in the room knows.
		await page.getByLabel('First password', { exact: true }).fill(GUEST_PASSWORD);
		await page.getByLabel('First password again').fill(GUEST_PASSWORD);
		await page.getByRole('button', { name: 'Create guest' }).click();
		made = true;
	}
	await expect(guestRows(page).filter({ hasText: GUEST })).toHaveCount(1);
}

/** The CSRF token this browser's session must echo on a state-changing request. */
async function csrf(page: Page): Promise<string> {
	const me = await page.request.get('/api/auth/me');
	return String((await me.json()).csrf_token);
}

async function aPerson(page: Page, name = `e2e-person-${Date.now()}`): Promise<string> {
	const created = await page.request.post('/api/people', {
		data: { name },
		headers: { 'x-csrf-token': await csrf(page) }
	});
	expect(created.ok(), 'could not create a person to share').toBeTruthy();
	return String((await created.json()).id);
}

/**
 * Open the Sharing panel from an entity page: two presses. Sharing is a row in the header's Options
 * menu, one door for the entity page's verbs. The door's accessible name is exactly its word: its
 * trailing chevron is `aria-hidden`.
 */
async function openSharing(page: Page): Promise<void> {
	await page.getByRole('button', { name: 'Options', exact: true }).click();
	await page.getByRole('menuitem', { name: 'Sharing' }).click();
}

test('Users is a settings section, and adding a guest puts them in the list', async ({ page }) => {
	await theGuest(page);

	// The state beside the name, which is what an admin reads to know whether somebody can get in.
	// A row with a name and no state would leave "did that work" unanswered.
	await expect(guestRows(page).filter({ hasText: GUEST }).getByText('Can sign in')).toBeVisible();
});

test('the screen says a guest sees nothing until something is shared', async ({ page }) => {
	/* Default-deny is the most surprising thing about this feature: adding an account grants access
	 * to nothing at all. An admin who does not know that has added a guest and believes the library
	 * is now readable by them. */
	await openUsers(page);

	await expect(page.getByText(/see nothing at all until you share/i)).toBeVisible();
});

test('blocking a guest changes what the row says, and can be lifted from the same row', async ({
	page
}) => {
	await theGuest(page);
	const row = guestRows(page).filter({ hasText: GUEST });

	// The row's four actions are behind one control, the same as a folder's, so each is opened for.
	await row.getByRole('button', { name: `More for ${GUEST}` }).click();
	await page.getByRole('menuitem', { name: 'Turn off sign-in' }).click();
	await expect(row.getByText('Sign-in turned off')).toBeVisible();

	// And back, because a block that cannot be lifted from the same row is a trap.
	await row.getByRole('button', { name: `More for ${GUEST}` }).click();
	await page.getByRole('menuitem', { name: 'Turn on sign-in' }).click();
	await expect(row.getByText('Can sign in')).toBeVisible();
});

test('the sharing panel stages a decision and writes it on Apply', async ({ page }) => {
	/* Two rules that cannot be got wrong on screen.
	 *
	 * The first is that nothing is written until Apply. A panel that posted as buttons were
	 * pressed would make a misread row a fact before anybody noticed, and could not be opened
	 * on a selection at all.
	 *
	 * The second is that Shared and Restricted are mutually exclusive, and the word that ends up
	 * recorded is the one that was showing when Apply was pressed. Holding both would be a trap:
	 * it resolves to Restricted, so the share underneath does nothing, looks like it does, and
	 * would come quietly into force the day the restrict was lifted.
	 *
	 * A person is the thing shared here because it needs no library root and no file on disk, and
	 * it is one of the kinds the panel has to cover.
	 */
	await theGuest(page);
	const name = `e2e-person-${Date.now()}`;
	const person = await aPerson(page, name);

	await page.goto(`/people/${person}`);
	await openSharing(page);

	const panel = page.locator('.share-sheet');
	await expect(panel).toBeVisible();
	const row = panel.locator('li').filter({ hasText: GUEST });
	await expect(row.locator('.standing')).toHaveText('Not shared');

	await row.getByRole('button', { name: 'Share', exact: true }).click();
	await expect(row.locator('.standing')).toHaveText('Shared');
	// ...and the count on Apply is what says it has not been written yet.
	await expect(panel.getByRole('button', { name: /^Apply \(1\)/ })).toBeVisible();

	// Straight across rather than adding a second row to the first.
	await row.getByRole('button', { name: 'Restrict', exact: true }).click();
	await expect(row.locator('.standing')).toContainText('Restricted');

	await panel.getByRole('button', { name: /^Apply/ }).click();
	await expect(panel).toBeHidden();

	// And it is what the server now holds, which is the only claim that matters.
	await page.reload();
	await openSharing(page);
	await expect(panel.locator('li').filter({ hasText: GUEST }).locator('.standing')).toHaveText(
		'Restricted'
	);
});

test('and Cancel throws the staged decision away', async ({ page }) => {
	await theGuest(page);
	const name = `e2e-person-${Date.now()}`;
	const person = await aPerson(page, name);

	await page.goto(`/people/${person}`);
	await openSharing(page);

	const panel = page.locator('.share-sheet');
	const row = panel.locator('li').filter({ hasText: GUEST });
	await row.getByRole('button', { name: 'Share', exact: true }).click();
	await panel.getByRole('button', { name: 'Cancel' }).click();
	await expect(panel).toBeHidden();

	await openSharing(page);
	await expect(panel.locator('li').filter({ hasText: GUEST }).locator('.standing')).toHaveText(
		'Not shared'
	);
});

test('the account is a settings section, and signing out is on the page it goes to', async ({
	page
}) => {
	/* One account control per screen: the sidebar row. Profile is a section of Settings (beside
	 * Appearance and Privacy, the other two things that are yours rather than the library's).
	 * Sign out is on the page it opens, and Lock is Ctrl+L and the Hidden control on the bar.
	 * Reached the way a person reaches it, through the rail, so this walks the route rather than
	 * an address.
	 */
	await page.goto('/browse');

	await expect(page.locator('.session-trigger')).toHaveCount(0);

	await page.getByRole('link', { name: 'Settings' }).click();
	await page.getByRole('button', { name: 'Profile' }).click();
	await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
	await expect(page.getByText('e2e-admin', { exact: true })).toBeVisible();
});

test('Ctrl+H shuts the vault from wherever somebody is standing, and says so', async ({ page }) => {
	/*
	 * The panic key. It is the one control that has to work while nobody is looking at the app,
	 * which is the situation it exists for, so it is a keystroke rather than something to find.
	 *
	 * H rather than L. There are two locks and they are not the same size: this one HIDES, and
	 * Ctrl+L locks Sift itself by ending the session. Each has its own key.
	 *
	 * What is watched is the request, not the control beside it: the Hidden button reads the same
	 * while the vault is shut, so asserting on it would pass without the keystroke doing anything
	 * at all. The lock going to the server is the thing that happened; the toast is how a person
	 * knows it did.
	 */
	const locks: string[] = [];
	page.on('request', (request) => {
		if (request.method() === 'POST' && request.url().includes('/api/vault/lock')) {
			locks.push(request.url());
		}
	});

	await page.goto('/browse');
	/* Only a wait for the shell to be drawn before the keystroke: the assertion that matters
	   is the request below. The name matches both states on purpose: the control says "Show
	   hidden items" while the vault is shut and "Hide hidden items" while it is open (every
	   control says what pressing it DOES), and this does not care which. */
	await expect(page.getByRole('button', { name: /hidden items/i })).toBeVisible();
	const before = locks.length;

	await page.keyboard.press('Control+h');

	await expect.poll(() => locks.length).toBeGreaterThan(before);
	await expect(page.getByText(/Hidden items are hidden/)).toBeVisible();
});

test('and Ctrl+L locks Sift itself, which is the bigger of the two', async ({ page }) => {
	/* The lock screen, and the password is what opens it again.
	 *
	 * Locking keeps the session and draws the lock screen over it: signing out would throw away
	 * saved logins and make "lock" and "sign out" the same button, and what somebody wants at the
	 * end of the evening is to be asked for their password, not to be turned out of the
	 * application.
	 *
	 * That is only safe because every route refuses while it is locked, and because the shell
	 * sends any address straight here rather than drawing the library first. Both have tests of
	 * their own; this is the keystroke.
	 */
	await page.goto('/browse');
	await expect(page.getByRole('heading', { name: 'Browse' })).toBeVisible();

	await page.keyboard.press('Control+l');

	await expect(page).toHaveURL(/\/locked/);
	await expect(page.getByLabel('Password', { exact: true })).toBeVisible();
});

test('a guest is shown no Add menu, and Playback is theirs to set', async ({ page, browser }) => {
	/*
	 * The two halves of "do not offer a guest a control that is not theirs, and do not withhold one
	 * that is".
	 *
	 * Add is admin-only because every item behind it (import a file, add a folder, paste a link)
	 * is a route the server refuses them; a menu whose every item fails reads as broken rather
	 * than as not-yours. Playback is not admin-only: it holds where a video starts, which is a
	 * preference about how one person watches.
	 *
	 * In a context of its own, because the guest signs in and the rest of this file signs in as an
	 * admin.
	 */
	await theGuest(page);

	const theirs = await browser.newContext();
	const guestPage = await theirs.newPage();
	try {
		await guestPage.goto('/login');
		await guestPage.getByLabel('Username').fill(GUEST);
		await guestPage.getByLabel('Password', { exact: true }).fill(GUEST_PASSWORD);
		await guestPage.getByRole('button', { name: 'Sign in' }).click();
		await expect(guestPage).toHaveURL(/\/browse/);

		/* The Add MENU, named exactly. `/^Add/` also matches the filter bar's "Added this week"
		   preset, which a guest is offered and should be, since it is a way of looking rather
		   than a way of writing. */
		await expect(guestPage.getByRole('button', { name: 'Add', exact: true })).toHaveCount(0);

		/* Both labels read off the registry: "Remember where you left off", and the app-scoped
		   control on this pane, "Largest size for converted videos". A label that matches
		   nothing anywhere is the shape of a check that cannot fail. */
		await guestPage.goto('/settings/playback');
		await expect(guestPage.getByText('Remember where you left off')).toBeVisible();
		// And the install's control is still not theirs.
		await expect(guestPage.getByText('Largest size for converted videos')).toHaveCount(0);
	} finally {
		await theirs.close();
	}
});

test('a mark on an entity wall opens the sharing panel it says it will', async ({ page }) => {
	/* Every mark's tooltip ends "(click to see sharing menu)", so every mark is a button: on a
	 * person's card, a site's card, a tag chip and a folder row as well as on tiles. A plain span
	 * there would say click and do nothing, with the mark drawn and the words right.
	 *
	 * Done through the browser rather than as a component test because the wiring between four
	 * screens and one panel is exactly what a component test of either half cannot see. The
	 * person's wall stands for all four: they share the component and the callback, and a mark is
	 * only drawn where something has been said, so the share has to be made first.
	 */
	await theGuest(page);
	const name = `e2e-person-${Date.now()}`;
	const person = await aPerson(page, name);

	await page.goto(`/people/${person}`);
	await openSharing(page);
	const panel = page.locator('.share-sheet');
	await panel
		.locator('li')
		.filter({ hasText: GUEST })
		.getByRole('button', { name: 'Share', exact: true })
		.click();
	await panel.getByRole('button', { name: /^Apply/ }).click();
	await expect(panel).toBeHidden();

	/* Narrowed to THIS person. The wall is paged to the screen and ordered by use, so among
	   everything the suite has made, a person with no files sits on a page nobody is looking at,
	   and "the first e2e person on screen" was some other test's, with nothing shared. */
	await page.goto('/people');
	await page.getByRole('searchbox', { name: 'Search people' }).fill(name);
	const card = page.locator('.card').filter({ hasText: name }).first();
	const mark = card.getByRole('button', { name: /Open sharing/ });
	await expect(mark, 'the wall draws no mark for a person who has just been shared').toHaveCount(1);

	await mark.click();

	await expect(panel, 'the mark says click to see the sharing menu and does nothing').toBeVisible();
	// On the right thing, rather than on whatever the wall had first: the sheet's sentence under its
	// title names what is being shared.
	await expect(panel.locator('.consequence')).toContainText(name);
});

test('and both shortcuts mean the same two sizes of thing to a guest', async ({
	page,
	browser
}) => {
	/*
	 * A guest can shut their own Hidden by hand.
	 *
	 * Hiding is personal, so a guest has their own Hidden, and a guest who could open it and had
	 * no way to shut it again would be worse off than without it. Both shortcuts are the shell's;
	 * the lock triggers behind them are mounted for everybody rather than for an admin.
	 *
	 * Its own browser context, because the rest of this file signs in as an admin and the assertion
	 * is about which account is at the keyboard.
	 */
	await theGuest(page);

	const guest = await browser.newContext();
	const asGuest = await guest.newPage();
	try {
		await asGuest.goto('/login');
		await asGuest.getByLabel('Username').fill(GUEST);
		await asGuest.getByLabel('Password', { exact: true }).fill(GUEST_PASSWORD);
		await asGuest.getByRole('button', { name: 'Sign in' }).click();
		await expect(asGuest).toHaveURL(/\/browse/);

		const locks: string[] = [];
		asGuest.on('request', (request) => {
			if (request.method() === 'POST' && request.url().includes('/api/vault/lock')) {
				locks.push(request.url());
			}
		});

		// The smaller one: Hidden shuts and the library stays open.
		await asGuest.keyboard.press('Control+h');
		await expect.poll(() => locks.length).toBeGreaterThan(0);
		await expect(asGuest.getByText(/Hidden items are hidden/)).toBeVisible();
		await expect(asGuest).toHaveURL(/\/browse/);

		// The bigger one: the whole session shuts and something has to be entered to come back.
		await asGuest.keyboard.press('Control+l');
		await expect(asGuest).toHaveURL(/\/login|\/locked|\/$/);
	} finally {
		await guest.close();
	}
});
