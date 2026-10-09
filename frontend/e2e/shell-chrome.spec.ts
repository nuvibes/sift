/* The shell's chrome: the inset card and the collapsible rail. Geometry needs a layout engine. */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

const toggle = (page: Page) => page.getByRole('button', { name: /the sidebar/ });

/* The rail's width once it stops animating, read from `nav.rail` where `--rail-width` lands. */
const railWidth = (page: Page) =>
	expect.poll(async () => (await page.locator('nav.rail').boundingBox())!.width);

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('the content sits on the sidebar as an inset card', async ({ page }) => {
	// The gap separates the rail from the card, in place of a border.
	await page.goto('/browse');

	const card = page.locator('.content');
	const box = (await card.boundingBox())!;

	expect(box.x + box.width, 'the card is flush with the right edge').toBeLessThan(1400);
	expect(box.y, 'the card is flush with the top').toBeGreaterThan(0);

	const radius = await card.evaluate((el) => getComputedStyle(el).borderTopLeftRadius);
	expect(parseFloat(radius), 'the card has square corners').toBeGreaterThan(8);

	// Square at the bottom: a curve there would round off the window's own corner.
	const bottom = await card.evaluate((el) => getComputedStyle(el).borderBottomLeftRadius);
	expect(parseFloat(bottom), 'the card is rounded at the bottom').toBe(0);
	expect(box.y + box.height, 'the card stops short of the bottom of the window').toBe(900);

	// The card fits the window, so the top bar cannot scroll off.
	expect(box.height).toBeLessThanOrEqual(900);
	const scrolls = await page.evaluate(() => document.body.scrollHeight > window.innerHeight + 1);
	expect(scrolls, 'the page scrolls as a whole').toBe(false);
});

test('the expanded rail is tight around its labels, not a quarter of the window', async ({
	page
}) => {
	// 208 fits the longest label, "Collections"; loose bounds survive a token nudge.
	await page.goto('/browse');

	await railWidth(page).toBeGreaterThan(180);
	await railWidth(page).toBeLessThan(224);
});

test('the rail collapses to icons and stays collapsed', async ({ page }) => {
	await page.goto('/browse');

	await railWidth(page).toBeGreaterThan(150);
	await expect(page.locator('nav.rail .label').first()).toBeVisible();
	// Counted, not copied from `nav.ts`, so adding a rail row does not fail this.
	const destinations = await page.locator('nav.rail a.item').count();
	expect(destinations).toBeGreaterThan(5);

	await toggle(page).click();

	await railWidth(page).toBeLessThan(100);
	await expect(page.locator('nav.rail .label').first()).toBeHidden();
	await expect(page.locator('nav.rail a.item')).toHaveCount(destinations);
});

test('and it is still collapsed after a reload', async ({ page }) => {
	// Kept in this browser, not the account, so the first paint has the right width.
	await page.goto('/browse');
	await toggle(page).click();
	await railWidth(page).toBeLessThan(100);

	await page.reload();

	await railWidth(page).toBeLessThan(100);
});

test('the brand becomes the mark, and still goes home', async ({ page }) => {
	// Collapsed, the brand is still the link home.
	await page.goto('/tags');
	await toggle(page).click();

	const brand = page.locator('nav.rail a.brand');
	await expect(brand).toHaveAttribute('href', '/browse');

	// Told apart by shape: the art is inline SVG with no filename to match.
	const art = brand.locator('svg');
	const collapsed = await art.boundingBox();
	expect(collapsed).not.toBeNull();
	expect(collapsed!.width / collapsed!.height).toBeLessThan(1.5);

	await toggle(page).click();
	const expanded = await art.boundingBox();
	expect(expanded).not.toBeNull();
	expect(expanded!.width / expanded!.height).toBeGreaterThan(2);

	await toggle(page).click();
	await brand.click();
	// The path only: walls write `?from=` into the query as the grid settles.
	await expect(page).toHaveURL((url) => url.pathname === '/browse');
});

test('a window with no room for labels collapses whatever the button last said', async ({
	page
}) => {
	// The narrow band is forced: there is no room for 208px of nav beside a usable grid.
	await page.goto('/browse');
	await expect(page.locator('nav.rail .label').first()).toBeVisible();

	await page.setViewportSize({ width: 900, height: 900 });

	await expect(page.locator('nav.rail .label').first()).toBeHidden();
	await railWidth(page).toBeLessThan(100);
});

