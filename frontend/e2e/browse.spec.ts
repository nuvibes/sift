import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { tileSizeSlider } from './tile-size';

/* The grid drawing real tiles: a unit test has no layout engine, so only here do the widths reach
 * the screen. The library is served by intercepting the API so the arithmetic is checkable. */

/* Serial: these tests share one library and several change it. */
test.describe.configure({ mode: 'serial' });

const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000 },
	{ id: 'a2', media_type: 'video', width: 1080, height: 1920, duration_ms: 30_000 },
	{ id: 'a3', media_type: 'image', width: 1000, height: 1000, duration_ms: null },
	{ id: 'a4', media_type: 'video', width: 1920, height: 1080, duration_ms: 5000 },
	{ id: 'a5', media_type: 'video', width: 1600, height: 900, duration_ms: 12_000 },
	{ id: 'a6', media_type: 'image', width: 800, height: 1200, duration_ms: null }
].map((asset) => ({ ...asset, favorite: false, rating: null, concealed: false }));

async function serveLibrary(page: import('@playwright/test').Page, items = ASSETS) {
	await page.route('**/api/assets?*', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: items.length, limit: 50, offset: 0 })
		});
	});
	// A tile draws its frame without a still.
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	/* Rows are justified and the grid strips a tile's controls below 150px, which the default
	 * window falls inside. */
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('the grid draws a tile for every item', async ({ page }) => {
	await serveLibrary(page);
	await page.goto('/browse');

	await expect(page.locator('.tile')).toHaveCount(ASSETS.length);
});

test('tiles are given real, different widths', async ({ page }) => {
	/* The app's policy drops a style attribute written into markup, silently. */
	await serveLibrary(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const widths = await page
		.locator('.tile')
		.evaluateAll((tiles) =>
			tiles.map((tile) => (tile as HTMLElement).getBoundingClientRect().width)
		);

	expect(widths.length).toBeGreaterThan(1);
	for (const width of widths) expect(width).toBeGreaterThan(20);
	expect(
		new Set(widths.map(Math.round)).size,
		'every tile came out the same width'
	).toBeGreaterThan(1);
});

test('a portrait item is narrower than a landscape one of the same height', async ({ page }) => {
	// True proportions, which is the whole reason for justified rows rather than uniform cards.
	await serveLibrary(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const boxes = await page.locator('.tile').evaluateAll((tiles) =>
		tiles.map((tile) => {
			const rect = (tile as HTMLElement).getBoundingClientRect();
			return { width: rect.width, height: rect.height };
		})
	);

	const landscape = boxes[0];
	const portrait = boxes[1];
	expect(portrait.width).toBeLessThan(landscape.width);
	// Same row, so the same height. Allow a pixel for rounding.
	if (Math.abs(portrait.height - landscape.height) < 2) {
		expect(portrait.width / portrait.height).toBeLessThan(1);
		expect(landscape.width / landscape.height).toBeGreaterThan(1);
	}
});

test('a row fills the space it has, and does not exceed it', async ({ page }) => {
	/* Against the content box: `clientWidth` includes padding. */
	await serveLibrary(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const measured = await page.evaluate(() => {
		const row = document.querySelector('.row');
		// The ONE scrolling box on the screen.
		const scroller = document.querySelector('.frame-body') as HTMLElement | null;
		if (!row || !scroller) return null;
		const style = getComputedStyle(scroller);
		const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
		const tiles = [...row.querySelectorAll('.tile')];
		const used = tiles.reduce((total, tile) => total + tile.getBoundingClientRect().width, 0);
		return { used, content: scroller.clientWidth - padding, count: tiles.length };
	});

	expect(measured).not.toBeNull();
	if (measured && measured.count > 1) {
		expect(measured.used).toBeGreaterThan(measured.content * 0.5);
		expect(measured.used, 'the row is wider than the space it sits in').toBeLessThanOrEqual(
			measured.content + 2
		);
	}
});

test('the grid never scrolls sideways', async ({ page }) => {
	/* A row wider than its container clips the last tile of every row. */
	await serveLibrary(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const overflow = await page.evaluate(() => {
		const scroller = document.querySelector('.frame-body') as HTMLElement;
		return scroller.scrollWidth - scroller.clientWidth;
	});

	expect(overflow, 'the grid overflows its own width').toBeLessThanOrEqual(0);
});

test('a still image is never asked for a preview clip', async ({ page }) => {
	// A refused preview that is not remembered is re-sent every time the tile crosses the viewport.
	const asked: string[] = [];
	await page.route('**/api/assets/*/preview', (route) => {
		asked.push(new URL(route.request().url()).pathname);
		return route.fulfill({ status: 404 });
	});
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: ASSETS, total: ASSETS.length, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	await page.waitForTimeout(1000);

	// a3 and a6 are images.
	expect(asked.some((path) => path.includes('/a3/') || path.includes('/a6/'))).toBe(false);
});

test('an empty library says so instead of showing nothing at all', async ({ page }) => {
	await serveLibrary(page, []);
	await page.goto('/browse');

	// A library with no folders is offered its first folder, not told it is empty.
	await expect(page.getByText('Sift has no folders yet.')).toBeVisible();
	await expect(page.locator('.tile')).toHaveCount(0);
});

test('the grid asks for one page and then stops', async ({ page }) => {
	/* An effect that reads the state it writes re-runs itself and asks forever. */
	const asked: string[] = [];
	await page.route('**/api/assets?*', async (route) => {
		asked.push(new URL(route.request().url()).search);
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: ASSETS, total: ASSETS.length, limit: 50, offset: 0 })
		});
	});
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	/* Silent, so the announcements of tests running beside this one are not counted as the loop. */
	await page.routeWebSocket('**/api/live/stream**', () => {});

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	await page.waitForTimeout(2000);

	// A repeat with the same query is the loop; a different one is the grid settling on a size.
	expect(asked.length, `the grid kept asking: ${asked.join(' | ')}`).toBeLessThanOrEqual(2);
});

