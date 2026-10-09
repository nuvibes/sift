import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { ADMIN, PASSWORD, signInAsAdmin } from './admin';

/* The vault in a browser: only what the API tests cannot see. Hiding itself is checked at the
 * server; here, the control, the PIN prompt, and the refetch after opening. The grid is
 * intercepted to watch when it is asked for again. */

const PIN = '531642';

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

/** The PIN, set with the password through the real endpoint. */
async function givePin(page: Page): Promise<void> {
	const response = await page.request.put('/api/auth/pin', {
		data: { pin: PIN, current_password: PASSWORD },
		headers: { 'x-csrf-token': await csrfToken(page) }
	});
	expect(response.ok(), 'could not set the PIN').toBeTruthy();
}

async function csrfToken(page: Page): Promise<string> {
	const me = await page.request.get('/api/auth/me');
	return (await me.json()).csrf_token as string;
}

function countGridRequests(page: Page): { get: () => number } {
	let seen = 0;
	void page.route('**/api/assets?*', async (route) => {
		seen += 1;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })
		});
	});
	return { get: () => seen };
}

// Serial: every test changes the shared admin's vault state and wrong-PIN count.
test.describe.configure({ mode: 'serial' });

const vaultButton = (page: Page) => page.getByRole('button', { name: /hidden items/i });

/** Open the vault as a person does: the control, the PIN, Show. */
async function openWithPin(page: Page): Promise<void> {
	await vaultButton(page).click();
	const prompt = page.getByRole('alertdialog');
	await prompt.getByLabel('PIN').fill(PIN);
	await prompt.getByRole('button', { name: 'Show' }).click();
	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'true');
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	// The first-run dialog is modal and depends on other files' order.
	await givePin(page);
	// Every test starts shut: the unlock is per session on a shared server.
	await page.request.post('/api/vault/lock', {
		headers: { 'x-csrf-token': await csrfToken(page) }
	});
});

test('the control says which state the vault is in', async ({ page }) => {
	await page.goto('/browse');

	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'false');
});

test('opening the prompt puts the caret straight in the PIN field', async ({ page }) => {
	// `onOpenAutoFocus` puts first focus in the field; a plain `autofocus` loses to the dialog.
	await page.goto('/browse');

	await vaultButton(page).click();
	const prompt = page.getByRole('alertdialog');
	await expect(prompt).toBeVisible();

	await expect(prompt.getByLabel('PIN')).toBeFocused();
});

test('showing hidden items asks for the PIN, and a wrong one is refused', async ({ page }) => {
	await page.goto('/browse');

	await vaultButton(page).click();

	const prompt = page.getByRole('alertdialog');
	await expect(prompt).toBeVisible();
	await expect(prompt).toContainText('Enter your PIN');
	// Without this the app reads as broken after a restart.
	await expect(prompt).toContainText('hides itself when Sift restarts');

	await prompt.getByLabel('PIN').fill('000000');
	await prompt.getByRole('button', { name: 'Show' }).click();

	await expect(prompt.getByRole('alert')).toContainText('Incorrect PIN.');
	await expect(prompt).toBeVisible();
});

test('a wrong PIN on the Hidden page leaves the prompt standing too', async ({ page }) => {
	// The same prompt from the Hidden screen's control, the other door.
	await page.goto('/hidden');

	await page.getByRole('main').getByRole('button', { name: 'Unlock', exact: true }).click();

	const prompt = page.getByRole('alertdialog');
	await expect(prompt).toBeVisible();

	await prompt.getByLabel('PIN').fill('000000');
	await prompt.getByRole('button', { name: 'Show' }).click();

	await expect(prompt.getByRole('alert')).toContainText('Incorrect PIN.');
	await expect(prompt, 'the prompt went away instead of saying the PIN was wrong').toBeVisible();

	// Still usable, not merely present.
	await prompt.getByLabel('PIN').fill(PIN);
	await prompt.getByRole('button', { name: 'Show' }).click();
	await expect(prompt).toBeHidden();
});