test('the marker for where you are sits inside the rail', async ({ page }) => {
	await page.goto('/browse');

	const marker = await page.locator('nav.rail a.item.active').evaluate((element) => {
		const before = getComputedStyle(element, '::before');
		return { left: before.left, rail: element.closest('nav')!.getBoundingClientRect().left };
	});

	expect(parseFloat(marker.left)).toBeGreaterThan(-13);
	expect(marker.rail).toBe(0);
});

test('the tile size survives going to another screen and back', async ({ page }) => {
	// The size lives outside the per-screen `Grid`, or every navigation would reset it.
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })
		})
	);
	await page.goto('/browse');

	const slider = page.locator('.size input[type="range"]');
	await slider.fill('0');
	const chosen = await slider.inputValue();

	await page.goto('/tags');
	await page.goto('/browse');

	await expect(page.locator('.size input[type="range"]')).toHaveValue(chosen);
});

test('the settings section title lines up with the top of the search box', async ({ page }) => {
	// The search box and the section title share a top edge, whatever the type scale.
	await page.goto('/settings/library');

	const searchBox = page.getByRole('searchbox', { name: 'Search settings' }).locator('xpath=..');
	const sectionTitle = page.getByRole('heading', { name: 'Folders', level: 1 });
	await expect(searchBox).toBeVisible();
	await expect(sectionTitle).toBeVisible();

	/* Measured after the webfont loads: the fallback moves the two headings differently. */
	await page.evaluate(async () => {
		await document.fonts.ready;
		await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
	});
	expect(
		await page.evaluate(() => document.fonts.check('16px "Archivo Variable"', 'Folders')),
		'the interface font never arrived, so both measurements below would be of the fallback'
	).toBe(true);

	const box = (await searchBox.boundingBox())!;
	const title = (await sectionTitle.boundingBox())!;

	expect(
		Math.abs(box.y - title.y),
		`the search box starts at ${box.y} and the section title at ${title.y}`
	).toBeLessThan(4);
});

test("a focus ring near the pane's own edge is not clipped by its scrollbar", async ({ page }) => {
	// The pane reserves room for a focus ring, which its scrolling edge would otherwise clip.
	await page.setViewportSize({ width: 1400, height: 900 });
	// Profile: its PIN field is at the very top of the pane.
	await page.goto('/settings/profile');

	const pin = page.getByLabel('New PIN');
	await pin.focus();

	// The scrolling section itself: `.pane` names more than one element.
	const paneBox = (await page.getByRole('region', { name: 'Profile' }).boundingBox())!;
	const pinBox = (await pin.boundingBox())!;

	const RING_WIDTH = 4; // the outer layer of --focus-ring: `0 0 0 4px ...`
	expect(
		pinBox.x - paneBox.x,
		`the field sits only ${pinBox.x - paneBox.x}px from the pane's start edge`
	).toBeGreaterThanOrEqual(RING_WIDTH);
	expect(
		pinBox.y - paneBox.y,
		`the field sits only ${pinBox.y - paneBox.y}px from the pane's top edge`
	).toBeGreaterThanOrEqual(RING_WIDTH);
});

test('the screen name sits above both columns rather than inside the list', async ({ page }) => {
	// The title is a row of the grid, not the nav's first item.
	await page.goto('/settings/library');

	const title = page.locator('.settings .title');
	await expect(title).toHaveText('Settings');
	expect(
		await title.evaluate((el) => el.closest('nav') !== null),
		'the screen name is still inside the sections list'
	).toBe(false);
});

test('a rail row is not told its own name twice', async ({ page }) => {
	// No tooltip beside a visible label; collapsed, it is the only name the icon has.
	await page.goto('/browse');

	const browse = page.getByRole('link', { name: 'Browse' });
	await expect(browse).toBeVisible();
	await browse.hover();
	await page.waitForTimeout(600);
	await expect(page.getByRole('tooltip')).toHaveCount(0);

	await toggle(page).click();
	await railWidth(page).toBeLessThan(100);

	await page.mouse.move(0, 0);
	await browse.hover();
	await expect(page.getByRole('tooltip')).toHaveText('Browse');
});

