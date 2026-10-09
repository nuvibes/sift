import { type Locator, type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * A chip's three controls (body, any-or-all mark, cross) answer the pointer separately: `Chip`
 * scopes its hover to `:has(.body:hover)`, and `FilterChip` gives the mark its own ink. jsdom does
 * not resolve `:hover`, so a real browser is asked. Every check is relative to the same element
 * at rest, so a palette change cannot make this file wrong.
 */

/** Two values on one dimension: the only filter that draws all three controls. */
async function chipWithEveryControl(page: Page): Promise<Locator> {
	await page.goto('/browse?tags=beach%7Csunset');
	const chip = page.locator('.filters .chip').first();
	await expect(chip).toBeVisible();
	// A known positive: without the mark this file would measure two controls and pass.
	await expect(chip.locator('.pressable.in-chip')).toHaveCount(1);
	await expect(chip.locator('.remove')).toHaveCount(1);
	return chip;
}

/** One computed property, read once the element's own (finite) transitions have finished. */
async function paint(where: Locator, property: 'background-color' | 'color'): Promise<string> {
	return await where.evaluate(async (el, name) => {
		await Promise.all(
			el
				.getAnimations()
				.filter((one) => one.effect?.getTiming().iterations !== Infinity)
				.map((one) => one.finished.catch(() => undefined))
		);
		return getComputedStyle(el).getPropertyValue(name);
	}, property);
}

/** Background and ink together: a selected chip hovers to the same background, brighter ink. */
async function look(where: Locator): Promise<string> {
	return `${await paint(where, 'background-color')} / ${await paint(where, 'color')}`;
}

/** Point at something, so there is a transition for `paint` to wait for. */
async function pointAt(page: Page, where: Locator): Promise<void> {
	await where.hover();
	await page.waitForTimeout(120);
}

/** Take the pointer off the chip, so the next measurement starts from rest. */
async function pointAway(page: Page): Promise<void> {
	await page.mouse.move(0, 0);
	await page.waitForTimeout(120);
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test("the chip's hover answers its body and neither of its other two controls", async ({
	page
}) => {
	const chip = await chipWithEveryControl(page);
	const body = chip.locator('.body');
	const mark = chip.locator('.pressable.in-chip');
	const cross = chip.locator('.remove');

	await pointAway(page);
	const atRest = await look(chip);

	/* The known positive: the body is the chip's press. */
	await pointAt(page, body);
	expect(await look(chip), "the chip's own hover does not reach it").not.toBe(atRest);

	await pointAway(page);
	await pointAt(page, mark);
	expect(await look(chip), 'pointing at the any-or-all mark lit the whole chip').toBe(atRest);

	/* The cross rises only while the pointer is on the chip, so it is reached along the chip, and
	 * sitting on it must leave the chip looking as it does at rest. */
	await pointAway(page);
	await pointAt(page, body);
	await pointAt(page, cross);
	expect(
		await look(chip),
		'pointing at the cross (which DESTROYS the chip) lit it as though it were about to be pressed'
	).toBe(atRest);
});

test('the any-or-all mark carries its own ink and lights on its own', async ({ page }) => {
	const chip = await chipWithEveryControl(page);
	const body = chip.locator('.body');
	const mark = chip.locator('.pressable.in-chip');

	await pointAway(page);
	const atRest = await paint(mark, 'color');

	/* Quieter than the words, as a difference rather than a token. */
	expect(atRest, "the mark wears the chip's ink rather than its own").not.toBe(
		await paint(body, 'color')
	);

	await pointAt(page, mark);
	expect(await paint(mark, 'color'), 'the mark does not light under the pointer').not.toBe(atRest);

	await pointAway(page);
	await pointAt(page, body);
	expect(
		await paint(mark, 'color'),
		'pointing at the words lit the mark, which is a different act'
	).toBe(atRest);
});
