/*
 * The bar across the top is the same bar on every screen: its SHAPE never changes, only the STATE
 * of a control. Where a control lands is a question only a layout engine answers, so this is a
 * browser test, and every screen is visited because any one of them can publish a different bar.
 */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { SIZE_STEPS } from '../src/lib/grid/justify';
import { signInAsAdmin } from './admin';
import { settled } from './settled';

/*
 * Every screen with a wall on it, plus one with no wall at all.
 *
 * Settings is in the list on purpose and is the strictest case: it publishes NOTHING into the screen
 * bar, so everything on the row is running on the defaults. A bar that fell apart anywhere would
 * fall apart there first.
 */
const SCREENS = [
	'/browse',
	'/favorites',
	'/people',
	'/sites',
	'/tags',
	'/collections',
	'/recent',
	'/settings'
] as const;

/*
 * The heading each of those screens draws, so a reading can be tied to the screen it belongs to.
 *
 * The bar SURVIVES a navigation, so there is no moment where it is absent and nothing to wait for
 * appearing, which means a measurement taken straight after a `goto` can be the outgoing screen's
 * bar. Settling on "two readings agree" narrows that and does not close it: two readings of the
 * outgoing bar agree perfectly. Waiting for the incoming screen's own heading is a positive signal
 * that the screen under the bar has changed, which is the thing that was outstanding.
 */
/* Not anchored, and that is the icon's doing: a heading carries its screen's glyph, which is a real
   character in the font's private use area sitting in front of the word. */
const HEADINGS: Record<(typeof SCREENS)[number], RegExp> = {
	'/browse': /Browse/,
	'/favorites': /Favorites/,
	'/people': /People/,
	'/sites': /Sites/,
	'/tags': /Tags/,
	'/collections': /Collections/,
	'/recent': /Recently viewed/,
	/* Settings opens on a SECTION (the address redirects to the first one), so the heading under
	   the bar is that section's, not the word Settings. */
	'/settings': /Folders/
};

/**
 * Go to a screen and wait until it is really the one under the bar, in its real typeface.
 *
 * The fonts wait is the whole of the second half. These buttons are measured to the PIXEL, and
 * until the web font arrives their labels are drawn in the fallback (close enough that nothing
 * looks wrong and about three pixels narrower across "Sort by"), so the same bar measured a moment
 * apart gives different positions depending on how loaded the machine is.
 */
async function open(page: Page, screen: (typeof SCREENS)[number]) {
	await page.goto(screen);
	await expect(page.locator('h1').first()).toHaveText(HEADINGS[screen]);
	await page.evaluate(() => document.fonts.ready.then(() => undefined));
}

/*
 * The named menus, in order.
 *
 * Read off the row rather than off the whole bar: the actions at the other end hold the vault
 * control and Add, which are about the account rather than about the screen, and Add is admin-only
 * by design: a guest is refused all three routes behind it. Uniform ACROSS SCREENS is the claim
 * here, not uniform across accounts.
 */
async function menus(page: Page): Promise<{ name: string; x: number; off: boolean }[]> {
	/*
	 * Read twice, and only believed once two readings agree.
	 *
	 * This bar SURVIVES a navigation (it belongs to the layout, not to any screen), so there is
	 * no moment where it is absent and nothing to wait for appearing. Waiting for the buttons to be
	 * visible proves nothing at all: they were already visible, on the screen being left. Measured
	 * straight after a `goto`, what comes back can be the outgoing screen's bar, or the incoming
	 * one before the store it reads from has been republished into: a small difference that reads
	 * exactly like a real layout fault. A test that reports a fault that is not there costs more
	 * than one that reports nothing.
	 */
	let previous = '';
	for (let attempt = 0; attempt < 40; attempt += 1) {
		const now = JSON.stringify(await read(page));
		if (now === previous && now !== '[]') break;
		previous = now;
		await page.waitForTimeout(50);
	}
	return await read(page);
}

