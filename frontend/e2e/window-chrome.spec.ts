/* The desktop window's own title bar, and the inset it bought back.
 *
 * All geometry, so none of it is answerable without a layout engine: the unit environment renders
 * the markup happily whatever the stylesheet does with it, and every rule involved is guarded on
 * an attribute a stylesheet reads.
 *
 * ## Why a browser can test a desktop-only strip
 *
 * The guard is `data-window="overlaid"`, which the layout stamps when the shell says the caption
 * buttons really are drawn over the page, and the layout decides that by asking the bridge the
 * shell injects. Injecting the same object is not a fixture that waves the rule through: it is
 * what makes the browser the desktop case, which is the only way to measure the two side by side.
 *
 * ## The pair is the point
 *
 * Both shells are measured in every test here: the desktop window gains a strip and gets its
 * inset back WITHOUT the browser moving at all, and a test of one alone cannot say that.
 */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/** What the desktop shell puts on the window, reduced to the one method the layout asks about. */
async function asTheDesktopApp(page: Page): Promise<void> {
	await page.addInitScript(() => {
		(window as unknown as { sift: unknown }).sift = {
			isDesktop: true,
			setTitleBar: async () => true
		};
	});
}

const box = async (page: Page, selector: string) => (await page.locator(selector).boundingBox())!;

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test.describe('in a browser', () => {
	test('there is no strip, and nothing is moved to make room for one', async ({ page }) => {
		await page.goto('/browse');
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });

		await expect(page.locator('.window-bar')).toHaveCount(1);
		/* Present in the markup and drawn as nothing: the component is rendered unconditionally and
		   the stylesheet is what decides. A count of zero here would mean the layout was
		   choosing, which is the arrangement that could disagree with itself. */
		await expect(page.locator('.window-bar')).toBeHidden();

		const shell = await box(page, '.shell');
		expect(shell.y).toBe(0);
		expect(Math.round(shell.height)).toBe(900);
	});
});

