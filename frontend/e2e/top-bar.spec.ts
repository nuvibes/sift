/* The top bar keeps its shape on every screen; only a control's state changes. Every screen is
 * visited, since any one of them can publish a different bar. */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { SIZE_STEPS } from '../src/lib/grid/justify';
import { signInAsAdmin } from './admin';
import { settled } from './settled';

// Settings publishes nothing into the bar, so it is the strictest case: all defaults.
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

/* Each screen's heading: the bar survives a navigation, so the incoming heading is the signal.
 * Not anchored: a heading starts with its screen's icon glyph. */
const HEADINGS: Record<(typeof SCREENS)[number], RegExp> = {
	'/browse': /Browse/,
	'/favorites': /Favorites/,
	'/people': /People/,
	'/sites': /Sites/,
	'/tags': /Tags/,
	'/collections': /Collections/,
	'/recent': /Recently viewed/,
	// Settings redirects to its first section, so that is the heading.
	'/settings': /Folders/
};

/** Open a screen and wait for its heading and the web font: the fallback is narrower. */
async function open(page: Page, screen: (typeof SCREENS)[number]) {
	await page.goto(screen);
	await expect(page.locator('h1').first()).toHaveText(HEADINGS[screen]);
	await page.evaluate(() => document.fonts.ready.then(() => undefined));
}

/* The named menus in order, from the row only: the account's actions at the far end differ
 * by account by design. */
async function menus(page: Page): Promise<{ name: string; x: number; off: boolean }[]> {
	// Believed only once two readings agree: the bar can still be the outgoing screen's.
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
	const bar = (await page.locator('header.topbar').boundingBox())!;
	const middle = bar.x + bar.width / 2;

	const found: { name: string; x: number; off: boolean }[] = [];
	for (const button of await buttons.all()) {
		const box = (await button.boundingBox())!;
		found.push({
			// The accessible name: the button's text is an icon ligature.
			name: (await button.getAttribute('aria-label')) ?? '',
			x: Math.round(box.x - middle),
			off: await button.isDisabled()
		});
	}
	return found;
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	// Wide enough that the menus sit on the top bar, not on the filter bar below.
	await page.setViewportSize({ width: 1440, height: 900 });
});

test('the same controls, in the same order, on every screen', async ({ page }) => {
	let expected: string[] | null = null;

	for (const screen of SCREENS) {
		await open(page, screen);
		const here = (await menus(page)).map((one) => one.name);

		expect(here, `${screen} has no menus at all`).toContain('Filter');
		expect(here, `${screen} is missing the order control`).toContain('Sort by');
		// The kept filters live in the Filter panel; asserted absent so a return is deliberate.
		expect(here, `${screen} still draws the retired kept-filters menu`).not.toContain(
			'Saved Filters'
		);

		if (expected === null) expected = here;
		else expect(here, `the bar changed shape on ${screen}`).toEqual(expected);
	}
});

/* A text button's edges move a pixel or two with fonts and scrollbars; a control appearing
 * slides the rest by a whole button's width. */
const SLACK = 4;

/** The centre group's midpoint less the bar's. */
async function offCentre(page: Page): Promise<number> {
	return page.evaluate(() => {
		const bar = document.querySelector('header.topbar')!.getBoundingClientRect();
		const group = document.querySelector('header.topbar .centre')!.getBoundingClientRect();
		return (group.left + group.right) / 2 - (bar.left + bar.right) / 2;
	});
}

/** Where the field's floor binds, how far the start end gives way. */
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
	// The same labels at the same x, centred on the bar's midpoint on every screen.
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
	// Disabled, not merely dimmed: the real attribute is what stops the click.
	await page.goto('/settings');
	const off = new Map((await menus(page)).map((one) => [one.name, one.off]));

	expect(off.get('Filter'), 'Filter acts on a screen with no files on it').toBe(true);
	expect(off.get('Sort by'), 'Sort acts on a screen with nothing to order').toBe(true);
	expect(off.has('Saved Filters'), 'the retired trigger is back').toBe(false);

	await page.goto('/browse');
	const live = new Map((await menus(page)).map((one) => [one.name, one.off]));
	expect(live.get('Filter'), 'Filter is dead on a wall of files').toBe(false);
	expect(live.get('Sort by'), 'Sort is dead on a wall of files').toBe(false);
});