async function read(page: Page): Promise<{ name: string; x: number; off: boolean }[]> {
	const buttons = page.locator('[role="group"][aria-label="What is on screen"] button');
	await expect(buttons.first()).toBeVisible();
	// Measured from the bar's midpoint, which is the centre group's.
	const bar = (await page.locator('header.topbar').boundingBox())!;
	const middle = bar.x + bar.width / 2;

	const found: { name: string; x: number; off: boolean }[] = [];
	for (const button of await buttons.all()) {
		const box = (await button.boundingBox())!;
		found.push({
			/*
			 * The NAME, which is the accessible name and not the text.
			 *
			 * These are icon-only buttons: what is inside them is a LIGATURE, a real character of the
			 * button's text in the font's private use area, and taking the private-use characters out
			 * of the text leaves nothing at all. The name a screen reader announces is the one thing
			 * that survives the row being drawn as glyphs or as words, which is what this check is
			 * about: the same controls, in the same order, wherever somebody is standing.
			 */
			name: (await button.getAttribute('aria-label')) ?? '',
			x: Math.round(box.x - middle),
			off: await button.isDisabled()
		});
	}
	return found;
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	/* Wide enough that the menus are on the top bar rather than on the row below it. Below the
	   room they need they move down to the filter bar, which has the width to spare: one home
	   or the other, never both, because a hidden second copy is still in the tab order. That
	   room is read off the BAR rather than the window; see `screenBar.watchRoom`. */
	await page.setViewportSize({ width: 1440, height: 900 });
});

test('the same controls, in the same order, on every screen', async ({ page }) => {
	let expected: string[] | null = null;

	for (const screen of SCREENS) {
		await open(page, screen);
		const here = (await menus(page)).map((one) => one.name);

		expect(here, `${screen} has no menus at all`).toContain('Filter');
		expect(here, `${screen} is missing the order control`).toContain('Sort by');
		/* There is no kept-filters trigger: the kept filters are drawn at the foot of the Filter
		   panel, because reaching for a narrowing you kept is part of deciding how to narrow
		   rather than a separate errand. Asserted absent rather than dropped, so its return
		   would be a decision somebody took. */
		expect(here, `${screen} still draws the retired kept-filters menu`).not.toContain(
			'Saved Filters'
		);

		if (expected === null) expected = here;
		else expect(here, `the bar changed shape on ${screen}`).toEqual(expected);
	}
});

/*
 * How far a menu may sit from where it sits on the first screen.
 *
 * Not zero, and the reason is a measurement limit rather than a design one. These are text buttons
 * in a track sized to its own content, so their edges move by a pixel or two with the metrics in
 * force at the moment they are read: which font has arrived, and whether the body under them has
 * a scrollbar. A zero tolerance reports faults that are not there.
 *
 * Four pixels still catches everything this exists for: a control appearing or disappearing on one
 * screen slides the survivors by the WIDTH OF A BUTTON: a hundred pixels and more, never four.
 */
const SLACK = 4;

/** The centre group's midpoint less the bar's: the menus' first edge to the search box's last. */
async function offCentre(page: Page): Promise<number> {
	return page.evaluate(() => {
		const bar = document.querySelector('header.topbar')!.getBoundingClientRect();
		const group = document.querySelector('header.topbar .centre')!.getBoundingClientRect();
		return (group.left + group.right) / 2 - (bar.left + bar.right) / 2;
	});
}

/** Where the field's floor binds, how far the start end gives way: half of what the bar lacks. */
async function floorShortfall(page: Page): Promise<number> {
	return page.evaluate((floor) => {
		const bar = document.querySelector('header.topbar') as HTMLElement;
		const style = getComputedStyle(bar);
		const inner =
			bar.clientWidth - parseFloat(style.paddingInlineStart) - parseFloat(style.paddingInlineEnd);
		const end = parseFloat(style.getPropertyValue('--bar-end'));
		return Math.max(0, (floor + 2 * (end + parseFloat(style.columnGap)) - inner) / 2);
	}, 227);
}

