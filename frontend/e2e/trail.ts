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