test('the filters control says what it is, and says why when it cannot act', async ({ page }) => {
	await page.goto('/browse');

	// The row is glyphs, so each keeps a name and a tooltip in words.
	const filter = page.getByRole('button', { name: 'Filter', exact: true });
	await expect(filter).toBeVisible();
	await expect(filter).toHaveAttribute('aria-label', 'Filter');

	// The panel is drawn over the page, so only the trigger can say it is open.
	await expect(filter).toHaveAttribute('aria-expanded', 'false');
	await filter.click();
	await expect(filter).toHaveAttribute('aria-expanded', 'true');
	await page.keyboard.press('Escape');

	// A screen that is not a wall: there the dimmed trigger opens nothing and the tooltip is all.
	await page.goto('/organize');
	const dimmed = page.getByRole('button', { name: 'Filter', exact: true });
	await expect(dimmed).toBeDisabled();
	await dimmed.hover();
	await expect(page.getByRole('tooltip')).toHaveText(
		'This screen lists things to review, not files'
	);
});

test('every settings section title is the same size', async ({ page }) => {
	// A missing token in a `font:` shorthand silently falls back to body size.
	const sections = ['library', 'jobs', 'downloads', 'playback', 'privacy', 'appearance', 'about'];
	const sizes: Record<string, string> = {};

	for (const section of sections) {
		await page.goto(`/settings/${section}`);
		const title = page.locator('.settings .pane h1').first();
		await expect(title).toBeVisible();
		sizes[section] = await title.evaluate((el) => getComputedStyle(el).fontSize);
	}

	const distinct = new Set(Object.values(sizes));
	expect(distinct.size, `sizes were ${JSON.stringify(sizes)}`).toBe(1);
	expect(parseFloat([...distinct][0])).toBeGreaterThan(20);
});

test('the settings close button is a circle, not an oval', async ({ page }) => {
	// A round close button with no "Esc" hint, on the panel opened from the rail.
	await page.goto('/browse');
	await page.getByRole('link', { name: 'Settings' }).click();
	const close = page.locator('.close');
	await expect(close).toBeVisible();
	const box = (await close.boundingBox())!;
	expect(
		Math.abs(box.width - box.height),
		`close is ${box.width}x${box.height}`
	).toBeLessThanOrEqual(1);
});

test('the sharing legend in Settings shows every mark the app can draw', async ({ page }) => {
	// The legend is built from `markFor`, as the tiles are, so it cannot drift from them.
	await signInAsAdmin(page);
	await page.goto('/settings/appearance');

	const legend = page.locator('.legend');
	await expect(legend).toBeVisible();

	await expect(legend).toContainText('guests you chose can see this');
	await expect(legend).toContainText('guests you chose can never see this');
	await expect(legend).toContainText('some guests and restricted from others');
	await expect(legend).toContainText('Press to see what decides this.');
	await expect(legend).toContainText('unhide with your PIN');

	// `.on-a-tile`: a badge with a scrim, not a chip.
	await expect(legend.locator('.on-a-tile')).toHaveCount(6);
	await expect(legend.locator('.on-a-tile.both')).toHaveCount(1);
	await expect(legend.locator('.on-a-tile.restricted')).toHaveCount(2);
});

test('every control in Settings starts at the same place, beside the name it belongs to', async ({
	page
}) => {
	// Every control lines up on one grid, across kinds of control.
	await signInAsAdmin(page);

	// Updates is left out: its rows are facts.
	for (const section of ['appearance', 'playback', 'privacy', 'performance']) {
		await page.goto(`/settings/${section}`);
		await expect(page.locator('.row .control').first()).toBeVisible();

		const rows = await page.evaluate(() => {
			const out: {
				label: string;
				left: number;
				right: number;
				figure: boolean;
				controlTop: number;
				nameBottom: number;
			}[] = [];
			// A `.loose` row states a fact beside its words, so its column is `auto` on purpose.
			for (const row of Array.from(document.querySelectorAll('.row:not(.loose)'))) {
				const control = row.querySelector('.control');
				const name = row.querySelector('.name');
				if (!control || !name || control.childElementCount === 0) continue;
				const mine = control.getBoundingClientRect();
				const words = name.getBoundingClientRect();
				out.push({
					label: (name.textContent ?? '').slice(0, 40),
					left: Math.round(mine.left),
					right: Math.round(mine.right),
					figure: control.querySelector(':scope > .note') !== null,
					controlTop: mine.top,
					nameBottom: words.bottom
				});
			}
			return out;
		});

		expect(rows.length, `${section} should have a control to measure`).toBeGreaterThan(0);
		// An action row's column grows left so its figure never pushes the press under it.
		const ends = new Set(rows.map((row) => row.right));
		expect(
			ends.size,
			`${section}: controls end at ${[...ends].join(', ')}, for ${rows
				.map((row) => row.label)
				.join(' | ')}`
		).toBe(1);
		const plain = rows.filter((row) => !row.figure);
		const edges = new Set(plain.map((row) => row.left));
		expect(
			edges.size,
			`${section}: controls start at ${[...edges].join(', ')}, for ${plain
				.map((row) => row.label)
				.join(' | ')}`
		).toBeLessThanOrEqual(1);
		const start = Math.min(...plain.map((row) => row.left));
		for (const row of rows.filter((one) => one.figure)) {
			expect(row.left, `${section}: ${row.label} starts after the column`).toBeLessThanOrEqual(
				start
			);
		}
		for (const row of rows) {
			// Overlap, not a shared centre: the control centres against the name and its help text.
			expect(row.controlTop, `${section}: ${row.label}`).toBeLessThan(row.nameBottom);
		}
	}
});