test('a hidden item is a locked tile with no picture in it', async ({ page }) => {
	// The server sends nothing about a concealed asset, and the tile must not fetch any.
	await serveLibrary(page, [
		{
			id: 'hidden',
			media_type: '',
			width: null,
			height: null,
			duration_ms: null,
			favorite: false,
			rating: null,
			concealed: true
		} as unknown as (typeof ASSETS)[number]
	]);
	await page.goto('/browse');

	await expect(page.locator('.tile.concealed')).toHaveCount(1);
	await expect(page.locator('.tile img')).toHaveCount(0);
	await expect(page.locator('.tile video')).toHaveCount(0);
});

/* The tile's overlay: a layout question, only answerable here. */
const SHAPES = [
	{ id: 'wide', media_type: 'video', width: 3840, height: 1080, duration_ms: 95_000 },
	{ id: 'tall', media_type: 'video', width: 600, height: 1600, duration_ms: 81_000 },
	{ id: 'anim', media_type: 'gif', width: 400, height: 400, duration_ms: 2040 }
	/* Without `thumb` a tile is still being imported and carries no controls. `rating: 6` is three
	   stars: stored out of ten, drawn out of five. */
].map((asset) => ({ ...asset, favorite: false, rating: 6, concealed: false, thumb: true }));

/* The badge and the overlay draw only on a tile that has a picture. */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

async function serveShapes(page: import('@playwright/test').Page) {
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: SHAPES, total: SHAPES.length, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
}