test('the right PIN opens the vault and the screen asks the server again', async ({ page }) => {
	const grid = countGridRequests(page);
	await page.goto('/browse');
	await expect(vaultButton(page)).toBeVisible();
	const before = grid.get();

	await vaultButton(page).click();
	const prompt = page.getByRole('alertdialog');
	await prompt.getByLabel('PIN').fill(PIN);
	await prompt.getByRole('button', { name: 'Show' }).click();

	await expect(prompt).toBeHidden();
	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'true');

	// What was concealed was never sent, so only a fresh request can show it.
	await expect.poll(() => grid.get()).toBeGreaterThan(before);
});

test('an already-open vault still asks for the PIN to open again', async ({ page }) => {
	// The PIN every time, even when open: tidying this away as a double prompt would be wrong.
	await page.goto('/browse');
	await openWithPin(page);

	await vaultButton(page).click();
	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'false');

	await vaultButton(page).click();
	await expect(page.getByRole('alertdialog')).toBeVisible();
});

test('opening Sift starts locked, whatever the last session left open', async ({ page }) => {
	// The tab is reopened: a restart locks everything anyway, proving nothing about the setting.
	await page.request.post('/api/vault/unlock', {
		data: { pin: PIN },
		headers: { 'x-csrf-token': await csrfToken(page) }
	});

	await page.goto('/browse');

	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'false');
});

test('hiding them again takes one press and no proof', async ({ page }) => {
	const grid = countGridRequests(page);
	await page.goto('/browse');
	await vaultButton(page).click();
	const prompt = page.getByRole('alertdialog');
	await prompt.getByLabel('PIN').fill(PIN);
	await prompt.getByRole('button', { name: 'Show' }).click();
	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'true');
	const before = grid.get();

	await vaultButton(page).click();

	// No confirmation: this is also the panic button.
	await expect(page.getByRole('alertdialog')).toBeHidden();
	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'false');
	await expect.poll(() => grid.get()).toBeGreaterThan(before);
});

test('a cold session needs the password: the PIN cannot bring one back', async ({ page }) => {
	// Signed out, there is no session for the PIN, which is why the PIN may be short.
	await page.request.post('/api/auth/logout', {
		headers: { 'x-csrf-token': await csrfToken(page) }
	});
	await page.context().clearCookies();

	const refused = await page.request.post('/api/vault/unlock', { data: { pin: PIN } });
	expect(refused.status(), 'a PIN opened a session that had none').toBe(403);

	await page.goto('/browse');
	await expect(page).toHaveURL(/\/login$/);

	await page.getByLabel('Username').fill(ADMIN);
	await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page).toHaveURL(/\/browse/);
	// Signing in is not unlocking.
	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'false');
});

test('the settings screen offers the PIN and says what it is for', async ({ page }) => {
	await page.goto('/settings/privacy');

	await expect(page.getByRole('heading', { name: 'Privacy' })).toBeVisible();
	// The claim it must never overstate: it hides, it does not encrypt.
	await expect(page.getByText(/(not|never|n't) encrypted/i).first()).toBeVisible();
	await page.goto('/settings/profile');
	// Exact: the form's own title "Change your PIN" contains it.
	await expect(page.getByRole('heading', { name: 'PIN', exact: true })).toBeVisible();
	await expect(page.getByText(/A PIN is set/)).toBeVisible();
});

test('hiding a file with the vault OPEN leaves it on the grid, marked', async ({ page }) => {
	// Hidden while shut, the file leaves the screen; while open, it stays and gains a mark.
	const asset = { id: 'v1', media_type: 'video', width: 800, height: 600, thumb: true };
	let hiddenNow = false;
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				items: [{ ...asset, hidden: hiddenNow }],
				total: 1,
				limit: 50,
				offset: 0
			})
		})
	);
	await page.route('**/api/assets/vault', async (route) => {
		hiddenNow = true;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 1, skipped: 0, vault_locked: false })
		});
	});
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);

	await page.goto('/browse');
	await openWithPin(page);
	await expect(page.locator('.tile')).toHaveCount(1);

	await page.locator('.tile-frame').first().click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Hide' }).click();

	/* `.mark.vaulted`: a mark is a badge, not a chip. */
	await expect(page.locator('.mark.vaulted')).toHaveCount(1);
	await expect(
		page.locator('.tile'),
		'the file vanished from a screen that can see it'
	).toHaveCount(1);
});
