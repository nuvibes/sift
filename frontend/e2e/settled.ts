import { expect, type Locator } from '@playwright/test';

/**
 * Wait for a sheet to finish arriving before measuring or pressing anything on it.
 *
 * It grows into place on a transition, so `toBeVisible` is the FIRST frame of that rather than
 * where it ends up: a measurement taken there is of a smaller sheet in a different place, and a
 * press aimed at a small handle lands beside it.
 *
 * Two frames agreeing about the box is not enough: a Svelte `css` transition compiles to a real CSS
 * animation, which the browser runs off the main thread, so two `getBoundingClientRect()` reads a
 * frame apart can agree in the MIDDLE of it, on a busy machine more than anywhere, and the wait
 * ends early with no tell at all.
 *
 * The animation's own `finished` promise is the fact, so that is what is waited on. A frame first,
 * because the transition is registered on the tick after the element appears, and the loop repeats
 * so that a second animation starting behind the first is waited on too; it ends when a frame goes
 * by with nothing running. Whatever is inside the sheet counts, except what never ends (a spinner
 * turning), which is not an arrival.
 */
export async function settled(sheet: Locator): Promise<void> {
	await sheet.evaluate(async (element) => {
		for (let round = 0; round < 4; round += 1) {
			await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined)));
			const running = element
				.getAnimations({ subtree: true })
				.filter((one) => one.effect?.getComputedTiming().endTime !== Infinity);
			if (running.length === 0) return;
			// A cancelled animation rejects; that is the element having settled, not a failure.
			await Promise.all(running.map((one) => one.finished.catch(() => undefined)));
		}
	});
}

/**
 * Wait until an element's box has stopped moving: two reads a pause apart agree. For what is not
 * an animation at all, a sheet that grows as its data arrives and slides what is below it from
 * under the pointer, which `settled` cannot see.
 */
export async function stillFor(target: Locator, pause = 200): Promise<void> {
	await expect
		.poll(async () => {
			const before = JSON.stringify(await target.boundingBox());
			await target.page().waitForTimeout(pause);
			return before === JSON.stringify(await target.boundingBox());
		})
		.toBe(true);
}
