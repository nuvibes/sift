import { expect, test } from '@playwright/test';
import { SIZE_STEPS } from '../src/lib/grid/justify';
import { signInAsAdmin } from './admin';

/*
 * Every top-level page's title sits in the same place.
 *
 * A page that carries its own padding on top of the layout's moves the title sideways and down as
 * you switch pages. This pins them to one spot: a change that reintroduces a per-page inset fails
 * here rather than being noticed by eye three pages later.
 *
 * A couple of pixels of tolerance, because a title centred against a toolbar of taller controls and
 * one standing alone round differently at the sub-pixel level.
 */

const PAGES = ['/browse', '/favorites', '/tags', '/people', '/sites', '/collections', '/downloads'];

test('every top-level title lands at the same x and y', async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/search?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0, applied: [] })
		})
	);

	/*
	 * Measured against the top of `main`, not against the window.
	 *
	 * The chips row above the title appears only when there are chips to show (a fixed empty row
	 * would be dead space above the title on every unfiltered screen), so a screen carrying a
	 * filter legitimately has its title one row lower than a screen carrying none. `/favorites` is
	 * that case in this very list: it IS `fav:yes`, so it draws a locked chip and `/browse` does
	 * not.
	 *
	 * Measuring inside `main` asks the question the test is actually about: does any screen add
	 * padding of its own above its title.
	 */
	const spots: { path: string; x: number; y: number }[] = [];
	for (const path of PAGES) {
		await page.goto(path);
		const title = page.getByRole('heading', { level: 1 }).first();
		await title.waitFor();
		const box = (await title.boundingBox())!;
		const main = (await page.locator('main').boundingBox())!;
		spots.push({ path, x: Math.round(box.x - main.x), y: Math.round(box.y - main.y) });
	}

	const xs = spots.map((s) => s.x);
	const ys = spots.map((s) => s.y);
	const spread = (ns: number[]) => Math.max(...ns) - Math.min(...ns);

	expect(spread(xs), `x varied across pages: ${JSON.stringify(spots)}`).toBeLessThanOrEqual(1);
	expect(spread(ys), `y varied across pages: ${JSON.stringify(spots)}`).toBeLessThanOrEqual(2);
});

/*
 * And every one of them says what it holds the same way.
 *
 * The row above is one component, but a screen can still put its own prose inside it and end up
 * reporting the same fact differently from its neighbours: "12 results" as a sentence in body
 * text where every other screen shows a count as one pill beside the title. Nothing about that
 * is misaligned, so the x/y test above passes; it just does not look like the same application
 * from one page to the next.
 *
 * So the rule is structural: the header ROW carries a heading, a count, a status and this screen's
 * controls, and no prose. A lede is prose and is allowed: it sits under the row, not in it.
 */
test('no top-level header writes its own prose into the title row', async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });

	const offenders: string[] = [];
	for (const path of PAGES) {
		await page.goto(path);
		await page.getByRole('heading', { level: 1 }).first().waitFor();
		const prose = await page.locator('.page-header .row p').count();
		if (prose > 0) offenders.push(`${path} (${prose})`);
	}

	expect(offenders, `these draw a paragraph inside the title row: ${offenders.join(', ')}`).toEqual(
		[]
	);
});

/*
 * What a fresh document per page can never catch.
 *
 * The test above calls `page.goto()` for every page, so nothing one page's stylesheet did could
 * still be there for the next one. A full-bleed screen's CSS carrying a `:global(main:has(...))`
 * rule aimed at the shared layout stays loaded once Vite has loaded it (client-side navigation
 * never unloads it) and goes on matching whatever other screen next carries that class.
 *
 * This one navigates the way a person does: it clicks through the rail rather than reloading the
 * document between pages, so a leak that only shows up after that has somewhere to happen.
 */