test('the centre group stands on the middle of the bar at every width, the rail open or not', async ({
	page
}) => {
	await open(page, '/browse');
	for (const collapsed of [false, true]) {
		const toggle = page.locator(
			`header.topbar button[aria-label="${collapsed ? 'Collapse' : 'Expand'} the sidebar"]`
		);
		if ((await toggle.count()) > 0) await toggle.click();
		for (const width of [1024, 1152, 1280, 1440, 1600, 1920, 2560]) {
			await page.setViewportSize({ width, height: 900 });
			/* Polled: the rail's width is animated. */
			await expect
				.poll(async () => Math.abs(await offCentre(page)) - (await floorShortfall(page)), {
					message: `off centre at ${width}px with the rail ${collapsed ? 'collapsed' : 'open'}`
				})
				.toBeLessThanOrEqual(1);
		}
	}
	await page.locator('header.topbar button[aria-label="Expand the sidebar"]').click();
});

test('and in the same place, near enough to read as the same place', async ({ page }) => {
	/*
	 * The half that the names alone cannot catch: the same labels at different x positions. Read
	 * from the bar's midpoint, and the centre group's own midpoint is held to it on every screen.
	 */
	let expected: number[] | null = null;

	for (const screen of SCREENS) {
		await open(page, screen);
		const here = (await menus(page)).map((one) => one.x);
		expect(
			Math.abs(await offCentre(page)),
			`the centre group is off centre on ${screen}`
		).toBeLessThanOrEqual(1);
		if (expected === null) expected = here;
		else {
			expect(here, `the bar is a different shape on ${screen}`).toHaveLength(expected.length);
			for (const [at, x] of here.entries()) {
				expect(
					Math.abs(x - expected[at]),
					`the menus moved sideways on ${screen}: ${here.join(', ')} against ${expected.join(', ')}`
				).toBeLessThanOrEqual(SLACK);
			}
		}
	}
});

test('a control that cannot act is drawn refusing, not drawn missing', async ({ page }) => {
	/*
	 * Drawn is not the claim. Drawn AND visibly unavailable is.
	 *
	 * A Filter button on Settings that still opened a panel counting files would be worse than no
	 * button at all. So the check is the real `disabled` attribute, which is what stops the click,
	 * what a screen reader reads out, and what the dimming keys off. A test asserting the opacity
	 * would pass on a control that still worked.
	 */
	await page.goto('/settings');
	const off = new Map((await menus(page)).map((one) => [one.name, one.off]));

	expect(off.get('Filter'), 'Filter acts on a screen with no files on it').toBe(true);
	expect(off.get('Sort by'), 'Sort acts on a screen with nothing to order').toBe(true);
	/* The kept filters are kept across every screen, and they are inside the Filter panel, which
	   is dimmed on a screen with no query language, so on Settings there is nothing to reach
	   them WITH, and that is the trade. */
	expect(off.has('Saved Filters'), 'the retired trigger is back').toBe(false);

	await page.goto('/browse');
	const live = new Map((await menus(page)).map((one) => [one.name, one.off]));
	expect(live.get('Filter'), 'Filter is dead on a wall of files').toBe(false);
	expect(live.get('Sort by'), 'Sort is dead on a wall of files').toBe(false);
});

test('the size slider acts on a wall of cards, not only on a wall of files', async ({ page }) => {
	/*
	 * The slider is live on entity walls: a wall of cards has a size just as much as a wall of
	 * tiles does; what it does not have is a ROW HEIGHT, which is a fact about how the media grid
	 * lays out and not about whether these things can be bigger.
	 */
	const slider = page.locator('.size input[type="range"]');

	await page.goto('/people');
	await expect(slider).toBeEnabled();

	await page.goto('/tags');
	await expect(slider).toBeEnabled();

	await page.goto('/collections');
	await expect(slider).toBeEnabled();
});

