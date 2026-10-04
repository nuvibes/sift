import { expect, test, type Page } from '@playwright/test';
import { ADMIN, PASSWORD, signInAsAdmin } from './admin';

/* The vault, in a real browser, against the real server.
 *
 * What is worth testing here is only what nothing else can see. The claim that a hidden thing is
 * absent from the grid, the counts and the media routes is checked at the API, on the server, for
 * the signed-in admin: that is where it has to be checked, because a screen that merely does
 * not draw a row it was sent has hidden nothing. Repeating it here would be testing the same
 * thing through a worse instrument.
 *
 * So this covers the parts that only exist once a browser is involved:
 *
 *   - the control is there, it says which state it is in, and a guest never sees it;
 *   - opening asks for the PIN, every time, including when the vault is already open;
 *   - a wrong PIN is refused and leaves the vault shut;
 *   - shutting it takes one press and no proof;
 *   - the screen re-asks the server after the vault opens, which is the only way what was hidden
 *     can appear: it was never in the page to reveal;
 *   - and a cold session needs the password, not the PIN.
 *
 * The grid itself is intercepted rather than seeded from real files. What the interception is for
 * is watching *when* the app asks for the grid again; what comes back does not matter, and
 * putting real files under the shared media area would collide with the spec that owns that
 * directory.
 */

const PIN = '531642';

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

/** The PIN, set the way the settings screen sets it: with the password, through the real endpoint. */
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

/** How many times the app has asked the server for the grid. */
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

/* One at a time, for this file only.
 *
 * Every test here signs in as the one shared admin and then changes SERVER-SIDE state that
 * account holds: whether the vault is open, and how many wrong PINs it has just been given. Run
 * in parallel they interleave on both, and the way it surfaces is a later test being unable to
 * sign in at all, which reads as the login being broken rather than as this file racing itself.
 */
test.describe.configure({ mode: 'serial' });

const vaultButton = (page: Page) => page.getByRole('button', { name: /hidden items/i });

/** Open the vault the way a person does: press the control, type the PIN, press Show. */
async function openWithPin(page: Page): Promise<void> {
	await vaultButton(page).click();
	const prompt = page.getByRole('alertdialog');
	await prompt.getByLabel('PIN').fill(PIN);
	await prompt.getByRole('button', { name: 'Show' }).click();
	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'true');
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	// Nothing here is about first run, and the dialog is modal, so without this every press below
	// lands on it, and whether it is there depends on what another spec file did first.
	await givePin(page);
	// Every test starts shut. The server keeps the unlocked state per session and this file shares
	// a server with the others, so leaving one open would leak into whatever ran next.
	await page.request.post('/api/vault/lock', {
		headers: { 'x-csrf-token': await csrfToken(page) }
	});
});

test('the control says which state the vault is in', async ({ page }) => {
	await page.goto('/browse');

	// Not a decoration: this is the thing somebody glances at to know whether hidden items are on
	// screen right now, and a control that looked the same either way would be worse than none.
	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'false');
});

test('opening the prompt puts the caret straight in the PIN field', async ({ page }) => {
	/* Bits UI moves focus to the dialog's own content root the moment it opens (that is what
	 * makes Escape and Tab work immediately), and that runs after a plain `autofocus` attribute
	 * on the input, so the attribute alone never wins and the box would have to be clicked into
	 * before anything could be typed. `onOpenAutoFocus` on the dialog content redirects that
	 * first focus to the field itself.
	 */
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
	// The copy has to say this or the app reads as broken after a restart: the PIN opens a session
	// that is already signed in, and nothing else.
	await expect(prompt).toContainText('hides itself when Sift restarts');

	await prompt.getByLabel('PIN').fill('000000');
	await prompt.getByRole('button', { name: 'Show' }).click();

	await expect(prompt.getByRole('alert')).toContainText('Incorrect PIN.');
	await expect(prompt).toBeVisible();
});

test('a wrong PIN on the Hidden page leaves the prompt standing too', async ({ page }) => {
	/*
	 * The same prompt, opened from the OTHER control: the one on the Hidden screen rather than
	 * the one in the top bar. The top-bar path above proves a wrong PIN does not make it vanish;
	 * this is the other door.
	 *
	 * Worth a test of its own rather than trusting the component: the component keeps itself open,
	 * and what is in question is whether something around it takes it away.
	 */
	await page.goto('/hidden');

	/* The locked page is its title over one sentence and one way in, the lock. */
	await page.getByRole('main').getByRole('button', { name: 'Unlock', exact: true }).click();

	const prompt = page.getByRole('alertdialog');
	await expect(prompt).toBeVisible();

	await prompt.getByLabel('PIN').fill('000000');
	await prompt.getByRole('button', { name: 'Show' }).click();

	await expect(prompt.getByRole('alert')).toContainText('Incorrect PIN.');
	await expect(prompt, 'the prompt went away instead of saying the PIN was wrong').toBeVisible();

	// And it is still usable rather than merely present: a dialog left on screen with a disabled
	// box would look identical in a screenshot and be just as broken.
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

	// The part that matters. What was concealed was never sent, so the only way it can appear is a
	// fresh request. An unlock that only changed a flag in the browser would reveal nothing.
	await expect.poll(() => grid.get()).toBeGreaterThan(before);
});

