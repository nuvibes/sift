import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * A filter column that both INCLUDES and EXCLUDES, drawn as two chips and nothing else.
 *
 * The query language allows this: a named parameter may appear more than once and every occurrence
 * is ANDed, so `?media=image&media=-video` is "images, and not video".
 *
 * ## The fault
 *
 * The bar works out which filters are the SCREEN's own (a person's page is `people:"..."`, and
 * that chip is drawn with a padlock and cannot be taken off) by comparing what the screen carries
 * against what is in the address. Comparing against only the FIRST value of the parameter would
 * draw the second as a locked chip: the same filter twice on one bar, once removable and once
 * padlocked, the padlocked copy spelled the way the address writes it (`-video`).
 *
 * ## Why the address is set directly rather than by clicking
 *
 * Clicking is covered where clicking lives: the panel's own rows. What is under test here is what
 * the bar makes of an address, and an address is a thing somebody can arrive at from a link, a
 * saved search or the back button as easily as from a click. Driving it directly also means this
 * cannot flake on facet counts arriving.
 */

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1500, height: 900 });
});

/*
 * The chips, named the way the bar draws them: by the shared chip component's own classes. A test
 * that names an element by a class does not go red when the class stops existing, it goes QUIET.
 * So naming the shared component's classes means the next rename breaks the component's tests too.
 */
/* The chips that narrow. They sit in one box of their own inside the row (`.narrows`, laid out
   as `display: contents`, so pointing at any chip lights what they narrow), which is why the chip
   is its child rather than the row's. */
const CHIP = '.filters .narrows > .chip';

/** The chips on the bar, as a person reads them. */
async function chips(page: import('@playwright/test').Page) {
	return page.locator(CHIP).evaluateAll((drawn) =>
		drawn.map((chip) => ({
			text: (chip.textContent ?? '').replace(/\s+/g, ' ').trim(),
			// `refused` is the shared chip's word for it; `selected` is the other half of the same pair.
			excluded: chip.classList.contains('refused'),
			// The three-state box the facet row carries, so the row and the chip it produced are
			// recognisably one thing. It is the box's own element, not a button: the whole chip is
			// what you press, so a button inside it would be a second control saying the same
			// thing.
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

	/* A locked chip here would be the bar deciding that half of what somebody asked for is a
	   constraint the screen imposed, which also makes it unremovable.

	   Asked as "two chips, and every one of them can be taken off" rather than as "no chip
	   carries the locked class". The bar does not draw the screen's own constraint at all, so a
	   count of locked chips is nought whichever way the fault comes back. What has to stay true
	   is that both of these belong to the person who typed them. */
	expect(drawn).toHaveLength(2);
	await expect(page.locator(`${CHIP} > button.remove`)).toHaveCount(2);
});

test('the excluded value is struck through, and the included one is not', async ({ page }) => {
	await page.goto('/browse?media=image&media=-video');
	await expect(page.locator(CHIP).first()).toBeVisible();

	/* The mark, not the colour. A line through the value is what the facet row wears, and the two
	   have to agree or the row and the chip it produced read as two different facts. Only a browser
	   can answer this: it is a computed style on an element inside a component. */
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

	/* A line through text is not announced. Without a word saying so, the chip reads as "media:
	   video" to somebody who cannot see it, which is the opposite of what it does. */
	await expect(chip).toContainText('not');
});

test('both chips can be taken off, and the screen keeps the other one', async ({ page }) => {
	await page.goto('/browse?media=image&media=-video');
	await expect(page.locator(CHIP)).toHaveCount(2);

	/*
	 * The chip first, then its cross. The cross is `opacity: 0`, sunk below its own line and taking
	 * no pointer until something is over the CHIP. Aimed at from rest the point Playwright computes
	 * is the sunk box, which is outside the chip, so the hover never reaches the chip and the cross
	 * never rises.
	 */
	const first = page.locator(CHIP).first();
	await first.hover();
	await first.locator('button.remove').click();
	await expect(page.locator(CHIP)).toHaveCount(1);

	// The one left is the exclusion, still an exclusion.
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
