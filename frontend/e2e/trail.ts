import type { Page } from '@playwright/test';

export async function pressCrumb(page: Page, label: string): Promise<void> {
	const trail = page.getByRole('navigation', { name: 'Breadcrumb' });
	const standing = trail.getByRole('link', { name: label, exact: true });
	if ((await standing.count()) > 0) {
		await standing.click();
		return;
	}
	await trail.getByRole('button', { name: 'Show the folded steps' }).click();
	await page.getByRole('menuitem', { name: label, exact: true }).click();
}

/* The trail's words, read from its folded press where the bar has folded it to fit. */
export async function trailWords(page: Page): Promise<string> {
	const trail = page.getByRole('navigation', { name: 'Breadcrumb' });
	const fold = trail.getByRole('button', { name: 'Show the folded steps' });
	if ((await fold.count()) === 0) return (await trail.textContent()) ?? '';
	await fold.click();
	const words = await page.getByRole('menuitem').allTextContents();
	await page.keyboard.press('Escape');
	return `${(await trail.textContent()) ?? ''} ${words.join(' ')}`;
}