test.describe('inside the desktop window', () => {
	test.beforeEach(async ({ page }) => {
		await asTheDesktopApp(page);
	});

	test("a strip of Sift's own runs across the top", async ({ page }) => {
		await page.goto('/browse');
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });

		const bar = await box(page, '.window-bar');
		expect(bar.y).toBe(0);
		expect(Math.round(bar.width)).toBe(1400);
		/* The same 36 the shell creates the caption overlay with. The operating system centres its
		   buttons inside whatever height it is given, so a disagreement leaves them off this strip's
		   line. */
		expect(Math.round(bar.height)).toBe(36);
	});

	test('and everything else starts below it rather than under it', async ({ page }) => {
		/*
		 * Measured as PADDING, which is what it is. The box starts at the top of the window and is
		 * the whole of it; what moves is everything INSIDE it. A top margin on a child of `body`
		 * would collapse through it and give the document a scrollbar it has no content for.
		 */
		await page.goto('/browse');
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });

		const shell = await box(page, '.shell');
		expect(Math.round(shell.y)).toBe(0);
		// The WHOLE window, not short by the strip: that is the difference padding makes.
		expect(Math.round(shell.height)).toBe(900);

		// And nothing inside it is under the strip, which is the thing the test is named for.
		const content = await box(page, '.content');
		expect(content.y).toBeGreaterThanOrEqual(36);
	});

	/*
	 * The inset on three sides.
	 *
	 * The caption buttons sit on the strip above the content, so the panel is inset on the same
	 * three sides it is in a browser rather than running flush to the top and the right.
	 */
	test('and the content panel is inset from the top and the right again', async ({ page }) => {
		await page.goto('/browse');
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });

		const shell = await box(page, '.shell');
		const content = await box(page, '.content');

		// Below the STRIP by the inset, rather than below the shell's own top edge: the shell starts
		// at the top of the window and clears the strip with padding, so its top edge and the room
		// inside it are 36px apart. Written as the sum so the number that changes is the inset.
		expect(Math.round(content.y - shell.y)).toBe(36 + 8);
		expect(Math.round(shell.x + shell.width - (content.x + content.width))).toBe(8);
	});

	test('and its top-right corner is curved rather than squared off for the buttons', async ({
		page
	}) => {
		await page.goto('/browse');
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });

		const radius = await page
			.locator('.content')
			.evaluate((el) => getComputedStyle(el).borderTopRightRadius);

		expect(radius).not.toBe('0px');
	});

	/*
	 * EVERY `position: fixed` THING INSET FROM THE TOP OF THE WINDOW, and this is a class of fault
	 * rather than a case.
	 *
	 * A fixed box is fixed to the WINDOW, and the window starts 36px above where the application
	 * does, so a close button anchored to the top can land half behind the strip. These are what is
	 * anchored to the top. Everything else that is fixed either covers the whole window on purpose
	 * (a veil, a backdrop) or is placed from the bottom or from a pointer, and neither of those can
	 * land under the strip.
	 */
	test('and a dialog over the app lines up with the page, not with the window', async ({
		page
	}) => {
		await page.route('**/api/assets?*', (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [
						{
							id: 'm1',
							media_type: 'video',
							width: 1920,
							height: 1080,
							duration_ms: 1000,
							favorite: false,
							rating: null,
							concealed: false,
							original_filename: 'clip.mp4',
							thumb: true
						}
					],
					total: 1,
					limit: 50,
					offset: 0
				})
			})
		);
		await page.route('**/api/assets/m1', (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					id: 'm1',
					media_type: 'video',
					width: 1920,
					height: 1080,
					duration_ms: 1000,
					favorite: false,
					rating: null,
					concealed: false,
					original_filename: 'clip.mp4',
					thumb: true
				})
			})
		);
		await page.route('**/api/assets/*/thumb*', (route) => route.fulfill({ status: 404 }));
		await page.route('**/api/assets/*/preview*', (route) => route.fulfill({ status: 404 }));

		await page.goto('/browse');
		const topbarBottom = await page
			.locator('.topbar')
			.evaluate((el) => el.getBoundingClientRect().bottom);
		await page.locator('.tile').first().click();
		const sheet = page.getByRole('dialog');
		await expect(sheet).toBeVisible();
		// It grows into place on a transition, so `toBeVisible` is the FIRST frame of that and every
		// measurement taken there is of a smaller, lower dialog. Two consecutive frames agreeing
		// about the box is what settled means: the same wait `asset-modal.spec.ts` makes.
		await sheet.evaluate(
			(element) =>
				new Promise<void>((done) => {
					let last = '';
					const look = () => {
						const at = element.getBoundingClientRect();
						const now = `${at.top}x${at.width}`;
						if (now === last) return done();
						last = now;
						requestAnimationFrame(look);
					};
					requestAnimationFrame(look);
				})
		);
		const sheetTop = (await sheet.boundingBox())!.y;

		/* The same claim `asset-modal.spec.ts` makes in a browser, made here with the strip on,
		   and written the same way, to the same tolerance, so the two cannot come to mean
		   different things. It passes there whatever this rule says, because the offset is zero,
		   so the browser test could never catch the dialog being drawn a strip's height too
		   high. */
		expect(
			Math.abs(sheetTop - topbarBottom),
			`the topbar ends at ${topbarBottom} and the dialog starts at ${sheetTop}`
		).toBeLessThan(2);
	});

	test('and the settings panel clears the strip on a window short enough to matter', async ({
		page
	}) => {
		/* 5vh of 700 is 35px and the strip is 36, so this is the height at which a 5vh rule
		   would put the top of the panel BEHIND the strip rather than merely close to it. Opened
		   from the rail rather than navigated to: `/settings` is a page as well as a panel, and
		   the page (unlike the panel) is not fixed to the window. */
		await page.setViewportSize({ width: 1400, height: 700 });
		await page.goto('/browse');

		/* WAITED FOR BEFORE THE LINK IS PRESSED, and this is not tidying up. The rail's Settings link
		   is an anchor: clicked before the router has taken the page over, the browser follows it and
		   loads `/settings` as a document, which draws the settings PAGE, and there is no dialog on
		   it to measure. On a quiet machine hydration wins that race every time; under four browsers
		   it does not, and the test then waits out its whole deadline for a dialog that was never
		   going to exist. Waiting for the rail to be drawn is waiting for the router to be there. */
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });
		await page.getByRole('link', { name: 'Settings', exact: true }).click();

		/* Named by what it IS rather than by a class. `.panel` is a word more than one screen
		   reaches for, and `.first()` resolves to whichever comes first in a document with a modal
		   opening over it. The settings modal says what it is. */
		const panel = page.getByRole('dialog', { name: 'Settings' });
		await expect(panel).toBeVisible();

		/*
		 * SETTLED, THEN READ ONCE, not `expect.poll`, which cannot fail here.
		 *
		 * It animates in, so the first frame is not where it ends up. Polling the reading until it
		 * satisfies the assertion is the opposite of robust: somewhere in an entrance animation the
		 * top passes through a value above the strip, poll accepts it, and the test goes green
		 * against the very rule it holds.
		 *
		 * So the wait is for the box to STOP MOVING, and then there is exactly one reading.
		 */
		await panel.evaluate(
			(element) =>
				new Promise<void>((done) => {
					let last = '';
					const look = () => {
						const at = element.getBoundingClientRect();
						const now = `${at.top}x${at.height}`;
						if (now === last) return done();
						last = now;
						requestAnimationFrame(look);
					};
					requestAnimationFrame(look);
				})
		);

		// 35 without the offset: one pixel behind the strip, which is the whole of the fault and is
		// invisible in a browser, where the strip is not there.
		expect((await panel.boundingBox())!.y).toBeGreaterThanOrEqual(36);
	});

	/* The card on an empty page is centred on the room it actually has, not on the window: a
	   screen with no application shell around it still starts below the strip.

	   Read off the CARD, not the page. The page fills the window and clears the strip with
	   padding, so its own box starts at nought. What matters is where the CARD lands: a
	   `padding` shorthand two lines under the clearing would overwrite it and centre the card on
	   the whole window with its top behind the strip. `gate:window-chrome` refuses that shape. */
	test('and a signed-out screen starts below the strip too', async ({ page }) => {
		await page.context().clearCookies();
		await page.goto('/login');
		await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();

		const page_ = await box(page, '.page');
		expect(Math.round(page_.y)).toBe(0);
		expect(Math.round(page_.height)).toBe(900);

		// The card itself, which is what the strip could otherwise sit on top of.
		const card = await box(page, '.page .card');
		expect(card.y).toBeGreaterThanOrEqual(36);
		/* And centred on the room BELOW the strip rather than on the window: the space over the card
		   is the space under it, once the strip is taken off the top. */
		const over = card.y - 36;
		const under = 900 - (card.y + card.height);
		expect(Math.abs(over - under)).toBeLessThanOrEqual(1);
	});
});
