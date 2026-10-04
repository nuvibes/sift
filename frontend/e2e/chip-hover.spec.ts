import { expect, test, type Locator, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * A CHIP'S THREE CONTROLS ANSWER THE POINTER SEPARATELY, AND ONLY THE BROWSER CAN SAY SO.
 *
 * ## The fault this exists for
 *
 * A filter chip is three things a pointer can be over: the body, which flips the filter between
 * included and refused; the any-or-all mark, which swaps how its values combine; and the cross,
 * which destroys the whole chip. Each must light on its own: pointing at the mark must not
 * brighten the words as though the filter were about to be pressed, and the reverse.
 *
 * Two rules hold it, in two files:
 *
 *   - `Chip.svelte` scopes its hover rules to `:has(.body:hover)`, so the chip's ground appears for
 *     the body and for neither of the other two. That is APP-WIDE: every chip in Sift is that
 *     component.
 *   - `FilterChip.svelte` gives the mark its own ink, as `:global(.pressable.in-chip)`, because a
 *     bare `.in-chip` TIES on specificity with `Pressable`'s own `color: inherit` reset and loses on
 *     source order.
 *
 * ## Why here and not in a unit test
 *
 * jsdom does not resolve `:hover` at all. A unit test can read the SOURCE and assert the selector
 * is spelled a certain way, which pins the spelling of one fix rather than the property anybody
 * cares about. A rule can look right and do nothing; only a browser can tell. So this asks a real
 * Chromium what the elements ACTUALLY CAME OUT AS, under a real pointer.
 *
 * ## Why every assertion is relative
 *
 * Nothing here names a token. What is being checked is that a change happens for one control and
 * does not happen for its neighbours, so each state is measured against the SAME element's
 * resting value, read a moment earlier. A palette change must not make this file wrong, and a rule
 * that quietly stopped reaching its element cannot be rescued by a colour that happens to match.
 */

/** A screen narrowed by one dimension with two values, which is the only filter that draws all
 *  three controls: a body, an any-or-all mark (two values, not refused) and a cross. */
async function chipWithEveryControl(page: Page): Promise<Locator> {
	await page.goto('/browse?tags=beach%7Csunset');
	const chip = page.locator('.filters .chip').first();
	await expect(chip).toBeVisible();
	// A KNOWN POSITIVE for the arrangement itself. Without the mark this file measures two controls
	// and passes, having never looked at the rule it was written for.
	await expect(chip.locator('.pressable.in-chip')).toHaveCount(1);
	await expect(chip.locator('.remove')).toHaveCount(1);
	return chip;
}

/**
 * One computed property of one element, once it has STOPPED MOVING.
 *
 * A read taken after a fixed wait races the transition: a colour read at 120 ms of a 140 ms
 * ease-out is ninety-nine per cent of the way home, one unit off in two channels, and that failure
 * is indistinguishable from the fault this file exists to catch.
 *
 * So the value is read after the element's own transitions have finished, which is a question the
 * browser can answer exactly and which no duration token can make wrong. Infinite animations are
 * left out (a spinner anywhere on the page would never finish), and the waits below stay, with a
 * smaller job: letting the pointer be processed so there is a transition to wait for.
 */
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

/**
 * BOTH of the properties a chip's hover can move, as one string.
 *
 * Not the background alone: a SELECTED chip (which
 * is what every included filter is) rests on `--sift-accent-bg` and hovers to the same background
 * with brighter ink. Asked about the ground alone, this file's own known positive could never fire,
 * and the two checks under it would then be passing for the reason they exist to refuse.
 */
async function look(where: Locator): Promise<string> {
	return `${await paint(where, 'background-color')} / ${await paint(where, 'color')}`;
}

/**
 * Put the pointer in the middle of something and give the browser a moment to notice. Not long
 * enough to settle anything, and it does not have to be: `paint` waits for the transition itself.
 * This is only so there IS one by the time it looks.
 */
async function pointAt(page: Page, where: Locator): Promise<void> {
	await where.hover();
	await page.waitForTimeout(120);
}

/** Take the pointer off the chip entirely, so the next measurement starts from rest. */
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

	/* The body IS the chip's press, so this is the known positive: without it, a rule that reached
	   nothing at all would pass both checks below by never changing anything. */
	await pointAt(page, body);
	expect(await look(chip), "the chip's own hover does not reach it").not.toBe(atRest);

	await pointAway(page);
	await pointAt(page, mark);
	expect(await look(chip), 'pointing at the any-or-all mark lit the whole chip').toBe(atRest);

	/*
	 * The cross is reached through the chip.
	 *
	 * The cross is `opacity: 0`, `translate: 0 100%` and `pointer-events: none` until the pointer
	 * is on the CHIP: a row of chips each ending in a cross is a row of ways to destroy something,
	 * so it rises only under the hand. Aimed at from rest, the point Playwright computes is its
	 * sunk box (below the chip, taking no pointer), so the hover never reaches the chip, the
	 * cross never rises, and the two wait on each other until the test's own deadline.
	 *
	 * So the pointer arrives the way a person's does: onto the chip, and then along to the cross.
	 * The chip's own hover rules are scoped to `:has(.body:hover)`, so a pointer sitting on the
	 * cross must leave the chip looking exactly as it does at rest.
	 */
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

	/* The mark is quieter than the words it sits beside. That is the whole of `--sift-ink-2`'s job
	   here, and it is what `Pressable`'s `color: inherit` took away when it won the tie. Asked as a
	   difference from the chip's own ink rather than as a token, so the palette may move. */
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
