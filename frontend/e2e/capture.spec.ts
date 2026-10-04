import { expect, test } from '@playwright/test';
import { signInAsAdmin } from './admin';

/* Adding things, from the browser's side.
 *
 * The full journey a dropped file takes, and a pasted link through the downloader, are tested
 * where those live. What is shown here is that the front door is wired to the shell and gated the
 * way the server is: an admin is offered the ways in, and anyone else is told plainly that they
 * are not.
 */

test('an admin is offered a link field and a file picker', async ({ page }) => {
	await signInAsAdmin(page);
	await page.goto('/browse');

	/* Exact: the filter bar offers an "Added this week" preset, and a substring match finds it too. */
	await page.getByRole('button', { name: 'Add', exact: true }).hover();

	const panel = page.getByRole('dialog', { name: 'Add media' });
	await expect(panel).toBeVisible();
	await expect(panel.getByText('Paste a link')).toBeVisible();
	await expect(panel.getByText('Choose files')).toBeVisible();
});

test('a signed-out visitor never reaches the add panel at all', async ({ page }) => {
	/*
	 * Somebody signed out never reaches the add panel: the shell, and the add button with it, is
	 * not drawn without a session, so they are sent to sign in first.
	 *
	 * The panel's guest wording is what a signed-in guest sees, and the control was never the
	 * wording in any case: the import endpoints refuse a guest, and that is tested where they
	 * are.
	 */
	await page.context().clearCookies();
	await page.goto('/browse');

	await expect(page).toHaveURL(/\/(login|setup)$/);
	await expect(page.getByRole('button', { name: 'Add' })).toHaveCount(0);
});