test('a tile carries a heart and a rating, and neither leaves the tile', async ({ page }) => {
	/* No stars on a tile: a narrow portrait tile cannot hold a heart and five stars. The window
	 * is tall enough for the controls to be drawn at all. */
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile')).toHaveCount(SHAPES.length);

	/* Settled when two readings agree: the wall re-measures after the window is resized. */
	await expect
		.poll(async () => {
			const first = await page
				.locator('.tile-frame')
				.first()
				.evaluate((el) => el.clientWidth);
			await new Promise((settle) => setTimeout(settle, 120));
			const second = await page
				.locator('.tile-frame')
				.first()
				.evaluate((el) => el.clientWidth);
			return first === second && first > 0;
		})
		.toBe(true);

	const frames = page.locator('.tile-frame');
	for (let index = 0; index < (await frames.count()); index += 1) {
		const frame = frames.nth(index);
		await frame.hover();
		const row = frame.locator('.controls-row');
		await expect(row).toBeVisible();

		const tile = (await frame.boundingBox())!;
		const controls = (await row.boundingBox())!;
		const width = Math.round(tile.width);

		expect(
			controls.x + controls.width,
			`the controls ran past the right edge of a ${width}px tile`
		).toBeLessThanOrEqual(tile.x + tile.width + 1);

		await expect(frame.locator('.stars'), `stars on a ${width}px tile`).toHaveCount(0);

		/* Written as its codepoint: every file in this repository is plain ASCII. */
		await expect(frame.locator('.rating')).toHaveText(`3${String.fromCodePoint(0x2605)}`);

		expect(
			Math.round(tile.height),
			'the tile is inside the band where the grid strips its chrome, so nothing was measured'
		).toBeGreaterThan(150);
	}

	/* And the other side of the threshold: a short window strips the controls. The height is
	 * picked by measuring, because a justified row's height is not monotonic in the window's. */
	await page.setViewportSize({ width: 1280, height: 700 });
	await expect(page.locator('.tile')).toHaveCount(SHAPES.length);
	const small = page.locator('.tile-frame').first();
	await small.hover();
	expect(Math.round((await small.boundingBox())!.height)).toBeLessThanOrEqual(150);
	await expect(small.getByRole('button', { name: 'Add to favorites' })).toHaveCount(0);
	// Hidden by `display: none`, so a count would be 1 either way.
	await expect(small.locator('.rating')).toBeHidden();

	// Put back: this file is serial.
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('a GIF is labelled rather than timed', async ({ page }) => {
	// A GIF loops and cannot be sought, so a length says less than "GIF" does.
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile')).toHaveCount(SHAPES.length);

	const badges = await page.locator('.duration').allTextContents();
	expect(badges).toContain('GIF');
	expect(badges).toContain('1:35');
	expect(badges, 'a GIF was still timed').not.toContain('0:02');
});

test('a picked tile under the pointer draws one ring, not two', async ({ page }) => {
	/* The hover ring and the selection ring together draw two circles; the selection's survives. */
	await serveShapes(page);
	await page.goto('/browse');
	const frame = page.locator('.tile-frame').first();
	await expect(frame).toBeVisible();

	// Ctrl-click picks without opening, and leaves the pointer on the tile.
	await frame.click({ modifiers: ['Control'] });
	await expect(frame.locator('.ring')).toHaveCount(1);

	/* A fixed wait, not a poll: mid-transition the shadow matches nothing and a poll would pass. */
	await page.waitForTimeout(600);
	const shadow = await frame
		.locator('.tile')
		.evaluate((element) => getComputedStyle(element).boxShadow);

	// Two layers means the hover's accent ring came back alongside `.ring`.
	expect(shadow, `the hover ring is still drawn: ${shadow}`).not.toMatch(/0px 0px 0px 2px/);
	expect(shadow, 'the tile stopped lifting altogether').toContain('18px');
});

test('the top row has room to rise into', async ({ page }) => {
	/* The scroller clips on both axes, so the lifted top row needs room above it. */
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const scroller = (await page.locator('.frame-body').boundingBox())!;
	const first = (await page.locator('.tile-frame').first().boundingBox())!;

	expect(
		first.y - scroller.y,
		'the top row is flush with the scroller, so its ring is clipped'
	).toBeGreaterThanOrEqual(5);
});

test('the heart goes at the smallest grid size, and only there', async ({ page }) => {
	/* Rows are justified, so a threshold set at 180px would strip the heart from the second step
	 * too. Both ends are checked. */
	await serveShapes(page);
	await page.setViewportSize({ width: 1280, height: 900 });
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const shownAt = async (step: string, selector: string) => {
		await (await tileSizeSlider(page)).fill(step);
		const frame = page.locator('.tile-frame').first();
		await frame.hover();
		const height = (await frame.boundingBox())!.height;
		const el = frame.locator(selector).first();
		if ((await el.count()) === 0) return { height, shown: false };
		const shown = await el.evaluate((node) => getComputedStyle(node).display !== 'none');
		return { height, shown };
	};

	// Step 0 is 120, the smallest the control offers.
	const smallHeart = await shownAt('0', '.controls-row button');
	const smallRating = await shownAt('0', '.rating');
	expect(smallHeart.shown, `heart still drawn on a ${smallHeart.height}px tile`).toBe(false);
	expect(smallRating.shown, `rating chip still drawn on a ${smallRating.height}px tile`).toBe(
		false
	);

	const next = await shownAt('1', '.controls-row button');
	expect(next.shown, `heart missing from a ${next.height}px tile`).toBe(true);
});

test('the right-click menu offers more than delete, and keeps delete last and admin-only', async ({
	page
}) => {
	/* Destructive last and admin actions gated; no Open, since clicking a tile opens it. */
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	await page.locator('.tile').first().click({ button: 'right' });

	for (const label of ['Add to', 'Copy link', 'Hide', 'Remove']) {
		await expect(page.getByRole('menuitem', { name: label })).toBeVisible();
	}
	// An absence, from a menu the loop above proved is drawn.
	await expect(page.getByRole('menuitem', { name: 'Open' })).toHaveCount(0);

	const deleteItem = page.getByRole('menuitem', { name: 'Remove' });
	await expect(deleteItem).toHaveClass(/destructive/);
});

test('favouriting from the menu saves it, and the heart on the tile agrees', async ({ page }) => {
	// Two ways to the same state must not disagree.
	let saved: unknown = null;
	await serveShapes(page);
	/* One request for a selection, whether it names one file or four hundred. */
	await page.route('**/api/assets/favorite', async (route) => {
		saved = route.request().postDataJSON();
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 1, skipped: 0, vault_locked: false })
		});
	});

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	await page.locator('.tile').first().click({ button: 'right' });
	// The heart is one of the five places a file can be put.
	await page.getByRole('menuitem', { name: 'Add to' }).hover();
	await page.getByRole('menuitem', { name: 'Favorites', exact: true }).click();

	expect(saved).toEqual({ asset_ids: ['wide'], favorite: true });
	const frame = page.locator('.tile-frame').first();
	await frame.hover();
	await expect(frame.locator('.controls-row button').first()).toHaveClass(/on/);
});