test('an already-open vault still asks for the PIN to open again', async ({ page }) => {
	// The deliberate-reveal gate. It looks redundant from the outside, which is exactly why it is
	// pinned here: somebody tidying up would remove it as a double prompt.
	await page.goto('/browse');
	await openWithPin(page);

	// Shut it and open it again through the control. The second opening costs the PIN just as the
	// first did: there is no path through this app that opens the vault without one.
	await vaultButton(page).click();
	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'false');

	await vaultButton(page).click();
	await expect(page.getByRole('alertdialog')).toBeVisible();
});

test('opening Sift starts locked, whatever the last session left open', async ({ page }) => {
	// A lock trigger, and the default one. The tab is reopened rather than the server restarted:
	// a restart locks everything by itself, since the server keeps the unlock in memory and nowhere
	// else, so it would prove nothing about the setting.
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

	// No dialog, no confirmation. This is also the panic button, and anything between pressing it
	// and it happening is time the screen is still showing what somebody wanted gone.
	await expect(page.getByRole('alertdialog')).toBeHidden();
	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'false');
	await expect.poll(() => grid.get()).toBeGreaterThan(before);
});

test('a cold session needs the password: the PIN cannot bring one back', async ({ page }) => {
	// Signed out, which is the state a restart leaves behind as far as this is concerned: there is
	// no session for a PIN to unlock. The PIN is short on purpose and is allowed to be short only
	// because it can never do this.
	await page.request.post('/api/auth/logout', {
		headers: { 'x-csrf-token': await csrfToken(page) }
	});
	await page.context().clearCookies();

	const refused = await page.request.post('/api/vault/unlock', { data: { pin: PIN } });
	expect(refused.status(), 'a PIN opened a session that had none').toBe(403);

	await page.goto('/browse');
	await expect(page).toHaveURL(/\/login$/);

	// And the password does bring it back.
	await page.getByLabel('Username').fill(ADMIN);
	await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page).toHaveURL(/\/browse/);
	// Signed back in, and the vault is shut: signing in is not unlocking it.
	await expect(vaultButton(page)).toHaveAttribute('aria-pressed', 'false');
});

test('the settings screen offers the PIN and says what it is for', async ({ page }) => {
	await page.goto('/settings/privacy');

	await expect(page.getByRole('heading', { name: 'Privacy' })).toBeVisible();
	/* The one claim this screen must never overstate. It hides; it does not encrypt. So this
	   matches the CLAIM rather than one wording of it: whatever the sentence is, it has to say
	   the files are not encrypted. */
	await expect(page.getByText(/(not|never|n't) encrypted/i).first()).toBeVisible();
	/* The PIN is on Profile, not Privacy: this screen keeps the claim about what hiding does and
	   Profile keeps the credential. Privacy is about the FEATURE and Profile is about YOU;
	   `settings-ui/Profile.svelte` carries the argument. */
	await page.goto('/settings/profile');
	/* EXACT: the form below this heading is a `FormCard` now, and its own title is "Change your
	   PIN", which contains this one. */
	await expect(page.getByRole('heading', { name: 'PIN', exact: true })).toBeVisible();
	await expect(page.getByText(/A PIN is set/)).toBeVisible();
});

test('hiding a file with the vault OPEN leaves it on the grid, marked', async ({ page }) => {
	/* Hiding needs a PIN to EXIST, not the vault to be open, so it can be done either way, and
	 * the two have opposite answers. Shut, the file is gone from this screen. Open, it is still
	 * perfectly visible and only gains a mark: a grid that took the tile away regardless would
	 * make a file vanish from a screen that was showing hidden files a second earlier, until a
	 * reload.
	 */
	const asset = { id: 'v1', media_type: 'video', width: 800, height: 600, thumb: true };
	let hiddenNow = false;
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			// The server keeps sending it while the vault is open; what changes is the mark on it.
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

	// Still there, and now saying so.
	/* `.mark.vaulted`: a mark is a badge, not a chip. */
	await expect(page.locator('.mark.vaulted')).toHaveCount(1);
	await expect(
		page.locator('.tile'),
		'the file vanished from a screen that can see it'
	).toHaveCount(1);
});