test('a notch grows the cards, and the wall still fills its width', async ({ page }) => {
	/*
	 * The notch is a floor and the cards fill the row: each notch is bigger than the last, a card
	 * is never smaller than a media tile at the same notch, and the wall reaches the header's right
	 * edge. Seeded with more cards than the widest row holds, or a partial row would pass.
	 */
	const me = await page.request.get('/api/auth/me');
	const csrf = (await me.json()).csrf_token;
	const stamp = Date.now();
	for (let at = 0; at < 24; at += 1) {
		await page.request.post('/api/people', {
			data: { name: `e2e-notch-${stamp}-${at}`, vault: false },
			headers: { 'x-csrf-token': csrf }
		});
	}

	/** Every size measured at the widest window, so the four notches can be told apart. */
	const wide: number[] = [];

	for (const width of [1024, 1440, 2560]) {
		await page.setViewportSize({ width, height: 900 });
		let previous = 0;
		const seen: number[] = [];

		/* The ladder itself, not a copy of it: a copy goes stale when the sizes move, and a
		   notch it invents falls back to the automatic size, which reads as a notch that shrank. */
		for (const notch of SIZE_STEPS) {
			await page.goto('/people');
			await page.evaluate((value) => {
				localStorage.setItem('sift.grid.rowHeight', String(value));
			}, notch);
			await page.goto('/people');

			const face = page.locator('.card .face').first();
			await expect(face).toBeVisible();
			const box = (await face.boundingBox())!;

			expect(
				box.height,
				`at ${width}px, notch ${notch} drew a picture ${Math.round(box.height)} tall: smaller than a media tile at the same notch`
			).toBeGreaterThanOrEqual(notch);

			/*
			 * Never SMALLER than the notch below it, and deliberately not "always bigger".
			 *
			 * `auto-fill` fits a whole number of columns, so on a narrow window there are only two
			 * or three to be had and four notches cannot all land on different ones: at 1024, notch
			 * 180 and notch 260 both resolve to two columns and draw at the same size, to within a
			 * pixel of rounding.
			 *
			 * That the four are all DIFFERENT is asserted below, at a width with room for it.
			 */
			expect(
				box.height,
				`at ${width}px, notch ${notch} (${Math.round(box.height)}) came out smaller than the notch below it (${Math.round(previous)})`
			).toBeGreaterThanOrEqual(previous - 1);
			previous = box.height;
			seen.push(Math.round(box.height));

			/*
			 * No band of empty ground down the right of the wall.
			 *
			 * The FURTHEST-right card of all of them, not the last one in the list: the last card is
			 * wherever the final partial row happens to end, which says nothing. What must reach the
			 * wall's own right edge is the rightmost column, and with the cards stretching to fill their
			 * tracks that is exact.
			 */
			const wall = (await page.locator('.wall').first().boundingBox())!;
			const edges = await page
				.locator('.card')
				.evaluateAll((cards) => cards.map((one) => one.getBoundingClientRect().right));
			const shortfall = wall.x + wall.width - Math.max(...edges);
			expect(
				shortfall,
				`at ${width}px, notch ${notch} left ${Math.round(shortfall)}px of empty ground down the right of the wall`
			).toBeLessThan(4);
		}

		if (width === 2560) wide.push(...seen);
	}

	/* On a window with room, the four notches are four different sizes. Without this the check above
	   would pass on a slider that did nothing at all: every notch equal to the one below it is
	   "never smaller" for all four. */
	expect(new Set(wide).size, `the four notches drew ${wide.join(', ')} on a wide window`).toBe(4);
});

test('the hover lift is not clipped by the top of the scrolling body', async ({ page }) => {
	/*
	 * A card lifts `translateY(-2px)` under the cursor: the Lift register for anything
	 * picture-shaped. The scrolling body is `overflow: hidden scroll`, so a wall that began at
	 * EXACTLY its top edge would have the top row's lift cut off: only the top row, only on
	 * hover. So the wall starts with room above it.
	 *
	 * The media grid never shows it, because a tile scales the PICTURE inside its own frame and
	 * nothing ever leaves the box.
	 */
	const me = await page.request.get('/api/auth/me');
	const csrf = (await me.json()).csrf_token;
	for (let at = 0; at < 6; at += 1) {
		await page.request.post('/api/people', {
			data: { name: `e2e-lift-${Date.now()}-${at}`, vault: false },
			headers: { 'x-csrf-token': csrf }
		});
	}

	await page.goto('/people');
	const card = page.locator('.card').first();
	await expect(card).toBeVisible();

	/*
	 * The size notch is SET rather than inherited, and that is what makes this test mean anything.
	 *
	 * It is a per-account preference and every spec in the run shares one admin, so whatever notch
	 * the last test left is the notch this one measures at, including the two tests above, which
	 * move the slider on purpose. The clearance over the scroller is four pixels at some sizes and
	 * two at others, so the same code passed or failed depending on which test ran before it, in a
	 * run where they are shuffled across four workers.
	 */
	const slider = page.locator('.size input[type="range"]');
	await slider.fill('1');
	await page.waitForTimeout(300);

	const scroller = (await page.locator('.frame-body').first().boundingBox())!;
	await card.hover();
	await page.waitForTimeout(400);
	const lifted = (await card.boundingBox())!;

	expect(
		lifted.y,
		`the lifted card starts at ${lifted.y.toFixed(1)} and the scroller clips everything above ${scroller.y.toFixed(1)}`
	).toBeGreaterThanOrEqual(scroller.y);
});