test('a full-bleed screen visited once does not poison an unrelated one afterwards', async ({
	page
}) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })
		})
	);

	const me = await page.request.get('/api/auth/me');
	const csrfToken = (await me.json()).csrf_token as string;
	async function create(path: string, data: object): Promise<string> {
		const made = await page.request.post(path, { data, headers: { 'x-csrf-token': csrfToken } });
		expect(made.ok(), await made.text()).toBeTruthy();
		return (await made.json()).id as string;
	}

	const personName = `e2e-alignment-${Date.now()}`;
	await create('/api/people', { name: personName, vault: false });

	/*
	 * `main`'s own bounding box never moves: the padding lives INSIDE it and shifts what main
	 * HOLDS, not main itself, which is why measuring main's own box proves nothing. The computed
	 * style is what a leaked `padding: 0` shows up as.
	 *
	 * Every screen that draws its own frame is given the whole box, so `main` is correctly unpadded
	 * on most of them; the padding this watches for belongs to the screens that do not.
	 */
	/*
	 * `.main-inner`, not `main`.
	 *
	 * The inset lives INSIDE the scrolling box, on the element the content is laid out in. On the
	 * box that scrolls it would sit above and below the scrollbar instead of around the content,
	 * which is the same split the page frame makes. `main` legitimately measures zero, so measuring
	 * it would be a test that can only fail.
	 */
	const mainPaddingLeft = () =>
		page
			.locator('main .main-inner')
			.first()
			.evaluate((el) => parseFloat(getComputedStyle(el).paddingLeft));

	/** How far in a FRAMED screen puts its title, which is the frame's job rather than main's. */
	const titleLeft = async () => {
		const title = page.getByRole('heading', { level: 1 }).first();
		await title.waitFor();
		return Math.round((await title.boundingBox())!.x);
	};

	// The one real navigation. Everything after this is a click, the way a person moves around.
	await page.goto('/browse');

	/* Scoped to the rail, and it has to be.
	 *
	 * An entity page draws a strip of tabs beside its heading, and those tabs are LINKS wearing the
	 * same words the rail does, so an unscoped "Tags" matches the rail's destination and the
	 * person page's tab together, and Playwright refuses rather than guessing. Refusing is the right
	 * behaviour: the two go to different places, and a walk that picked whichever came first would
	 * have been measuring a screen it did not mean to be on. */
	const destination = (name: string) =>
		page.locator('nav.rail').getByRole('link', { name, exact: true });

	// The `.screen` variant of the leak: `AssetGrid`'s CSS is resident from `/browse` above, and
	// People's own top-level element carries that same class name for a reason that has nothing to
	// do with AssetGrid.
	await destination('People').click();
	// The wall writes its own position into the address as soon as the first page lands, so the
	// anchor may or may not be on the end by the time this runs. What the test needs is the route.
	await expect(page).toHaveURL(/\/people(\?|$)/);
	// Framed, so main gives it the whole box, and its title still lands where the grid's did.
	const framed = await titleLeft();
	expect(framed, 'a framed screen lost its own inset').toBeGreaterThan(20);

	// Into a person's own page, which genuinely IS full-bleed, and then straight back out. The
	// `.page` variant of the leak lives here: the person page's CSS reaching out for `.page` and
	// then still matching it on a screen that has nothing to do with a person.
	await page.getByRole('link', { name: personName, exact: true }).click();
	await expect(page).toHaveURL(/\/people\//);

	await destination('Tags').click();
	await expect(page).toHaveURL(/\/tags$/);
	expect(await titleLeft(), "a framed screen lost its inset after a person's own page").toBe(
		framed
	);

	/* A second framed screen, drawn by a different route than Tags: the leak this walk is
	   looking for is CSS from a full-bleed screen still matching on a framed one, and a single
	   sample after the person page could be the one screen that happens to survive it. */
	await destination('Collections').click();
	/* The wall writes where it was left into its own address once a page lands (`?from=`), so
	   the address is the wall's with or without that. */
	await expect(page).toHaveURL(/\/collections(\?|$)/);
	expect(await titleLeft(), "a second framed screen lost its inset after a person's page").toBe(
		framed
	);

	/*
	 * Every destination on the rail is given the whole box (the Theater fills it itself, because
	 * its wall is a flex column that has to have the window's height), so the layout's own padding
	 * reaches nothing and is not measured. What the leak can still do is move a FRAMED screen's
	 * inset, which is what the two assertions above measure.
	 */
	await destination('Theater').click();
	await expect(page).toHaveURL(/\/theater$/);
	await expect(
		page.locator('main .main-inner'),
		'a screen inside the shell is being scrolled and padded by the layout again'
	).toHaveCount(0);
});

