import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* A guest from creation to being shown something: the screens wired to each other.
 * Serial, one guest: each account costs a deliberately slow, capped password hash. */

test.describe.configure({ mode: 'serial' });

const GUEST_PASSWORD = 'A-Guest-Passphrase-9';

// Per run, not fixed: a fixed name would carry state from an earlier run.
const GUEST = `e2e-guest-${Date.now()}`;

let made = false;

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

function guestRows(page: Page) {
	return page.getByRole('list', { name: 'Guests' }).getByRole('listitem');
}

async function openUsers(page: Page) {
	await page.goto('/settings/users');
	await expect(page.getByRole('heading', { name: 'User Management', level: 1 })).toBeVisible();
}

/** Add this file's one guest through the screen, the first time it is asked for. */
async function theGuest(page: Page) {
	await openUsers(page);
	if (!made) {
		await page.getByLabel('Username').fill(GUEST);
		await page.getByLabel('First password', { exact: true }).fill(GUEST_PASSWORD);
		await page.getByLabel('First password again').fill(GUEST_PASSWORD);
		await page.getByRole('button', { name: 'Create guest' }).click();
		made = true;
	}
	await expect(guestRows(page).filter({ hasText: GUEST })).toHaveCount(1);
}

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

/** Opens the Sharing panel from an entity page's Options menu. */
async function openSharing(page: Page): Promise<void> {
	await page.getByRole('button', { name: 'Options', exact: true }).click();
	await page.getByRole('menuitem', { name: 'Sharing' }).click();
}

test('Users is a settings section, and adding a guest puts them in the list', async ({ page }) => {
	await theGuest(page);

	await expect(guestRows(page).filter({ hasText: GUEST }).getByText('Can sign in')).toBeVisible();
});

test('the screen says a guest sees nothing until something is shared', async ({ page }) => {
	// Default-deny: adding an account grants access to nothing.
	await openUsers(page);

	await expect(page.getByText(/see nothing at all until you share/i)).toBeVisible();
});

test('blocking a guest changes what the row says, and can be lifted from the same row', async ({
	page
}) => {
	await theGuest(page);
	const row = guestRows(page).filter({ hasText: GUEST });

	await row.getByRole('button', { name: `More for ${GUEST}` }).click();
	await page.getByRole('menuitem', { name: 'Turn off sign-in' }).click();
	await expect(row.getByText('Sign-in turned off')).toBeVisible();

	// And back: a block that cannot be lifted from the same row is a trap.
	await row.getByRole('button', { name: `More for ${GUEST}` }).click();
	await page.getByRole('menuitem', { name: 'Turn on sign-in' }).click();
	await expect(row.getByText('Can sign in')).toBeVisible();
});

test('the sharing panel stages a decision and writes it on Apply', async ({ page }) => {
	/* Nothing is written until Apply, and Shared and Restricted exclude each other: holding both
	 * would leave a share that does nothing until the restrict lifts. */
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
	await expect(panel.getByRole('button', { name: /^Apply \(1\)/ })).toBeVisible();

	await row.getByRole('button', { name: 'Restrict', exact: true }).click();
	await expect(row.locator('.standing')).toContainText('Restricted');

	await panel.getByRole('button', { name: /^Apply/ }).click();
	await expect(panel).toBeHidden();

	// And it is what the server now holds.
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
	// Profile is a Settings section, reached through the rail; Sign out is on that page.
	await page.goto('/browse');

	await expect(page.locator('.session-trigger')).toHaveCount(0);

	await page.getByRole('link', { name: 'Settings' }).click();
	await page.getByRole('button', { name: 'Profile' }).click();
	await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
	await expect(page.getByText('e2e-admin', { exact: true })).toBeVisible();
});

test('Ctrl+H shuts the vault from wherever somebody is standing, and says so', async ({ page }) => {
	/* Ctrl+H hides; Ctrl+L locks Sift. Watched as the request, since the Hidden control reads the
	 * same while shut. */
	const locks: string[] = [];
	page.on('request', (request) => {
		if (request.method() === 'POST' && request.url().includes('/api/vault/lock')) {
			locks.push(request.url());
		}
	});

	await page.goto('/browse');
	// Either name: the control says what pressing it does.
	await expect(page.getByRole('button', { name: /hidden items/i })).toBeVisible();
	const before = locks.length;

	await page.keyboard.press('Control+h');

	await expect.poll(() => locks.length).toBeGreaterThan(before);
	await expect(page.getByText(/Hidden items are hidden/)).toBeVisible();
});

test('and Ctrl+L locks Sift itself, which is the bigger of the two', async ({ page }) => {
	// Locking keeps the session and asks for the password again.
	await page.goto('/browse');
	await expect(page.getByRole('heading', { name: 'Browse' })).toBeVisible();

	await page.keyboard.press('Control+l');

	await expect(page).toHaveURL(/\/locked/);
	await expect(page.getByLabel('Password', { exact: true })).toBeVisible();
});

test('a guest is shown no Add menu, and Playback is theirs to set', async ({ page, browser }) => {
	// A guest gets no Add, whose every route refuses them, but does get Playback.
	await theGuest(page);

	const theirs = await browser.newContext();
	const guestPage = await theirs.newPage();
	try {
		await guestPage.goto('/login');
		await guestPage.getByLabel('Username').fill(GUEST);
		await guestPage.getByLabel('Password', { exact: true }).fill(GUEST_PASSWORD);
		await guestPage.getByRole('button', { name: 'Sign in' }).click();
		await expect(guestPage).toHaveURL(/\/browse/);

		// Exact: `/^Add/` also matches the "Added this week" preset.
		await expect(guestPage.getByRole('button', { name: 'Add', exact: true })).toHaveCount(0);

		await guestPage.goto('/settings/playback');
		await expect(guestPage.getByText('Remember where you left off')).toBeVisible();
		await expect(guestPage.getByText('Largest size for converted videos')).toHaveCount(0);
	} finally {
		await theirs.close();
	}
});

test('a mark on an entity wall opens the sharing panel it says it will', async ({ page }) => {
	// Every mark is a button that opens the sharing panel; a person's card stands for all four.
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

	// Narrowed to this person: the wall is paged and ordered by use.
	await page.goto('/people');
	await page.getByRole('searchbox', { name: 'Search people' }).fill(name);
	const card = page.locator('.card').filter({ hasText: name }).first();
	const mark = card.getByRole('button', { name: /Open sharing/ });
	await expect(mark, 'the wall draws no mark for a person who has just been shared').toHaveCount(1);

	await mark.click();

	await expect(panel, 'the mark says click to see the sharing menu and does nothing').toBeVisible();
	await expect(panel.locator('.consequence')).toContainText(name);
});

test('and both shortcuts mean the same two sizes of thing to a guest', async ({
	page,
	browser
}) => {
	// A guest has their own Hidden and can shut it, and lock Sift, by key.
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

		await asGuest.keyboard.press('Control+h');
		await expect.poll(() => locks.length).toBeGreaterThan(0);
		await expect(asGuest.getByText(/Hidden items are hidden/)).toBeVisible();
		await expect(asGuest).toHaveURL(/\/browse/);

		await asGuest.keyboard.press('Control+l');
		await expect(asGuest).toHaveURL(/\/login|\/locked|\/$/);
	} finally {
		await guest.close();
	}
});
