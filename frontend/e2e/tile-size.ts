import type { Locator, Page } from '@playwright/test';

/* The tile size slider, on the top bar at every width. */
export async function tileSizeSlider(page: Page): Promise<Locator> {
	const onBar = page.locator('header.topbar .size input[type="range"]');
	await onBar.waitFor();
	return onBar;
}