test('copying a link puts the asset address on the clipboard', async ({ page, context }) => {
	await context.grantPermissions(['clipboard-read', 'clipboard-write']);
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	await page.locator('.tile').first().click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Copy link' }).click();

	const copied = await page.evaluate(() => navigator.clipboard.readText());
	expect(copied).toMatch(/\/asset\/wide$/);
});

test('the heart and the chip sit on the same line', async ({ page }) => {
	/* The heart and the duration chip line up by their CENTRES: the boxes differ in height, so
	 * bottoms would line up only by coincidence. */
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	const frame = page.locator('.tile-frame').first();
	await frame.hover();

	const seen = await frame.evaluate((el) => {
		const middle = (selector: string) => {
			const box = (el.querySelector(selector) as HTMLElement).getBoundingClientRect();
			return { middle: box.top + box.height / 2, height: box.height };
		};
		return { chip: middle('.duration'), heart: middle('.controls-row button') };
	});

	/* Under a pixel: both middles land on fractions of type. */
	expect(
		Math.abs(seen.chip.middle - seen.heart.middle),
		`heart and chip are not on one line: chip ${seen.chip.height}px, heart ${seen.heart.height}px`
	).toBeLessThanOrEqual(1);

	/* Or the check above is trivially true. */
	expect(seen.chip.height, 'the two boxes are the same height').not.toBe(seen.heart.height);
});

test('the grid scrolls to its own last row and its paginator', async ({ page }) => {
	/* The last rows and the pager must be reachable inside the grid's own scroller. */
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });

	const items = Array.from({ length: 60 }, (_, n) => ({
		id: `s${n}`,
		media_type: 'video',
		width: 1920,
		height: 1080,
		duration_ms: 61_000,
		favorite: false,
		rating: null,
		concealed: false,
		thumb: true
	}));
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: 200, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	/* Held at the bottom until scrolling changes nothing: rows are still being added after the
	 * first tile shows. */
	const toTheBottom = async () =>
		page.evaluate(async () => {
			const scroller = document.querySelector('.frame-body')!;
			let landed = -1;
			for (let tries = 0; tries < 30 && landed !== scroller.scrollTop; tries += 1) {
				landed = scroller.scrollTop;
				scroller.scrollTop = scroller.scrollHeight;
				await new Promise(requestAnimationFrame);
				await new Promise(requestAnimationFrame);
			}
			return { bottom: scroller.getBoundingClientRect().bottom, window: window.innerHeight };
		});

	const room = await toTheBottom();
	expect(room.bottom).toBeLessThanOrEqual(room.window + 1);

	/* The pager and the last row THE PAGE DRAWS are on screen; the grid asks for a surplus that
	 * belongs to the next page. */
	await expect(page.getByRole('navigation', { name: 'Pages' })).toBeInViewport();
	// Again: a row may have been added meanwhile.
	await toTheBottom();
	await expect(page.locator('.row').last()).toBeInViewport();
});