test('every wall offers the same four orders the file grid does', async ({ page }) => {
	/*
	 * One vocabulary. Newest, Oldest and the two name directions apply to every kind of thing in
	 * the library, so they are offered on every wall, under the same words and against the same
	 * server keys (taken out of the grid's own list rather than written again beside it), so the
	 * Sort menu on one screen teaches you the Sort menu on the next.
	 */
	const UNIVERSAL = ['Newest first', 'Oldest first', 'Name A-Z', 'Name Z-A'];

	/*
	 * THE SIZE PAIR IS THE SAME QUESTION UNDER TWO NAMES.
	 *
	 * One vague word covering "most seen", "most used" and "most items" would leave no wall saying
	 * what it sorts BY, and the walls do not measure the same thing: these four count FILES; the
	 * grid counts BYTES.
	 *
	 * So the key stays one (`largest`) and the WORD is exact on each: `COUNTED_INSTEAD` in
	 * `sort-state.svelte.ts` gives the counting walls their own pair, and the grid keeps the file
	 * one. Written here as one entry per wall rather than as a shared string, because the claim
	 * this test makes (one vocabulary) is about the four above; the fifth pair is deliberately
	 * two vocabularies for two measurements, and a test that hid that would be asserting the drift.
	 */
	const SIZE_PAIR: Record<string, readonly string[]> = {
		'/browse': ['Largest file', 'Smallest file'],
		'/people': [
			'Most files',
			'Fewest files',
			'Largest in total',
			'Smallest in total',
			'Longest in total',
			'Shortest in total'
		],
		'/sites': [
			'Most files',
			'Fewest files',
			'Largest in total',
			'Smallest in total',
			'Longest in total',
			'Shortest in total'
		],
		'/tags': [
			'Most files',
			'Fewest files',
			'Largest in total',
			'Smallest in total',
			'Longest in total',
			'Shortest in total'
		],
		'/collections': [
			'Most files',
			'Fewest files',
			'Largest in total',
			'Smallest in total',
			'Longest in total',
			'Shortest in total'
		]
	};
	const OPINIONS = ['Favorites first', 'Highest rated'];

	const walls = ['/browse', '/people', '/sites', '/tags', '/collections'] as const;
	for (const screen of walls) {
		await open(page, screen);

		const sort = page.getByRole('button', { name: /Sort by/ });
		await expect(sort).toBeEnabled();
		await sort.click();

		for (const order of [...UNIVERSAL, ...SIZE_PAIR[screen], ...OPINIONS]) {
			/* An OPTION: the orders are a list in the app's own chooser, so the role is the
			   listbox's. Tolerating another role is what lets a control quietly become a third
			   thing. */
			await expect(
				page.getByRole('option', { name: order }),
				`${screen} does not offer ${order}`
			).toHaveCount(1);
		}

		await page.keyboard.press('Escape');
	}
});