test('the size slider acts on a wall of cards, not only on a wall of files', async ({ page }) => {
	// Entity walls have a size too, just no row height.
	const slider = page.locator('.size input[type="range"]');

	await page.goto('/people');
	await expect(slider).toBeEnabled();

	await page.goto('/tags');
	await expect(slider).toBeEnabled();

	await page.goto('/collections');
	await expect(slider).toBeEnabled();
});

test('a notch grows the cards, and the wall still fills its width', async ({ page }) => {
	/* Each notch is bigger, never smaller than a media tile at the same notch, and the wall
	 * reaches the header's edge. More cards than a row holds, or a partial row would pass. */
	const me = await page.request.get('/api/auth/me');
	const csrf = (await me.json()).csrf_token;
	const stamp = Date.now();
	for (let at = 0; at < 24; at += 1) {
		await page.request.post('/api/people', {
			data: { name: `e2e-notch-${stamp}-${at}`, vault: false },
			headers: { 'x-csrf-token': csrf }
		});
	}

	const wide: number[] = [];

	for (const width of [1024, 1440, 2560]) {
		await page.setViewportSize({ width, height: 900 });
		let previous = 0;
		const seen: number[] = [];

		// The ladder itself: a notch off it falls back to automatic.
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

			// Never smaller, not always bigger: at 1024 two notches can share a column count.
			expect(
				box.height,
				`at ${width}px, notch ${notch} (${Math.round(box.height)}) came out smaller than the notch below it (${Math.round(previous)})`
			).toBeGreaterThanOrEqual(previous - 1);
			previous = box.height;
			seen.push(Math.round(box.height));

			// The rightmost card, not the last one, reaches the wall's right edge.
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

	// Four sizes at a wide window, or a slider that did nothing would pass above.
	expect(new Set(wide).size, `the four notches drew ${wide.join(', ')} on a wide window`).toBe(4);
});

test('the hover lift is not clipped by the top of the scrolling body', async ({ page }) => {
	// Room above the wall, or the scroller clips the top row's hover lift.
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

	// The notch is set, not inherited: it is a shared admin's preference other tests move.
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
	// One sort vocabulary on every wall, read from the grid's own list.
	const UNIVERSAL = ['Newest first', 'Oldest first', 'Name A-Z', 'Name Z-A'];

	/* Walls that count files say so; the grid sorts by bytes. One key (`largest`), an exact word
	 * per wall from `COUNTED_INSTEAD` in `sort-state.svelte.ts`. */
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
			// An option of the app's listbox, and no other role.
			await expect(
				page.getByRole('option', { name: order }),
				`${screen} does not offer ${order}`
			).toHaveCount(1);
		}

		await page.keyboard.press('Escape');
	}
});

/* The packaged window draws its caption buttons over this bar's end, so that room is
 * padded by hand here; at 1024 the search field needs 227px for one row. */
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

	// 960 is the desktop window's floor; 1060 is the worst squeeze with the rail open.
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

		/* The hint stays, which pins the menus' threshold rather than letting the field's own query
		 * cover for it. Polled: the rail's width animates. */
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
	// The second guard alone: a field squeezed by hand drops its hint rather than wrapping.
	await page.goto('/browse');
	const field = page.locator('header.topbar .search');
	const hint = page.locator('header.topbar .search .shortcut');

	// A known positive.
	await expect(hint).toBeVisible();
	const oneRow = (await field.boundingBox())?.height;
	expect(oneRow, 'the field should be one row to begin with').toBeLessThan(60);

	const track = (px: number) =>
		page.addStyleTag({
			content: `header.topbar .centre > .wrap { inline-size: ${px}px !important; }`
		});

	// A property, not a boundary: a container query measures the content box, not the padding.
	await track(300);
	await expect(hint, 'there is room for the hint at 300px').toBeVisible();

	for (const width of [260, 240, 228, 220, 200, 180, 160]) {
		await track(width);
		expect(
			(await field.boundingBox())?.height,
			`the field grew a row instead of dropping the hint at ${width}px`
		).toBe(oneRow);
	}

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
	// Measured once settled: a bar mid-entrance puts every row "past" it.
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

/* The tile size leaves the bar before Filter and Sort by do, on a wall and an entity page. */
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