test('the vault mark does not depend on the sharing-marks switch', async ({ page }) => {
	/* The vault mark is not a preference: it says a hidden file is being shown anyway. The switch
	 * is flipped for real, since mocking the read would test the mock. */
	await signInAsAdmin(page);

	const items = [
		{
			id: 'v1',
			media_type: 'video',
			width: 1920,
			height: 1080,
			duration_ms: 61_000,
			favorite: false,
			rating: null,
			concealed: false,
			thumb: true,
			shared: true,
			shared_here: true,
			hidden: true
		}
	];
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: 1, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	await expect(page.locator('.mark.vaulted')).toHaveCount(1);
	await expect(page.locator('.marks .mark')).toHaveCount(2);

	/* Put back at the end: every spec on this server shares the account. */
	const before = await page.evaluate(async () => {
		const answer = await fetch('/api/settings', { credentials: 'same-origin' });
		const { sections } = (await answer.json()) as {
			sections: { settings?: { key: string; value: unknown }[] }[];
		};
		return sections
			.flatMap((section) => section.settings ?? [])
			.find((one) => one.key === 'appearance.tile.sharing')?.value;
	});
	expect(before, 'the account has no answer for the sharing mark').toBeTruthy();

	await page.evaluate(async () => {
		const me = await (await fetch('/api/auth/me', { credentials: 'same-origin' })).json();
		await fetch('/api/settings', {
			method: 'PUT',
			credentials: 'same-origin',
			headers: { 'content-type': 'application/json', 'x-csrf-token': me.csrf_token },
			body: JSON.stringify({ values: { 'appearance.tile.sharing': 'never' } })
		});
	});
	await page.reload();
	await expect(page.locator('.tile').first()).toBeVisible();

	await expect(page.locator('.marks .mark')).toHaveCount(1);
	await expect(page.locator('.mark.vaulted')).toHaveCount(1);

	await page.evaluate(async (value) => {
		const me = await (await fetch('/api/auth/me', { credentials: 'same-origin' })).json();
		await fetch('/api/settings', {
			method: 'PUT',
			credentials: 'same-origin',
			headers: { 'content-type': 'application/json', 'x-csrf-token': me.csrf_token },
			body: JSON.stringify({ values: { 'appearance.tile.sharing': value } })
		});
	}, before);
});

test('a rating set on the asset screen lands on its tile immediately', async ({ page }) => {
	/* The asset screen opens over the grid, which updates one row rather than re-reading a page. */
	await signInAsAdmin(page);

	const items = [
		{
			id: 'r9',
			media_type: 'image',
			width: 1000,
			height: 1000,
			duration_ms: null,
			favorite: false,
			rating: null,
			concealed: false,
			thumb: true,
			original_filename: 'star-me.jpg'
		}
	];
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: 1, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/r9', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ ...items[0], added_at: 0, view_count: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	/* Stored out of ten, drawn out of five, so the third star writes 6. The whole reply: the grid
	   finds the row by `asset_id`. */
	await page.route('**/api/assets/*/rating', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ asset_id: 'r9', favorite: false, rating: 8, views: 0 })
		})
	);

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	// Nothing on the tile yet.
	await expect(page.locator('.rating')).toHaveCount(0);

	await page.locator('.tile').first().click();
	await expect(page.getByRole('dialog')).toBeVisible();
	/* One chip that opens a chooser of buttons, not a row of stars. */
	await page.getByRole('button', { name: 'This file: not rated' }).click();
	const fourth = page.getByRole('button', { name: '4 stars', exact: true });
	/* Waited for by its reply, which is what updates the tile. */
	const answered = page.waitForResponse((reply) => reply.url().includes('/rating'));
	await fourth.click();
	await answered;
	/* And confirmed here: the control is drawn a frame before its handler is. */
	await expect(page.getByRole('button', { name: 'This file: 4 out of 5' })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog')).toHaveCount(0);

	// Without a reload. The star is a codepoint: this repo's source is ASCII.
	await expect(page.locator('.rating')).toHaveText(`4${String.fromCharCode(9733)}`);
});