test('the Settings title starts where its section list does', async ({ page }) => {
	/*
	 * One vertical line down the left of that screen: the word "Settings", the group headings, and
	 * the section names.
	 *
	 * The list is a scrolling column and carries a focus-ring allowance on every side; the title is
	 * not a column, so without matching it every name in the list would sit a step to the right of
	 * the title above it, and that edge is the first thing the eye follows down the page.
	 *
	 * Read off the TEXT rather than the boxes, with a range, because a box's own left is unmoved by
	 * its padding: comparing box edges can agree while the words plainly do not.
	 */
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await page.goto('/settings/library');
	await expect(page.getByText('Settings', { exact: true }).first()).toBeVisible();

	const lefts = await page.evaluate(() =>
		['.settings > .title', '.settings .sections .heading', '.settings .sections .item'].map(
			(selector) => {
				const element = document.querySelector(selector);
				if (!element) return { selector, left: Number.NaN };
				const range = document.createRange();
				range.selectNodeContents(element);
				const left = Math.round(range.getBoundingClientRect().left);
				range.detach();
				return { selector, left };
			}
		)
	);

	const spread = Math.max(...lefts.map((s) => s.left)) - Math.min(...lefts.map((s) => s.left));
	expect(spread, `these do not start on one line: ${JSON.stringify(lefts)}`).toBeLessThanOrEqual(1);
});

/**
 * Wait for a sheet to finish arriving before poking at it.
 *
 * Not politeness. The layer that watches for a click outside is armed after the dialog opens, so a
 * click fired in the same millisecond lands before anything is listening and is simply swallowed,
 * which reads as the dialog refusing to close. No person clicks that fast; a test does.
 */
async function settled(sheet: import('@playwright/test').Locator) {
	await sheet.evaluate(async (el) => {
		await Promise.all(el.getAnimations({ subtree: true }).map((one) => one.finished));
		await new Promise(requestAnimationFrame);
	});
}

test('a modal goes away when you click beside it', async ({ page }) => {
	/* Clicking outside is what everything else on the screen closes on, so a dialog that ignores
	 * it reads as stuck rather than as careful.
	 */
	await signInAsAdmin(page);
	await page.goto('/browse');

	/* The Add panel stands for all of them: the test is about the rule every sheet in the app
	   obeys, so it needs any sheet. */
	await page.getByRole('button', { name: 'Add', exact: true }).click();
	const sheet = page.getByRole('dialog', { name: 'Add media' });
	await expect(sheet).toBeVisible();
	await settled(sheet);

	// Well away from the sheet.
	await page.mouse.click(8, 8);

	await expect(sheet).toBeHidden();
});

test('and so does the one asking a question', async ({ page }) => {
	/*
	 * The interesting half. An alert dialog traps that click by default, on the reasoning that a
	 * destructive question deserves a deliberate answer. In practice the click outside IS a
	 * deliberate answer, and Cancel is the safe half of every question Sift asks, so dismissing
	 * can only ever decline, which is the second assertion here.
	 */
	await signInAsAdmin(page);

	/* Made through the API: the question under test is the delete's, and Add on a wall opens a
	   screen of its own rather than a box on the wall. */
	const name = `e2e-dismiss-${Date.now()}`;
	const me = await page.request.get('/api/auth/me');
	const made = await page.request.post('/api/collections', {
		data: { name },
		headers: { 'x-csrf-token': (await me.json()).csrf_token as string }
	});
	expect(made.ok(), await made.text()).toBeTruthy();
	/* Narrowed to it, because the wall is paged to the screen and ordered by use: a new collection
	   with nothing in it can sit on a page nobody is looking at. */
	await page.goto('/collections');
	await page.getByRole('searchbox', { name: 'Search collections' }).fill(name);
	/* A card, like every other entity wall's. Delete is on its right-click menu, like every verb
	   acting on a row; the card's corners belong to the heart and the rating. */
	const row = page.locator('.card').filter({ hasText: name });
	await expect(row).toBeVisible();

	await row.click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Delete' }).click();
	const confirm = page.getByRole('alertdialog');
	await expect(confirm).toBeVisible();
	await settled(confirm);

	await page.mouse.click(8, 8);

	await expect(confirm).toBeHidden();
	// Declined, not done: the collection is still there.
	await expect(row).toBeVisible();
});

/*
 * What a button holding an icon and a word has on either side of its contents.
 *
 * An icon is a glyph in a typeface, and the space the typeface gives it is not the same as the box
 * the app draws it in. So a row of "icon, gap, word" laid out as ordinary text sits a couple of
 * pixels off-centre inside a button that is centring it perfectly, and it looks as though it has
 * more room on one side than the other.
 *
 * Taken from the widest button of that shape in the app. A tolerance of half a pixel, because the
 * thing being checked is a two-to-three pixel error.
 */