test('pressing a mark on a tile opens the sharing panel', async ({ page }) => {
	// Pressing a mark opens the panel that explains it; marks are siblings of the tile's button.
	await signInAsAdmin(page);

	const items = [
		{
			id: 'm1',
			media_type: 'video',
			width: 1920,
			height: 1080,
			duration_ms: 61_000,
			favorite: false,
			rating: null,
			concealed: false,
			thumb: true,
			shared: true,
			shared_here: true
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
		route.fulfill({
			status: 200,
			contentType: 'image/png',
			body: Buffer.from(
				'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
				'base64'
			)
		})
	);

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	/* A pointer move after the grid settles: a tooltip opens on a move, never on a rebuild. */
	/* `.mark`, not `.chip`; an empty match would read as a tile that never settles. */
	const mark = page.locator('.marks .mark').first();
	await expect
		.poll(async () => {
			const first = await mark.boundingBox();
			await page.waitForTimeout(120);
			const second = await mark.boundingBox();
			return first && second && first.x === second.x && first.y === second.y;
		})
		.toBe(true);
	await page.mouse.move(0, 0);
	// The marks take no press until the tile is under the pointer.
	await page.locator('.tile-frame').first().hover();
	await mark.hover();
	await expect(page.getByRole('tooltip')).toBeVisible();

	// A real click: the closing panel gives focus back, and no label may appear for it.
	await mark.click();

	await expect(page.locator('.share-sheet')).toBeVisible();
	await expect(page).not.toHaveURL(/\/asset\//);

	// A press latches the tooltip shut until the pointer has left.
	await expect(page.getByRole('tooltip')).toBeHidden();

	/* Clicked beside, not Escape: after a key, restored focus rightly shows a tooltip. */
	/* The panel ignores a click-away while `data-starting-style` says it is still coming in. */
	await expect(page.locator('.share-sheet')).not.toHaveAttribute('data-starting-style');
	await page.mouse.click(4, 400);
	await expect(page.locator('.share-sheet')).toBeHidden();

	// Focus returns about here; neither the label nor the tile may light.
	await page.waitForTimeout(500);
	await expect(page.getByRole('tooltip')).toBeHidden();

	const lit = await page
		.locator('.tile')
		.first()
		.evaluate((tile) => getComputedStyle(tile).boxShadow);
	expect(lit === 'none' || lit === '').toBe(true);
});

test('and pressing the crossed-out eye opens Hidden rather than Sharing', async ({ page }) => {
	// The crossed-out eye opens Hidden and not Sharing, asserted both ways round.
	await signInAsAdmin(page);

	const items = [
		{
			id: 'h1',
			media_type: 'video',
			width: 1920,
			height: 1080,
			duration_ms: 61_000,
			favorite: false,
			rating: null,
			concealed: false,
			thumb: true,
			hidden: true,
			hidden_here: true
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
		route.fulfill({
			status: 200,
			contentType: 'image/png',
			body: Buffer.from(
				'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
				'base64'
			)
		})
	);

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	/* Settled first: a press aimed during the grid's rebuild lands on a replaced mark. */
	const eye = page.locator('.marks .mark.vaulted').first();
	await expect
		.poll(async () => {
			const first = await eye.boundingBox();
			await page.waitForTimeout(120);
			const second = await eye.boundingBox();
			return first && second && first.x === second.x && first.y === second.y;
		})
		.toBe(true);
	// The marks take no press until the tile is under the pointer.
	await page.locator('.tile-frame').first().hover();
	await eye.click();

	const panel = page.getByRole('dialog').filter({ hasText: 'Hidden is about your own screen' });
	await expect(panel).toBeVisible();
	await expect(page.locator('.share-sheet')).toHaveCount(0);
	await expect(page).not.toHaveURL(/\/asset\//);
});