/*
 * THE BAR IS THE TITLE BAR, AND A BROWSER CANNOT SHOW YOU THAT.
 *
 * In the packaged window there is no operating-system title bar: the minimise, maximise and close
 * are overlaid on this row, and the bar gives up about 138px of its inline end to them. A browser
 * tab has no such buttons, so a measurement of this bar in a browser is of a bar 138px wider than
 * the one that ships, and a viewport rule such as `(min-width: 880px)` for whether the screen
 * menus fit is wrong for the same reason.
 *
 * At 1024 CSS pixels (half of a 2560px monitor at 125% scaling) the search field needs 227px for
 * one row: two mode buttons, eight characters of typing room and the `Ctrl+F` cap. Given less,
 * the cap wraps and the field grows taller than the bar and hangs over the row below with nothing
 * typed in it.
 *
 * The padding is put on by hand here because that is the one thing about the packaged window a
 * browser cannot be made to have. Everything else (the rail, the grid, the field's contents) is
 * the real thing.
 */
/** The three caption buttons, plus the bar's own end padding behind them. */
const CAPTION_BUTTONS = 154;

test('the search field never hangs over the row below with nothing typed in it', async ({
	page
}) => {
	await page.goto('/browse');
	const bar = page.locator('header.topbar');
	const field = page.locator('header.topbar .search');
	const size = page.locator('header.topbar .size');
	await expect(field).toBeVisible();

	await page.addStyleTag({
		content: `header.topbar { padding-inline-end: ${CAPTION_BUTTONS}px !important; }`
	});

	/* 960 is the width the desktop window refuses to go below, 1024 is half of a wide monitor,
	   and 1060 is where the rail is open and the squeeze is worst. */
	for (const width of [1440, 1200, 1100, 1060, 1024, 1000, 960]) {
		await page.setViewportSize({ width, height: 900 });
		await expect
			.poll(
				async () => {
					const [inner, outer] = [await field.boundingBox(), await bar.boundingBox()];
					if (!inner || !outer) return null;
					return Math.round(inner.y + inner.height - (outer.y + outer.height));
				},
				{ message: `the search field hangs below the bar at ${width}px` }
			)
			.toBeLessThanOrEqual(0);

		/*
		 * AND IT KEEPS ITS HINT, which is the half that pins the threshold.
		 *
		 * Without this the check above passes with the menus' threshold at anything at all: the
		 * field's own container query would drop the hint and the box would stay one row, so the
		 * second guard would quietly cover for the first being wrong. What the threshold buys is the
		 * field never being squeezed that far in the first place (227px is one row WITH the hint on
		 * it), and this is the only thing that says so.
		 */
		/* Polled, like the reading above it: the rail's width is animated, so a single measurement
		   taken straight after a resize can be one caught mid-flight. */
		await expect
			.poll(async () => Math.round((await field.boundingBox())!.width), {
				message: `the menus stayed on the bar and squeezed the field at ${width}px`
			})
			.toBeGreaterThanOrEqual(227);
		if (width === 1440) await expect(size, 'no tile size with room for it').toBeVisible();
		if (width === 1024) await expect(size, 'the tile size kept its place at 1024px').toBeHidden();
	}
});

test('a field too narrow for the keyboard hint drops the hint, not the row', async ({ page }) => {
	/*
	 * THE SECOND GUARD, ON ITS OWN.
	 *
	 * The test above proves the squeeze does not happen at any width the window can be. This one
	 * proves what happens if it ever does: a control added to the row, a wider rail, a longer
	 * shortcut. The field's own container query drops the hint rather than growing a row for it.
	 *
	 * The field's width is given by hand, and that is the INPUT rather than the measurement: what is
	 * asserted is the field's height and whether the hint is drawn, neither of which is being set
	 * here. It is the only way to ask for a field of a given width without also asking for a window
	 * this application would not open at.
	 */
	await page.goto('/browse');
	const field = page.locator('header.topbar .search');
	const hint = page.locator('header.topbar .search .shortcut');

	// A KNOWN POSITIVE. Without it a hint that never drew at all would pass everything below.
	await expect(hint).toBeVisible();
	const oneRow = (await field.boundingBox())?.height;
	expect(oneRow, 'the field should be one row to begin with').toBeLessThan(60);

	const track = (px: number) =>
		page.addStyleTag({
			content: `header.topbar .centre > .wrap { inline-size: ${px}px !important; }`
		});

	/*
	 * The claim is a PROPERTY, not a boundary: squeeze this field as far as you like and it never
	 * becomes two rows. Pinning the exact pixel the hint goes at would pin an implementation detail,
	 * and it is a detail that is easy to get wrong in the safe direction, because a container
	 * query asks about the CONTENT box and this field has 9px of padding and border on each side.
	 * Written the wrong way round the hint goes 18px early, nothing wraps, and no assertion about a
	 * single width would ever notice.
	 */
	await track(300);
	await expect(hint, 'there is room for the hint at 300px').toBeVisible();

	for (const width of [260, 240, 228, 220, 200, 180, 160]) {
		await track(width);
		expect(
			(await field.boundingBox())?.height,
			`the field grew a row instead of dropping the hint at ${width}px`
		).toBe(oneRow);
	}

	// And it really did go, rather than the row having had room all along.
	await expect(hint).toBeHidden();
});