test('an icon and a word inside a button are centred together', async ({ page }) => {
	await signInAsAdmin(page);
	await page.goto('/browse');
	await expect(page.locator('nav.rail a.item').first()).toBeVisible();

	// The rail's Done button only exists while the rail is being arranged.
	await page.locator('nav.rail a.item').first().click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await expect(page.locator('button.done')).toBeVisible();

	const room = await page.evaluate(() => {
		const button = document.querySelector('button.done')!;
		const box = button.getBoundingClientRect();
		const parts = Array.from(button.children).map((child) => child.getBoundingClientRect());
		return {
			left: Math.min(...parts.map((part) => part.left)) - box.left,
			right: box.right - Math.max(...parts.map((part) => part.right))
		};
	});

	expect(Math.abs(room.left - room.right)).toBeLessThanOrEqual(0.5);
});

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

test('the chip in a tile corner centres its LETTERS, not its line box', async ({ page }) => {
	/*
	 * `GIF` and `1:52` have no descenders, so the room under the baseline is empty, and a chip
	 * that centres the line box puts that empty room to work holding the text up. The box can be
	 * perfectly centred while the word visibly sits high.
	 *
	 * So this measures the INK. The line box is the thing that is already right, and asserting on
	 * it is measuring against a box that cannot fail.
	 *
	 * The metrics come from a 100px probe scaled down rather than from the chip's own 10px:
	 * Chromium rounds text metrics to whole pixels, so asking at the real size answers in steps of
	 * a tenth of the whole box and could not see a defect this size.
	 */
	await signInAsAdmin(page);

	const items = [
		{ id: 'gif-1', media_type: 'gif', width: 800, height: 600, thumb: true, duration_ms: 2000 },
		{ id: 'vid-1', media_type: 'video', width: 800, height: 600, thumb: true, duration_ms: 112000 }
	];
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: items.length, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);

	// The largest tile, where the chip is at its full size.
	/* The top of the ladder, taken from the ladder. A number that is not on it is refused by the
	   size the wall remembers, which silently makes this the automatic size instead. */
	await page.addInitScript(
		(size) => localStorage.setItem('sift.grid.rowHeight', String(size)),
		SIZE_STEPS[SIZE_STEPS.length - 1]
	);
	await page.goto('/browse');
	await expect(page.locator('.duration')).toHaveCount(2);

	/*
	 * Read back the PIXELS, not a rect.
	 *
	 * Geometry cannot see this: Chromium rounds text metrics to whole pixels, and the half-leading
	 * arithmetic on top of that drifts further than the defect itself, so both the line box and a
	 * baseline worked out from font metrics pass against the broken layout. What matters is what
	 * the letters look like, so the chip is screenshotted and the rows holding ink are counted.
	 *
	 * The image goes back INTO the page to be decoded, because the browser already has a PNG
	 * decoder and adding one here would be a dependency for one assertion.
	 */
	const shots: { what: string; png: string }[] = [];
	for (const chip of await page.locator('.duration').all()) {
		shots.push({
			what: (await chip.textContent()) ?? '',
			png: (await chip.screenshot()).toString('base64')
		});
	}

	const chips = await page.evaluate(async (taken) => {
		const read = async (png: string) => {
			const image = new Image();
			image.src = `data:image/png;base64,${png}`;
			await image.decode();
			const canvas = document.createElement('canvas');
			canvas.width = image.width;
			canvas.height = image.height;
			const context = canvas.getContext('2d')!;
			context.drawImage(image, 0, 0);
			const { data } = context.getImageData(0, 0, image.width, image.height);

			// The chip is near-black and the text is near-white, so the brightest pixel in a row says
			// whether any letter reaches it. The threshold is well clear of the anti-aliased edge.
			const brightest: number[] = [];
			for (let y = 0; y < image.height; y++) {
				let most = 0;
				for (let x = 0; x < image.width; x++) {
					const at = (y * image.width + x) * 4;
					most = Math.max(most, (data[at] + data[at + 1] + data[at + 2]) / 3);
				}
				brightest.push(most);
			}
			const floor = Math.min(...brightest);
			const inked = brightest
				.map((value, y) => ({ value, y }))
				.filter((row) => row.value > floor + 60)
				.map((row) => row.y);
			return {
				above: inked[0],
				below: image.height - 1 - inked[inked.length - 1],
				height: image.height
			};
		};
		return Promise.all(taken.map(async (one) => ({ what: one.what, ...(await read(one.png)) })));
	}, shots);

	expect(chips).toHaveLength(2);
	for (const chip of chips) {
		// One pixel, because the chip is drawn on a whole-pixel raster and an odd number of spare
		// rows cannot be split evenly. Two is the defect.
		expect(Math.abs(chip.above - chip.below), `${chip.what} sits off-centre`).toBeLessThanOrEqual(
			1
		);
	}
});

