import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * A column that both includes and excludes (`?media=image&media=-video`) draws two chips, both
 * removable: comparing the screen's own filters against only the FIRST value would draw the second
 * as a locked chip. The address is set directly, as a link or the back button would.
 */

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1500, height: 900 });
});

/* By the shared chip's classes, so a rename breaks the component's tests too. `.narrows` is
   `display: contents`, so the chip is its child. */
const CHIP = '.filters .narrows > .chip';

async function chips(page: import('@playwright/test').Page) {
	return page.locator(CHIP).evaluateAll((drawn) =>
		drawn.map((chip) => ({
			text: (chip.textContent ?? '').replace(/\s+/g, ' ').trim(),
			// The shared chip's words: `refused` and `selected`.
			excluded: chip.classList.contains('refused'),
			// The facet row's three-state box, an element rather than a second button.
			box: (() => {
				const held = chip.querySelector('.box .box');
				if (held === null) return 'none';
				if (held.classList.contains('out')) return 'out';
				return held.classList.contains('on') ? 'on' : 'none';
			})()
		}))
	);
}

test('one column, one value in and one out, is two chips and no padlock', async ({ page }) => {
	await page.goto('/browse?media=image&media=-video');
	await expect(page.locator(CHIP).first()).toBeVisible();

	const drawn = await chips(page);

	expect(drawn.map((one) => one.box)).toEqual(['on', 'out']);
	expect(drawn[0].text).toContain('Photos');
	expect(drawn[1].text).toContain('Videos');

	/* Two chips, both removable: the screen's own constraint is not drawn at all, so counting
	   locked chips would be nought either way. */
	expect(drawn).toHaveLength(2);
	await expect(page.locator(`${CHIP} > button.remove`)).toHaveCount(2);
});

test('the excluded value is struck through, and the included one is not', async ({ page }) => {
	await page.goto('/browse?media=image&media=-video');
	await expect(page.locator(CHIP).first()).toBeVisible();

	/* A line through the value, as the facet row wears it: a computed style only a browser has. */
	const lines = await page
		.locator(`${CHIP} .value`)
		.evaluateAll((values) => values.map((value) => getComputedStyle(value).textDecorationLine));

	expect(lines[0]).not.toContain('line-through');
	expect(lines[1]).toContain('line-through');
});

test('the refusal is in the accessible name, not only in the strike', async ({ page }) => {
	await page.goto('/browse?media=-video');
	const chip = page.locator(CHIP).first();
	await expect(chip).toBeVisible();

	/* A line through text is not announced, so a word says it. */
	await expect(chip).toContainText('not');
});

test('both chips can be taken off, and the screen keeps the other one', async ({ page }) => {
	await page.goto('/browse?media=image&media=-video');
	await expect(page.locator(CHIP)).toHaveCount(2);

	/* The chip first: its cross rises only while the chip is hovered. */
	const first = page.locator(CHIP).first();
	await first.hover();
	await first.locator('button.remove').click();
	await expect(page.locator(CHIP)).toHaveCount(1);

	// The one left is still an exclusion.
	expect(new URL(page.url()).searchParams.getAll('media')).toEqual(['-video']);
});

test('a People row ticked on a wall of files writes the id, and its chip still reads the name', async ({
	page
}) => {
	const id = '01HX00000000000000000000PB';
	await page.route('**/api/assets/facets*', (route) => {
		const facet = new URL(route.request().url()).searchParams.get('facet') ?? '';
		const values = facet === 'people' ? [{ value: id, label: 'Bryn Calloway', count: 1 }] : [];
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ facet, values })
		});
	});
	await page.route(`**/api/people/${id}`, (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ id, name: 'Bryn Calloway' })
		})
	);
	await page.goto('/browse');
	await page.getByRole('button', { name: 'Filter', exact: true }).click();
	await page.locator('.column .value', { hasText: 'Bryn Calloway' }).click();

	await expect(page).toHaveURL(new RegExp(`[?&]people=${id}(&|$)`));
	await expect(page.locator(CHIP)).toHaveCount(1);
	expect((await chips(page))[0].text).toContain('Bryn Calloway');
	expect((await chips(page))[0].text).not.toContain(id);
});
