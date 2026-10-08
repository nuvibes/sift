/*
 * The Filter panel's column chooser opens without the page growing a scroll bar for a frame: the
 * list hangs below its trigger near the window's foot, and a first frame drawn past the foot shakes
 * the whole screen.
 */
import { signInAsAdmin } from './admin';
import { expect, test } from './test';

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1440, height: 900 });
});

test('opening a column chooser never draws the page past the window for a frame', async ({
	page
}) => {
	await page.goto('/browse');
	await page.getByRole('button', { name: 'Filter', exact: true }).first().click();
	const chooser = page.locator('button[aria-label="What this column shows"]').first();
	await expect(chooser).toBeVisible();

	// Record the document's height on every frame from the press until the list has settled.
	await page.evaluate(() => {
		const seen: number[] = [];
		(window as unknown as { __frames: number[] }).__frames = seen;
		const tick = () => {
			const root = document.documentElement;
			seen.push(root.scrollHeight - root.clientHeight);
			if (seen.length < 40) requestAnimationFrame(tick);
		};
		requestAnimationFrame(tick);
	});
	await chooser.click();
	await expect(page.getByRole('listbox')).toBeVisible();
	await expect
		.poll(() => page.evaluate(() => (window as unknown as { __frames: number[] }).__frames.length))
		.toBeGreaterThanOrEqual(40);

	const over = await page.evaluate(() =>
		(window as unknown as { __frames: number[] }).__frames.filter((extra) => extra > 0)
	);
	expect(over, 'frames on which the page was taller than the window').toEqual([]);
});