test('a glyph alone in a button is centred in it', async ({ page }) => {
	/*
	 * The same fault as the chip above, one control smaller and in the other axis too.
	 *
	 * The glyph is handed to `Button` as its children, so it is wrapped in the button's label and
	 * laid out as TEXT, and a line box reserves room under the baseline for descenders whether or
	 * not anything in it has one. `place-items: center` then centres that box faithfully and the
	 * cross rides high inside it: three pixels of slack above and five below, about a pixel and a
	 * half of visible error on a 24px button.
	 *
	 * Measured as INK for the reason the chip test gives: the box is the thing that is already
	 * right.
	 *
	 * ## Asked without typing
	 *
	 * Every way this check can flake is about TYPING: a second search box, the query handed over
	 * and the bar's copy emptied, the row rebuilt when the dropdown's answer lands under a
	 * screenshot in flight. So it does not type. The address carries the query, the box is drawn
	 * holding it on the first render, and the cross is there before anything else has happened:
	 * same control, same rule, and nothing in flight to be caught mid-way through.
	 *
	 * ## And it is not one control
	 *
	 * The rule belongs to `Button`, which collapses the strut wherever its label holds a glyph
	 * alone, so this asks it of more than one control. Add to the list rather than writing a second
	 * test.
	 */
	await signInAsAdmin(page);

	await page.goto('/browse?q=anything');
	await expect(page.locator('h1').first()).toBeVisible();

	/*
	 * The icon FONT has to be there before a picture of a glyph means anything.
	 *
	 * The cross is a ligature in Material Symbols. Until that font arrives the browser draws the
	 * fallback for those characters instead, which is a different shape in a different place, and
	 * this test measures INK, so it reports the fallback as the cross sitting thirteen pixels off to
	 * one side. It is not a slow page and no amount of settling reaches it: the box stops moving long
	 * before the font lands, so two readings agree on a button whose glyph is still wrong.
	 */
	/*
	 * Waited for, then PAINTED, and then confirmed.
	 *
	 * Awaiting the font is not the same as having drawn with it. The face is declared
	 * `font-display: block`, so until it arrives the glyph is invisible and the browser swaps it in
	 * on a later frame; the settle loop below cannot see that happen, because a glyph changing
	 * inside a fixed-size button moves no box. A screenshot in that gap photographs the FALLBACK
	 * and reports a defect that is not there.
	 *
	 * So: await it, give the browser two frames to paint with it, and then ASK whether it is really
	 * in use. The last step turns a silent wrong measurement into a sentence.
	 */
	await page.evaluate(async () => {
		await document.fonts.ready;
		await document.fonts.load('24px "Material Symbols Rounded"');
		await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
	});
	expect(
		await page.evaluate(() => document.fonts.check('24px "Material Symbols Rounded"', 'close')),
		'the icon font never arrived, so every measurement below would be of the fallback'
	).toBe(true);

	/*
	 * Held still before it is photographed.
	 *
	 * Typing opens the suggestion sheet, and the field's own arrival is animated, so for a frame
	 * or two after the button exists it is still moving, and a screenshot taken then is a picture
	 * of the movement rather than of a defect.
	 */
	/*
	 * Two glyph-alone buttons, at two sizes of box, which is what makes this a test of the RULE
	 * rather than of one control.
	 *
	 * Both glyphs are symmetric in their own em box, and that is a requirement rather than a
	 * coincidence: an ink measurement asks where the marks are, so it can only be read as a statement
	 * about the BUTTON when the glyph itself has nothing off-centre about it. The crossed-out eye is
	 * the counter-example (its slash runs past the eye), and it measures two pixels out here while
	 * sitting in a box that is centred correctly. Adding a control to this list means checking its
	 * glyph first.
	 */
	for (const name of ['Clear the search', 'Collapse the sidebar']) {
		const clear = page.getByRole('button', { name }).first();
		await expect(clear, `${name} is not on this screen`).toBeVisible();

		let settled = '';
		for (let tries = 0; tries < 20; tries += 1) {
			const now = JSON.stringify(await clear.boundingBox());
			if (now === settled) break;
			settled = now;
			await page.waitForTimeout(100);
		}

		const png = (await clear.screenshot()).toString('base64');

		const ink = await page.evaluate(async (shot) => {
			const image = new Image();
			image.src = `data:image/png;base64,${shot}`;
			await image.decode();
			const canvas = document.createElement('canvas');
			canvas.width = image.width;
			canvas.height = image.height;
			const context = canvas.getContext('2d')!;
			context.drawImage(image, 0, 0);
			const { data } = context.getImageData(0, 0, image.width, image.height);

			const bright = (x: number, y: number) => {
				const at = (y * image.width + x) * 4;
				return (data[at] + data[at + 1] + data[at + 2]) / 3;
			};
			let floor = 255;
			for (let y = 0; y < image.height; y++)
				for (let x = 0; x < image.width; x++) floor = Math.min(floor, bright(x, y));

			const rows: number[] = [];
			const columns: number[] = [];
			for (let y = 0; y < image.height; y++) {
				let most = 0;
				for (let x = 0; x < image.width; x++) most = Math.max(most, bright(x, y));
				if (most > floor + 40) rows.push(y);
			}
			for (let x = 0; x < image.width; x++) {
				let most = 0;
				for (let y = 0; y < image.height; y++) most = Math.max(most, bright(x, y));
				if (most > floor + 40) columns.push(x);
			}
			return {
				above: rows[0],
				below: image.height - 1 - rows[rows.length - 1],
				left: columns[0],
				right: image.width - 1 - columns[columns.length - 1]
			};
		}, png);

		/* There has to BE some ink, and this is a known positive rather than a formality: the icon
		   font draws nothing at all until it arrives, and a button photographed empty yields no rows
		   and no columns, which is `undefined` arithmetic, not a failure with a reason. */
		expect(
			ink.above === undefined || ink.left === undefined,
			`${name}: nothing was drawn in the button, so there was no glyph to measure`
		).toBe(false);

		// One pixel, because a small button screenshotted on a whole-pixel raster cannot always split
		// its spare rows evenly. Three is the defect this was written for.
		expect(
			Math.abs(ink.above - ink.below),
			`${name}: the glyph sits high in its button`
		).toBeLessThanOrEqual(1);
		expect(
			Math.abs(ink.left - ink.right),
			`${name}: the glyph sits off to one side`
		).toBeLessThanOrEqual(1);
	}
});

