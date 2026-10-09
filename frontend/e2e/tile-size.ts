import type { Locator, Page } from '@playwright/test';

/* The tile size slider wherever the bar keeps it: on the top bar, or, once the bar has run out of
   room, behind the Tile size press on the screen's own row. */
export async function tileSizeSlider(page: Page): Promise<Locator> {
	const onBar = page.locator('header.topbar .size:not(.gone) input[type="range"]');
	const press = page.locator('.bar-fold').getByRole('button', { name: 'Tile size', exact: true });
	await onBar.or(press).first().waitFor();
	if (await onBar.isVisible()) return onBar;
	const inPanel = page.locator('.tile-size input[type="range"]');
	if (!(await inPanel.isVisible())) await press.click();
	await inPanel.waitFor();
	return inPanel;
}
