import { expect, test } from './test';
import { SIZE_STEPS } from '../src/lib/grid/justify';
import { signInAsAdmin } from './admin';

/* Every top-level page's title sits in the same place, so no page adds an inset of its own.
 * Two pixels of tolerance: a title beside taller controls rounds differently. */

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

	/* Measured from the top of `main`: the chips row appears only when there are chips, and
	 * `/favorites` always draws one. */
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

/* The header row holds a heading, a count, a status and controls, never prose; a lede goes
 * under the row. */
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

/* Clicked through the rail, not reloaded: a full-bleed screen's `:global(main:has(...))` rule
 * stays loaded after a client-side navigation and can match the next screen. */
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

	/* The inset is on `.main-inner`, inside the scrolling box; `main` itself is unpadded on
	 * screens that draw their own frame. */
	const mainPaddingLeft = () =>
		page
			.locator('main .main-inner')
			.first()
			.evaluate((el) => parseFloat(getComputedStyle(el).paddingLeft));

	/** How far in a framed screen puts its title. */
	const titleLeft = async () => {
		const title = page.getByRole('heading', { level: 1 }).first();
		await title.waitFor();
		return Math.round((await title.boundingBox())!.x);
	};

	await page.goto('/browse');

	// Scoped to the rail: an entity page's tabs are links with the same words.
	const destination = (name: string) =>
		page.locator('nav.rail').getByRole('link', { name, exact: true });

	// People's top-level element shares a class with `AssetGrid`, whose CSS is resident.
	await destination('People').click();
	// The wall writes its position into the address, so only the route is checked.
	await expect(page).toHaveURL(/\/people(\?|$)/);
	const framed = await titleLeft();
	expect(framed, 'a framed screen lost its own inset').toBeGreaterThan(20);

	// A person's page is full-bleed; its `.page` rule must not follow us back out.
	await page.getByRole('link', { name: personName, exact: true }).click();
	await expect(page).toHaveURL(/\/people\//);

	await destination('Tags').click();
	await expect(page).toHaveURL(/\/tags$/);
	expect(await titleLeft(), "a framed screen lost its inset after a person's own page").toBe(
		framed
	);

	// A second framed screen, by a different route than Tags.
	await destination('Collections').click();
	await expect(page).toHaveURL(/\/collections(\?|$)/);
	expect(await titleLeft(), "a second framed screen lost its inset after a person's page").toBe(
		framed
	);

	// Rail destinations get the whole box; the leak can only move a framed screen's inset.
	await destination('Theater').click();
	await expect(page).toHaveURL(/\/theater$/);
	await expect(
		page.locator('main .main-inner'),
		'a screen inside the shell is being scrolled and padded by the layout again'
	).toHaveCount(0);
});