test('pointing at a link does not change the page you are on', async ({ page }) => {
	/*
	 * The worst version of the leak this file watches for.
	 *
	 * Hovering a link in the rail makes SvelteKit preload the route behind it, and preloading a
	 * route loads its STYLESHEET. So a rule written in any component of any screen takes effect the
	 * moment somebody points at that screen's name, without visiting it, and for the rest of the
	 * browser session, because client-side navigation never unloads a stylesheet. An unanchored
	 * `:global(.scroll-root) { max-block-size: 60vh }` in one screen is a rule about EVERY
	 * scrolling box, and pointing at that screen takes a band off the bottom of every other one.
	 *
	 * So: the bottom of the scrolling body, before and after pointing at every screen in the rail.
	 * Measured rather than reasoned about, because the mechanism is invisible in any one
	 * stylesheet.
	 */
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })
		})
	);

	await page.goto('/browse');
	const body = page.locator('.frame-body');
	await expect(body).toBeVisible();

	const bottom = async () =>
		Math.round((await body.boundingBox())!.y + (await body.boundingBox())!.height);
	const before = await bottom();

	// Every destination in the rail, one after another, without leaving the page.
	for (const name of ['Theater', 'Organize', 'People', 'Collections', 'Downloads', 'Hidden']) {
		// Scoped to the rail for the reason the walk above gives: an entity page's tabs are links
		// wearing the same words.
		const link = page.locator('nav.rail').getByRole('link', { name, exact: true });
		if ((await link.count()) === 0) continue;
		await link.hover();
		// Long enough for the preload to fetch and for its stylesheet to be applied.
		await page.waitForTimeout(600);
		expect(
			await bottom(),
			`Pointing at ${name} changed the page behind it. Something in that route's stylesheet is ` +
				'written unanchored: a `:global` rule with no local selector in front of it reaches ' +
				'every screen, and preloading is enough to apply it.'
		).toBe(before);
	}
});