test('every row of the search list lies inside the bar, its words whole', async ({ page }) => {
	await page.setViewportSize({ width: 1024, height: 900 });
	await page.goto('/browse');
	await page.getByRole('combobox', { name: 'Search' }).first().click();
	const rows = page.locator('#search-suggestions [role=option]');
	const network = rows.filter({ hasText: /network/i });
	for (let presses = 0; presses < 10 && (await network.count()) === 0; presses += 1) {
		await rows
			.filter({ hasText: /Show \d+ more/ })
			.last()
			.click();
	}
	await expect(network.first(), 'Show more never reached the Network row').toBeVisible();
	// The bar and the list measured once nothing on them is still moving: on four slow cores the
	// list was read against a bar part way through its entrance, every row "past" it.
	await settled(page.locator('header.topbar'));
	await settled(page.locator('#search-suggestions'));

	const outside = await page.evaluate(() => {
		const bar = document.querySelector('header.topbar')!;
		const box = bar.getBoundingClientRect();
		const style = getComputedStyle(bar);
		const start = box.left + parseFloat(style.paddingLeft);
		const end = box.right - parseFloat(style.paddingRight);
		return [...document.querySelectorAll('#search-suggestions [role=option]')].flatMap((row) => {
			const at = row.getBoundingClientRect();
			const cut = [...row.querySelectorAll('span')].filter(
				(one) => one.clientWidth > 0 && one.scrollWidth > one.clientWidth
			);
			const out = at.left < start - 0.5 || at.right > end + 0.5;
			return out || cut.length > 0 ? [`${row.textContent?.trim()} (${cut.length} cut)`] : [];
		});
	});
	expect(outside, 'rows past the bar or with their words cut').toEqual([]);
});

/* As the bar narrows the tile size leaves before the menus do, so at 1024 with the rail open Filter
 * and Sort by still stand on the bar, centred, on a wall and on an entity page alike; at 1440 the
 * tile size is back (at 1280 an admin's Add with its paste half still leaves it just short). */
test('the tile size leaves the bar before Filter and Sort by do', async ({ page }) => {
	await page.setViewportSize({ width: 1024, height: 800 });
	for (const screen of ['/browse', '/people'] as const) {
		await page.goto(screen);
		await settled(page.locator('header.topbar'));
		const menus = page.locator('header.topbar .centre .menus');
		await expect(menus, `${screen}: the menus left the bar at 1024`).toBeVisible();
		await expect(page.locator('.bar-fold .menus')).toHaveCount(0);
		const [bar, centre] = await Promise.all([
			page.locator('header.topbar').boundingBox(),
			page.locator('header.topbar .centre').boundingBox()
		]);
		if (!bar || !centre) throw new Error(`${screen}: the bar or its centre has no box`);
		expect(Math.abs(bar.x + bar.width / 2 - (centre.x + centre.width / 2))).toBeLessThan(
			0.5 + SLACK
		);
	}
	await page.setViewportSize({ width: 1440, height: 800 });
	await page.goto('/browse');
	await settled(page.locator('header.topbar'));
	await expect(page.locator('header.topbar .size:not(.gone)')).toBeVisible();
});