test('the Settings title starts where its section list does', async ({ page }) => {
	/* "Settings", the group headings and the section names share one left edge, read off the
	 * text: a box's left is unmoved by its padding. */
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

/** Wait for a sheet to arrive: its click-outside layer is armed just after it opens. */
async function settled(sheet: import('@playwright/test').Locator) {
	await sheet.evaluate(async (el) => {
		await Promise.all(el.getAnimations({ subtree: true }).map((one) => one.finished));
		await new Promise(requestAnimationFrame);
	});
}

test('a modal goes away when you click beside it', async ({ page }) => {
	// Every sheet closes on a click outside, or it reads as stuck.
	await signInAsAdmin(page);
	await page.goto('/browse');

	await page.getByRole('button', { name: 'Add', exact: true }).click();
	const sheet = page.getByRole('dialog', { name: 'Add media' });
	await expect(sheet).toBeVisible();
	await settled(sheet);

	await page.mouse.click(8, 8);

	await expect(sheet).toBeHidden();
});

test('and so does the one asking a question', async ({ page }) => {
	// An alert closes on a click outside too, and that can only decline.
	await signInAsAdmin(page);

	// Created through the API: Add on a wall opens a screen of its own.
	const name = `e2e-dismiss-${Date.now()}`;
	const me = await page.request.get('/api/auth/me');
	const made = await page.request.post('/api/collections', {
		data: { name },
		headers: { 'x-csrf-token': (await me.json()).csrf_token as string }
	});
	expect(made.ok(), await made.text()).toBeTruthy();
	// Narrowed to it: a new, unused collection can sit on a page out of view.
	await page.goto('/collections');
	await page.getByRole('searchbox', { name: 'Search collections' }).fill(name);
	// Delete is on the card's right-click menu, like every verb on a row.
	const row = page.locator('.card').filter({ hasText: name });
	await expect(row).toBeVisible();

	await row.click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Delete' }).click();
	const confirm = page.getByRole('alertdialog');
	await expect(confirm).toBeVisible();
	await settled(confirm);

	await page.mouse.click(8, 8);

	await expect(confirm).toBeHidden();
	await expect(row).toBeVisible();
});

/* An icon glyph's font box is not its drawn box, so "icon, gap, word" can sit off-centre in
 * a centred button. Half a pixel of tolerance. */
test('an icon and a word inside a button are centred together', async ({ page }) => {
	await signInAsAdmin(page);
	await page.goto('/browse');
	await expect(page.locator('nav.rail a.item').first()).toBeVisible();

	// The rail's Done button exists only while the rail is being arranged.
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
	/* `GIF` and `1:52` have no descenders, so a centred line box holds the word high: this
	 * measures the ink. */
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

	/* The ladder's top size: a size off the ladder falls back to automatic. */
	await page.addInitScript(
		(size) => localStorage.setItem('sift.grid.rowHeight', String(size)),
		SIZE_STEPS[SIZE_STEPS.length - 1]
	);
	await page.goto('/browse');
	await expect(page.locator('.duration')).toHaveCount(2);

	/* Pixels, not a rect: rounded text metrics hide the defect. The page decodes its own
	 * screenshot, so no PNG library is added. */
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

			// Near-white text on a near-black chip: a row's brightest pixel says if ink reaches it.
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
		// One pixel: an odd number of spare rows cannot split evenly. Two is the defect.
		expect(Math.abs(chip.above - chip.below), `${chip.what} sits off-centre`).toBeLessThanOrEqual(
			1
		);
	}
});

test('a glyph alone in a button is centred in it', async ({ page }) => {
	/* A glyph alone in a `Button` is laid out as text, whose line box rides it high. Measured as
	 * ink, without typing: the address carries the query. Add controls to the list below. */
	await signInAsAdmin(page);

	await page.goto('/browse?q=anything');
	await expect(page.locator('h1').first()).toBeVisible();

	/* The icon font loads with `font-display: block`, and a fallback glyph sits far off-centre:
	 * awaited, painted for two frames, then confirmed in use. */
	await page.evaluate(async () => {
		await document.fonts.ready;
		await document.fonts.load('24px "Material Symbols Rounded"');
		await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
	});
	expect(
		await page.evaluate(() => document.fonts.check('24px "Material Symbols Rounded"', 'close')),
		'the icon font never arrived, so every measurement below would be of the fallback'
	).toBe(true);

	/* Held still first, since the field animates in. Both glyphs are symmetric in their em box;
	 * check a glyph before adding its control. */
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

		// Some ink must exist: an empty photograph gives undefined arithmetic, not a failure.
		expect(
			ink.above === undefined || ink.left === undefined,
			`${name}: nothing was drawn in the button, so there was no glyph to measure`
		).toBe(false);

		// One pixel on a whole-pixel raster. Three is the defect.
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
	/* Pointing at a rail link preloads its route and its stylesheet for the session, so an
	 * unanchored global rule can trim every other scrolling box. */
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

	for (const name of ['Theater', 'Organize', 'People', 'Collections', 'Downloads', 'Hidden']) {
		// Scoped to the rail: an entity page's tabs are links with the same words.
		const link = page.locator('nav.rail').getByRole('link', { name, exact: true });
		if ((await link.count()) === 0) continue;
		await link.hover();
		// Long enough for the preload and its stylesheet.
		await page.waitForTimeout(600);
		expect(
			await bottom(),
			`Pointing at ${name} changed the page behind it. Something in that route's stylesheet is ` +
				'written unanchored: a `:global` rule with no local selector in front of it reaches ' +
				'every screen, and preloading is enough to apply it.'
		).toBe(before);
	}
});

/* A trailed title stands where an untrailed one and Theater's do; where the screen's own row
 * is drawn, lower by that row's height. */
test('a trail leaves the title exactly where an untrailed one and Theater sit', async ({
	page
}) => {
	await signInAsAdmin(page);

	// The person is answered so the page has a title; empty answers still draw a header.
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

	/* Read in one frame once still: two reads can straddle a row arriving. */
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
			expect(
				one.lines.length,
				`${name}'s title slid as the row came in: ${seen}`
			).toBeLessThanOrEqual(2);
		}
	}
});