/*
 * THE TRAIL ON THE TOP BAR, AND ONE FIRST LINE FOR EVERY SCREEN.
 *
 * On a desk the breadcrumbs stand in the top bar, so a title with a trail stands where one without
 * does, and both where Theater's does. Where the screen's own row is drawn (the menus moved off the
 * top bar) the title stands lower by that row's height and nothing else, and it is not carried
 * down as the row arrives: the row is the menus' place at that width.
 */
test('a trail leaves the title exactly where an untrailed one and Theater sit', async ({
	page
}) => {
	await signInAsAdmin(page);

	/* The person has to be answered or the page draws "not found" and has no title to measure. The
	   rest of what that screen reads is left to the real server: empty answers still draw a header,
	   which is the only thing being measured. */
	await page.route('**/api/people/p1', (route) =>
		route.fulfill({
			json: {
				id: 'p1',
				name: 'Wren Halloway',
				vault: false,
				asset_count: 0,
				cover_asset_id: null,
				cover_track_id: null,
				favorite: false,
				rating: null,
				shared: false,
				restricted: false
			}
		})
	);

	/* Every line the title stands on, frame by frame from the first paint. */
	await page.addInitScript(() => {
		const lines: number[] = [];
		Object.assign(window, { titleLines: lines });
		const look = () => {
			const title = document.querySelector('main h1');
			const y = title ? Math.round(title.getBoundingClientRect().top) : null;
			if (y !== null && y !== lines.at(-1)) lines.push(y);
			requestAnimationFrame(look);
		};
		requestAnimationFrame(look);
	});

	/* Read in one frame once the layout is still: two boxes read apart can straddle a row arriving. */
	async function measure(path: string) {
		await page.goto(path);
		await page.getByRole('heading', { level: 1 }).first().waitFor();
		const at = await page.evaluate(async () => {
			const frame = () => new Promise<number>((done) => requestAnimationFrame(done));
			const row = document.querySelector('.screenbar')!;
			const top = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
			let still = 0;
			let last = Number.NaN;
			for (let i = 0; i < 600 && still < 12; i++) {
				await frame();
				const now = top('main h1').top;
				still = now === last && row.getAnimations({ subtree: true }).length === 0 ? still + 1 : 0;
				last = now;
			}
			return {
				title: top('main h1').top,
				main: top('main').top,
				row: row.getBoundingClientRect().height,
				lines: (window as unknown as { titleLines: number[] }).titleLines
			};
		});
		return {
			inMain: Math.round(at.title - at.main),
			fromTop: Math.round(at.title),
			row: Math.round(at.row),
			lines: at.lines,
			inBar: (await page.locator('header.topbar nav[aria-label="Breadcrumb"] a').count()) > 0,
			banded: (await page.locator('.frame-trail').count()) > 0
		};
	}

	/* 1400: only Theater's menus leave the top bar. 1024: every screen's do. */
	for (const [width, height] of [
		[1400, 900],
		[1024, 768]
	]) {
		await page.setViewportSize({ width, height });
		const bare = await measure('/people');
		expect(bare.inBar, 'a top-level wall drew a trail, which it has nowhere to go from').toBe(
			false
		);

		const trailed = await measure('/people/p1');
		/* At 1024 with the sidebar open the trail has folded to its press, which is not a link. */
		if (width === 1400)
			expect(trailed.inBar, 'a person page drew no trail on the top bar').toBe(true);
		expect(trailed.banded, 'a desk frame drew a trail band of its own').toBe(false);

		const theater = await measure('/theater');

		for (const [name, one] of Object.entries({ bare, trailed, theater })) {
			const seen = JSON.stringify({ width, bare, [name]: one });
			expect(
				Math.abs(one.inMain - bare.inMain),
				`${name}'s title is off the line: ${seen}`
			).toBeLessThanOrEqual(2);
			expect(
				Math.abs(one.fromTop - bare.fromTop - (one.row - bare.row)),
				`${name}'s title moved by more than the screen's row: ${seen}`
			).toBeLessThanOrEqual(2);
			/* The line before the menus' home is known, and the line under the row: never between. */
			expect(
				one.lines.length,
				`${name}'s title slid as the row came in: ${seen}`
			).toBeLessThanOrEqual(2);
		}
	}
});