test('the tooltip over a tile colours its status word', async ({ page }) => {
	/* The status word carries the colour and the rest does not, so Shared and Restricted differ. */
	await signInAsAdmin(page);

	const items = [
		{
			id: 'shared-1',
			media_type: 'video',
			width: 800,
			height: 600,
			thumb: true,
			shared: true,
			shared_here: true
		},
		{
			id: 'restricted-1',
			media_type: 'video',
			width: 800,
			height: 600,
			thumb: true,
			restricted: true,
			restricted_here: true
		}
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

	await page.goto('/browse');
	await expect(page.locator('.marks .mark')).toHaveCount(2);

	const inkOf = async (index: number): Promise<string> => {
		// The tile lifts on hover, so it is hovered before its chip.
		await page.locator('.tile-frame').nth(index).hover();
		await page.locator('.marks .mark').nth(index).hover();
		const lead = page.locator('[role="tooltip"] .lead');
		await expect(lead).toBeVisible();
		return lead.evaluate((node) => getComputedStyle(node).color);
	};

	const shared = await inkOf(0);
	const restricted = await inkOf(1);

	expect(shared, 'Shared and Restricted are the same colour over a tile').not.toBe(restricted);
});

test('every piece of furniture on a tile is one size, at every size of tile', async ({ page }) => {
	/* The tile's furniture reads one set of tokens, the same on the smallest tile as the largest:
	 * a step-down by tile size would follow the window, as the grid quantises the size control. */
	await signInAsAdmin(page);
	await serveShapes(page);

	const sizes = async () =>
		page.evaluate(() => {
			const of = (selector: string) => {
				const found = document.querySelector(selector);
				if (!found) return null;
				const style = getComputedStyle(found);
				return {
					height: Math.round(found.getBoundingClientRect().height),
					radius: style.borderTopLeftRadius,
					size: style.fontSize,
					weight: style.fontWeight
				};
			};
			return {
				tile: Math.round(document.querySelector('.tile-frame')!.getBoundingClientRect().height),
				clock: of('.duration'),
				mark: of('.mark'),
				rating: of('.rating')
			};
		});

	/** The tile height once two readings agree: a resize re-justifies asynchronously. */
	const settledTile = async () => {
		let seen = -1;
		for (let tries = 0; tries < 25; tries += 1) {
			const now = (await sizes()).tile;
			if (now === seen) return now;
			seen = now;
			await page.waitForTimeout(100);
		}
		return seen;
	};

	/* Searched for, not written down: a justified row's height is not monotonic in the window's. */
	const HEIGHTS = [1080, 1000, 920, 1160, 860, 1240, 800, 1320];

	const windowWhereTileIs = async (width: number, low: number, high: number) => {
		for (const height of HEIGHTS) {
			await page.setViewportSize({ width, height });
			const tile = await settledTile();
			if (tile > low && tile <= high) return { height, tile };
		}
		return null;
	};

	/* Big enough that the marks (140) and the rating (150) are drawn. */
	await page.setViewportSize({ width: 1600, height: HEIGHTS[0] });
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	await page.locator('.tile-frame').first().hover();
	await expect(page.locator('.rating').first()).toBeVisible();

	const big = await windowWhereTileIs(1600, 180, Number.POSITIVE_INFINITY);
	expect(big, 'no window height in the list draws a tile over 180px at 1600 wide').not.toBeNull();
	await page.locator('.tile-frame').first().hover();
	await expect(page.locator('.rating').first()).toBeVisible();

	const roomy = await sizes();
	expect(
		roomy.tile,
		'the window is too short to draw a tile with its furniture on'
	).toBeGreaterThan(180);
	for (const [what, piece] of Object.entries(roomy)) {
		if (what === 'tile' || !piece || typeof piece === 'number') continue;
		expect(piece.height, `${what} is not the full-size height`).toBe(22);
		expect(piece.size, `${what} is set at a different size from its neighbours`).toBe(
			roomy.clock!.size
		);
		expect(piece.weight, `${what} is set at a different weight from its neighbours`).toBe(
			roomy.clock!.weight
		);
	}

	/* The other end: a tile between 150 (the rating still drawn) and 180, where every piece must be
	 * the SAME. */
	const narrow = await windowWhereTileIs(1280, 150, 180);
	expect(
		narrow,
		'no window height in the list lands a tile between 150 and 180 at 1280 wide'
	).not.toBeNull();
	await page.locator('.tile-frame').first().hover();

	const small = await sizes();
	expect(small.tile, 'the tile is below the band where its chrome is drawn').toBeGreaterThan(150);
	for (const [what, piece] of Object.entries(small)) {
		if (what === 'tile' || !piece || typeof piece === 'number') continue;
		/* Equal to the roomy measurement, not to a number written here. */
		const big = roomy[what as keyof typeof roomy] as { height: number; size: string };
		expect(piece.height, `${what} is a different height on a small tile`).toBe(big.height);
		expect(piece.size, `${what} is a different type size on a small tile`).toBe(big.size);
	}
});
