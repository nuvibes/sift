import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { rewrite } from './routes';

/* The wall, in a real browser: fullscreen decides what composites, and jsdom reports every box
 * as zero, so a wall's shape can only be seen here. The library is intercepted. */

const CLIP = {
	id: 't1',
	media_type: 'video',
	width: 320,
	height: 240,
	duration_ms: 6000,
	favorite: false,
	rating: null,
	concealed: false,
	original_filename: 'clip.mp4',
	// Or the cell is drawn as still importing, and an absence would pass on an empty screen.
	thumb: true
};

async function serveLibrary(page: Page) {
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [CLIP], total: 1, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/t1', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CLIP) })
	);
	await page.route('**/api/assets/*/thumb*', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview*', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/sprite*', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/t1/stream*', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/t1/playback', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				route: 'direct',
				reason: '',
				url: '/api/assets/t1/stream',
				scale_height: null,
				projected_realtime: null,
				streamable: true,
				duration_ms: 6000,
				resume_ms: null
			})
		})
	);
	await page.route('**/api/assets/t1/view', (route) => route.fulfill({ status: 204, body: '' }));
	/* Every browser test shares one admin, so this account's walls are intercepted per test. */
	await page.route('**/api/theater/arrangements', (route) =>
		route.request().method() === 'GET'
			? route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[]}' })
			: route.continue()
	);

	/* The wall's preferences in a store per test: the shared account would leak the shape one
	 * parallel test picks into another, and a pin would stop a pick being read back. */
	const mine: Record<string, unknown> = {
		'theater.layout': 'side_by_side',
		'theater.autoplay': false,
		'theater.end_behaviour': 'loop_all',
		'theater.timer_seconds': 0
	};

	await page.route('**/api/settings', async (route) => {
		if (route.request().method() !== 'GET') {
			const sent = route.request().postDataJSON() as { values?: Record<string, unknown> } | null;
			const values = sent?.values ?? {};
			const keys = Object.keys(values);
			// Anything else is passed through untouched.
			if (keys.length === 0 || !keys.every((key) => key in mine)) return route.continue();
			for (const key of keys) mine[key] = values[key];
			return route.fulfill({ status: 204, body: '' });
		}

		/* Walked, not reached by path: a setting is an object with a `key`. */
		const swap = (node: unknown): void => {
			if (Array.isArray(node)) return node.forEach(swap);
			if (node === null || typeof node !== 'object') return;
			const one = node as Record<string, unknown>;
			if (typeof one.key === 'string' && one.key in mine) one.value = mine[one.key];
			Object.values(one).forEach(swap);
		};
		await rewrite<unknown>(route, swap);
	});
}

/**
 * Wait for every animation that is going to END to end. `getAnimations()` includes repeating ones,
 * whose `finished` never settles; their `endTime` is `Infinity`.
 */
async function settled(page: Page): Promise<void> {
	await page.evaluate(() =>
		Promise.all(
			document
				.getAnimations()
				.filter((one) => Number.isFinite(one.effect?.getComputedTiming().endTime ?? Infinity))
				.map((one) => one.finished.catch(() => undefined))
		).then(() => undefined)
	);
}

/** Wait until an element's box has stopped moving: two readings that agree. */
async function stopped(page: Page, target: ReturnType<Page['locator']>): Promise<void> {
	let previous = '';
	for (let attempt = 0; attempt < 40; attempt += 1) {
		const now = JSON.stringify(await target.boundingBox());
		if (now === previous && now !== 'null') return;
		previous = now;
		await page.waitForTimeout(50);
	}
}

function cells(page: Page) {
	return page.locator('section[aria-label^="Cell "]');
}

/*
 * Reach for the chrome where a hand would: the bars answer the bands at the top and the foot, and
 * until then the row is out of the accessibility tree. Retried as a whole, since an idle clock can
 * hide the bar again; two moves, as a pointer put back where it is reports nothing.
 */
async function reachFor(
	page: Page,
	edge: 'top' | 'bottom',
	control: ReturnType<Page['getByRole']>
): Promise<void> {
	await expect(async () => {
		const wall = await page.locator('.wall').boundingBox();
		if (wall === null) throw new Error('there is no wall to reach across');
		const y = edge === 'top' ? wall.y + 16 : wall.y + wall.height - 20;
		await page.mouse.move(wall.x + wall.width / 2, y - 4);
		await page.mouse.move(wall.x + wall.width / 2, y);
		await expect(control).toBeVisible({ timeout: 1000 });
	}).toPass({ timeout: 20_000 });
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await serveLibrary(page);
	// Theater refuses to draw a wall below a laptop width.
	await page.setViewportSize({ width: 1400, height: 900 });
});

/* Choosing a shape through the screen bar's menu, which shuts on the choice. */
async function chooseLayout(page: Page, named: string) {
	await page.getByRole('button', { name: 'Layouts', exact: true }).click();
	await page.getByRole('option', { name: named, exact: true }).click();
}

test('a wall opens on two feeds, laid out side by side', async ({ page }) => {
	/* Where the cells LAND: all in one column is the failure, and only a browser lays it out. */
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	/* A cell fades and scales in, so the boxes are waited still, not just the animations running
	 * when `settled` is called. */
	await settled(page);
	await stopped(page, cells(page).nth(1));

	const one = await cells(page).nth(0).boundingBox();
	const two = await cells(page).nth(1).boundingBox();
	expect(one).not.toBeNull();
	expect(two).not.toBeNull();
	expect(two!.x).toBeGreaterThan(one!.x);
	expect(Math.abs(two!.y - one!.y)).toBeLessThan(2);
});

test('a feed is removed from its own menu, and the last one stays', async ({ page }) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);

	await cells(page).nth(1).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Remove this feed' }).click();
	await expect(cells(page)).toHaveCount(1);

	// The last one cannot go: there would be nothing left to pick a shape for.
	await cells(page).first().click({ button: 'right' });
	await expect(page.getByRole('menuitem', { name: 'Remove this feed' })).toBeDisabled();
});

test('the controls survive filling the screen, and stay above it', async ({ page }) => {
	/* Fullscreen draws only the fullscreen element, so the shared bar has to be inside it. */
	await page.goto('/theater');
	const layout = page.getByRole('button', { name: 'Layouts', exact: true });
	await expect(layout).toBeVisible();

	const before = await layout.boundingBox();
	await page.keyboard.press('f');
	await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(true);

	/* Woken first: the bar goes quiet after two and a half seconds, which a slow fill outlasts. */
	await page.mouse.move(700, 500);
	await expect(layout, 'the controls went off the window with the rest of the page').toBeVisible();
	const after = await layout.boundingBox();
	const height = page.viewportSize()!.height;
	expect(before!.y, 'the bar was not at the top to begin with').toBeLessThan(height / 2);
	expect(after!.y, 'the bar left the top of the window').toBeLessThan(height / 2);

	/* In flow, not over the wall: the wall starts below the bar. */
	const wall = await cells(page).first().boundingBox();
	expect(wall!.y, 'the bar is drawn over the wall rather than above it').toBeGreaterThanOrEqual(
		after!.y + after!.height - 1
	);
});

test('the bar goes quiet while filled, and reaching for it brings it back', async ({ page }) => {
	/* It goes on an idle clock and comes back when reached for; `B` does the same from the
	   keyboard. Put UP before either height is read. */
	await page.goto('/theater');
	// The screen binds the key on mount.
	await expect(cells(page)).toHaveCount(2);
	await page.keyboard.press('f');
	await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(true);

	const layout = page.getByRole('button', { name: 'Layouts', exact: true });
	/* The wall, not a cell: a width-limited cell does not grow with the wall. */
	const wall = page.locator('.wall');

	await reachFor(page, 'top', layout);
	const tallWithBar = (await wall.boundingBox())!.height;

	await page.keyboard.press('b');
	await expect(layout).toBeHidden();

	/* `display: none`, not a fade, so the wall takes the space. */
	const tallWithout = (await wall.boundingBox())!.height;
	expect(tallWithout, 'the wall did not grow into the space the bar left').toBeGreaterThan(
		tallWithBar
	);

	await reachFor(page, 'top', layout);
	await expect(layout).toBeVisible();
});

test('the cells ease down with the top bar rather than jump', async ({ page }) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await page.keyboard.press('f');
	await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(true);
	const layout = page.getByRole('button', { name: 'Layouts', exact: true });
	await page.keyboard.press('b');
	await expect(layout).toBeHidden();
	await settled(page);
	await stopped(page, cells(page).first());

	const sampling = page.evaluate(
		() =>
			new Promise<number[]>((resolve) => {
				const tops: number[] = [];
				const frame = () => {
					const cell = document.querySelector('section[aria-label^="Cell "]')!;
					tops.push(cell.getBoundingClientRect().top);
					const moved = tops.some((top) => top !== tops[0]);
					const still = moved && tops.slice(-30).every((top) => top === tops.at(-1));
					if (still || tops.length > 600) resolve(tops);
					else requestAnimationFrame(frame);
				};
				requestAnimationFrame(frame);
			})
	);
	await reachFor(page, 'top', layout);
	const tops = await sampling;

	const moves = tops.filter((top, at) => at > 0 && top !== tops[at - 1]).length;
	expect(moves, 'the cells jumped to their place in one frame').toBeGreaterThan(5);
});

test('a panel goes OVER the wall rather than pushing it down', async ({ page }) => {
	/* Panels open over the wall, so it does not move; and the panel is asserted open, or nothing
	 * moving would pass. */
	await page.goto('/theater');
	const wall = cells(page).first();
	/* Settled before as well: the wall grows as the feeds load. */
	await settled(page);
	const before = await wall.boundingBox();

	/* This one is a panel; Layout is a menu, which this rule is not about. */
	await page.getByRole('button', { name: 'Saved Layouts', exact: true }).click();
	const panel = page.locator('.drawer').first();
	await expect(panel).toBeVisible();
	await settled(page);

	/* The SIZE, not the position: a centred cell's top moves for other reasons. */
	const after = await wall.boundingBox();
	expect(
		Math.round(after!.height),
		'the panel took height from the wall, so it is in the flow rather than over it'
	).toBeGreaterThanOrEqual(Math.round(before!.height));

	/* The mechanism: a short panel may not reach the wall, so out of flow is what is held. */
	const position = await page
		.locator('.drawer')
		.first()
		.evaluate((element) => getComputedStyle(element).position);
	expect(position, 'the panel is in the flow, so it pushes whatever is under it').toBe('absolute');
});

test('a layout is chosen from the menu and applies immediately', async ({ page }) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);

	await chooseLayout(page, 'Grid 2x2');

	await expect(cells(page)).toHaveCount(4);
	// And it goes back.
	await chooseLayout(page, 'Grid 1x2 (P)');
	await expect(cells(page)).toHaveCount(2);

	/* Retired shapes stay accepted by the server, since saved walls are filed under them. */
	await page.getByRole('button', { name: 'Layouts', exact: true }).click();
	for (const gone of ['One', 'Stacked', 'Stacked three', 'One above two', 'Two above one']) {
		await expect(page.getByRole('option', { name: gone, exact: true })).toHaveCount(0);
	}
	await expect(page.getByRole('option', { name: 'Grid 2x2', exact: true })).toBeVisible();
	await expect(page.getByRole('option', { name: 'Grid 1x2 (L)', exact: true })).toBeVisible();
});

test('the whole wall goes to the corner, not one cell of it', async ({ page }) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);

	/* On the wall's own bar, at the foot, so a filled wall has a way off it. */
	const corner = page.getByRole('button', { name: 'Open mini player', exact: true });
	await reachFor(page, 'bottom', corner);
	await corner.click();

	const panel = page.getByRole('region', { name: 'Mini player' });
	await expect(panel).toBeVisible();
	// Both cells, not the one in front.
	await expect(panel.locator('section[aria-label^="Cell "]')).toHaveCount(2);
});

test('the wall in the corner grows and shrinks with the panel', async ({ page }) => {
	/* The panel's picture box is a flex container, or the wall sizes from its contents and dragging
	 * the corner moves nothing in it. */
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	const corner = page.getByRole('button', { name: 'Open mini player', exact: true });
	await reachFor(page, 'bottom', corner);
	await corner.click();

	const panel = page.getByRole('region', { name: 'Mini player' });
	await expect(panel).toBeVisible();
	const feed = panel.locator('section[aria-label="Cell 1"]');
	const box = async (thing: typeof panel) => {
		const at = await thing.boundingBox();
		if (at === null) throw new Error('nothing to measure');
		return at;
	};

	/* Read once at rest: two looks that agree. */
	let last = '';
	await expect
		.poll(async () => {
			const now = JSON.stringify(await box(panel));
			const still = now === last;
			last = now;
			if (!still) await page.waitForTimeout(100);
			return still;
		})
		.toBe(true);
	const startedAt = await box(feed);
	const panelAt = await box(panel);

	const grip = await box(panel.locator('.corner.se'));
	await page.mouse.move(grip.x + 8, grip.y + 8);
	await page.mouse.down();
	await page.mouse.move(grip.x + 8 - 320, grip.y + 8 - 260, { steps: 6 });
	await page.mouse.move(grip.x + 8 + 320, grip.y + 8 + 260, { steps: 12 });
	await page.mouse.up();

	const grewTo = await box(panel);
	expect(grewTo.width, 'the panel itself did not resize').toBeGreaterThan(panelAt.width + 100);

	/* Grown by roughly as much as the panel, not a few pixels. */
	await expect
		.poll(async () => (await box(feed)).height, {
			message: 'the wall did not grow with the panel it is in'
		})
		.toBeGreaterThan(startedAt.height + 100);

	// Growing but never shrinking is a missing `min-block-size: 0`.
	const again = await box(panel.locator('.corner.se'));
	await page.mouse.move(again.x + 8, again.y + 8);
	await page.mouse.down();
	await page.mouse.move(again.x + 8 - 300, again.y + 8 - 240, { steps: 12 });
	await page.mouse.up();

	await expect
		.poll(async () => (await box(feed)).height, {
			message: 'the wall grew with the panel but would not shrink with it'
		})
		.toBeLessThan(startedAt.height + 100);
});

test('nothing on the wall wears the scrollbar of a box that should not scroll', async ({
	page
}) => {
	/* Everything in a cell is meant to fit, so anything that can scroll was drawn too large. */
	await page.goto('/theater');
	await chooseLayout(page, 'Grid 1x3');
	await expect(cells(page)).toHaveCount(3);
	await cells(page).first().hover();
	await settled(page);

	const scrollers = await page.evaluate(() => {
		const found: string[] = [];
		for (const cell of document.querySelectorAll('section[aria-label^="Cell "]')) {
			for (const node of cell.querySelectorAll('*')) {
				const box = node as HTMLElement;
				const style = getComputedStyle(box);
				// A drawn scrollbar, not clipping: `hidden` clips without one.
				const bars =
					(/(auto|scroll)/.test(style.overflowY) && box.scrollHeight > box.clientHeight + 1) ||
					(/(auto|scroll)/.test(style.overflowX) && box.scrollWidth > box.clientWidth + 1);
				if (bars) found.push(`${box.tagName}.${box.className}`);
			}
		}
		return found;
	});

	expect(scrollers, 'something inside a cell is drawn larger than the cell').toEqual([]);
});

test('every control on a cell bar answers the pointer', async ({ page }) => {
	/* A transparent layer drawn over the bar can win every hit test while the screen looks right,
	 * so each control is asked whether it receives its own click. */
	await page.goto('/theater');
	await cells(page).first().hover();
	await settled(page);

	const swallowed = await page.evaluate(() => {
		const bar = document.querySelector('.player-bar');
		if (!bar) return ['there is no bar at all'];
		const lost: string[] = [];
		for (const node of bar.querySelectorAll('button, input')) {
			/* Only what is SHOWING: the volume popup has a box while hidden by `visibility`. */
			if (!node.checkVisibility({ visibilityProperty: true, opacityProperty: true })) continue;
			const box = node.getBoundingClientRect();
			if (box.width < 1 || box.height < 1) continue;
			const top = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
			if (!bar.contains(top)) {
				const name =
					node.getAttribute('aria-label') ??
					node.querySelector('[aria-label]')?.getAttribute('aria-label') ??
					node.className;
				lost.push(
					`${name} at ${Math.round(box.x)},${Math.round(box.y)} ${Math.round(box.width)}x${Math.round(box.height)} -> ${top?.tagName}.${(top as HTMLElement)?.className}`
				);
			}
		}
		return lost;
	});

	expect(swallowed, 'something is drawn over the cell bar and taking its clicks').toEqual([]);
});

test('a cell bar is the player bar, at the player size', async ({ page }) => {
	/* The play button is larger than the row's icons; a less specific rule would lose silently. */
	await page.goto('/theater');
	await cells(page).first().hover();
	await settled(page);

	const shape = await page.evaluate(() => {
		const bar = document.querySelector('.player-bar')!;
		const play = bar.querySelector('.play')!.getBoundingClientRect();
		const line = bar.querySelector('.timeline')!.getBoundingClientRect();
		const row = bar.querySelector('.row')!.getBoundingClientRect();
		return { play: Math.round(play.width), above: line.bottom <= row.top + 1 };
	});

	expect(shape.play, 'the play button is the size of every other icon on the row').toBe(44);
	expect(shape.above, 'the scrubber is on the same line as the transport').toBe(true);
});

/*
 * Portrait feeds reach the foot of a filled screen and stop 8px above the bar while it is up.
 * Wide enough that three 9:16 feeds are limited by the HEIGHT.
 */
test('portrait feeds reach the foot of a filled wall, and stop 8px above the bar while it is up', async ({
	page
}) => {
	const PORTRAIT = { ...CLIP, width: 1080, height: 1920 };
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [PORTRAIT], total: 1, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/t1', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PORTRAIT) })
	);
	await page.setViewportSize({ width: 1800, height: 900 });
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await chooseLayout(page, 'Grid 1x3');
	await expect(cells(page)).toHaveCount(3);

	await page.keyboard.press('f');
	await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(true);
	await page.keyboard.press('b');
	await expect(page.locator('.stage-bar')).toBeHidden();
	await settled(page);
	await stopped(page, cells(page).first());

	const bottoms = () =>
		page.evaluate(() => {
			const wall = document.querySelector('.wall')!.getBoundingClientRect();
			return {
				wall: wall.bottom,
				cells: [...document.querySelectorAll('section[aria-label^="Cell "]')].map(
					(cell) => cell.getBoundingClientRect().bottom
				)
			};
		});

	const hidden = await bottoms();
	for (const [at, bottom] of hidden.cells.entries()) {
		expect(
			Math.abs(hidden.wall - bottom),
			`cell ${at + 1} stops ${(hidden.wall - bottom).toFixed(1)}px short of the foot of the wall`
		).toBeLessThanOrEqual(1);
	}

	await reachFor(page, 'bottom', page.locator('.stage-bar'));
	await page.locator('.stage-bar').hover();
	await stopped(page, cells(page).first());
	const barTop = (await page.locator('.stage-bar').boundingBox())!.y;
	const up = await bottoms();
	for (const [at, bottom] of up.cells.entries()) {
		expect(
			Math.abs(barTop - bottom - 8),
			`cell ${at + 1} stops ${(barTop - bottom).toFixed(1)}px above the bar`
		).toBeLessThanOrEqual(1);
	}
});

test('a cell is the shape of its own picture, so nothing is padded or cropped', async ({
	page
}) => {
	/* A cell is the picture's own shape, so neither ground beside it nor cropped sides. */
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await expect(cells(page).first().locator('video, img')).toBeAttached();
	await settled(page);

	/* Against what the LIBRARY says: the stream 404s, and a file that does not play still gets the
	   right shape. */
	const wanted = CLIP.width / CLIP.height;
	const drawn = await page.evaluate(() =>
		[...document.querySelectorAll('section[aria-label^="Cell "]')].map((cell) => {
			const picture = cell.querySelector('video, img') as HTMLElement | null;
			const box = cell.getBoundingClientRect();
			return {
				ratio: box.width / box.height,
				fit: picture ? getComputedStyle(picture).objectFit : 'no picture'
			};
		})
	);

	expect(drawn.length).toBe(2);
	for (const [at, one] of drawn.entries()) {
		expect(
			Math.abs(one.ratio - wanted),
			`cell ${at + 1} is not the shape of its picture: ${one.ratio.toFixed(3)} against ${wanted.toFixed(3)}`
		).toBeLessThan(0.02);
		// Nothing is asked to crop, at any width.
		expect(one.fit).toBe('contain');
	}
});

test('a bar on a narrow cell stays on that cell', async ({ page }) => {
	/* A cell is as wide as its picture, and the bar's controls must not hang over the next feed. */
	await page.goto('/theater');
	await chooseLayout(page, 'Grid 1x3');
	await expect(cells(page)).toHaveCount(3);
	await cells(page).first().hover();
	await settled(page);

	const escaped = await page.evaluate(() => {
		const out: string[] = [];
		for (const cell of document.querySelectorAll('section[aria-label^="Cell "]')) {
			const bar = cell.querySelector('.player-bar');
			if (!bar) continue;
			const edge = cell.getBoundingClientRect();
			for (const node of bar.querySelectorAll('button, input')) {
				if (!node.checkVisibility({ visibilityProperty: true, opacityProperty: true })) continue;
				const box = node.getBoundingClientRect();
				if (box.width < 1) continue;
				if (box.left < edge.left - 1 || box.right > edge.right + 1) {
					const name =
						node.getAttribute('aria-label') ??
						node.querySelector('[aria-label]')?.getAttribute('aria-label') ??
						node.className;
					out.push(`${name} runs from ${Math.round(box.left)} to ${Math.round(box.right)}`);
				}
			}
		}
		return out;
	});

	expect(escaped, 'a control on the bar is drawn outside its own cell').toEqual([]);
});

/*
 * A press works everywhere on a cell: the number badge and the hearing glyph over its corner must
 * not take the pointer. The badge is mostly invisible, so it would eat presses unseen.
 */
test('a press lands on the cell even where its number sits', async ({ page }) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await settled(page);
	await stopped(page, cells(page).nth(1));

	/* The badge's own box, from the page. */
	const corner = await page.evaluate(() => {
		const cell = document.querySelectorAll('section[aria-label^="Cell "]')[1];
		const badge = cell?.querySelector('.at');
		if (!badge) return null;
		const box = badge.getBoundingClientRect();
		const x = box.left + box.width / 2;
		const y = box.top + box.height / 2;
		const hit = document.elementFromPoint(x, y);
		return {
			x,
			y,
			reaches: hit?.closest('video, img.picture, canvas.picture, .say-press') !== null
		};
	});

	expect(corner, 'no number was drawn on the cell').not.toBeNull();
	/* The hit test first, so a failure says why. */
	expect(corner!.reaches, 'something over the cell takes the pointer where the number sits').toBe(
		true
	);

	await page.mouse.click(corner!.x, corner!.y);
	await expect(cells(page).nth(1)).toHaveClass(/chosen/);
});

/*
 * Every preview stays inside the strip: a centred row that overflows cannot be scrolled back past
 * its start. Previews shrink today; `justify-content: safe center` guards the other side.
 */
test('every preview stays inside the strip, at any picture width', async ({ page }) => {
	/* Wider than the strip can hold, registered after `serveLibrary` so it wins. */
	const WIDE = { ...CLIP, width: 1920, height: 400 };
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [WIDE], total: 1, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/t1', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(WIDE) })
	);

	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await chooseLayout(page, 'Stage View 1x1');
	await expect(cells(page)).toHaveCount(6);
	await settled(page);
	await stopped(page, cells(page).nth(5));

	const escaped = await page.evaluate(() => {
		const strip = document.querySelector('.strip');
		if (!strip) return null;
		// Wound back to the start.
		strip.scrollLeft = 0;
		const box = strip.getBoundingClientRect();
		const out: string[] = [];
		for (const cell of strip.querySelectorAll('section[aria-label^="Cell "]')) {
			const at = cell.getBoundingClientRect();
			if (at.left < box.left - 1 || at.right > box.right + 1) {
				out.push(
					`${cell.getAttribute('aria-label')} runs from ${Math.round(at.left)} to ` +
						`${Math.round(at.right)} in a strip from ${Math.round(box.left)} to ${Math.round(box.right)}`
				);
			}
		}
		return out;
	});

	expect(escaped, 'no strip was drawn').not.toBeNull();
	expect(escaped, 'a preview is outside the strip and cannot be scrolled to').toEqual([]);
});

/* The strip takes the foot while the chrome is hidden, and its top and the feeds hold still. */
test('the strip grows into the foot while the chrome is hidden, and the feeds above it hold still', async ({
	page
}) => {
	const PORTRAIT = { ...CLIP, width: 1080, height: 1920 };
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [PORTRAIT], total: 1, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/t1', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PORTRAIT) })
	);
	await page.setViewportSize({ width: 1800, height: 1000 });
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await chooseLayout(page, 'Stage View 1x3');
	await expect(cells(page)).toHaveCount(8);

	await page.keyboard.press('f');
	await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(true);
	await page.keyboard.press('b');
	await expect(page.locator('.stage-bar')).toBeHidden();
	await settled(page);
	await stopped(page, page.locator('.strip'));

	const measure = () =>
		page.evaluate(() => {
			const round = (n: number) => Math.round(n * 10) / 10;
			const wall = document.querySelector('.wall')!.getBoundingClientRect();
			const strip = document.querySelector('.strip')!.getBoundingClientRect();
			const bar = document.querySelector('.stage-bar')?.getBoundingClientRect();
			return {
				wallBottom: round(wall.bottom),
				stripTop: round(strip.top),
				stripBottom: round(strip.bottom),
				stripTall: round(strip.height),
				barTop: bar ? round(bar.top) : null,
				feeds: [...document.querySelectorAll('.grid section[aria-label^="Cell "]')].map((one) => {
					const box = one.getBoundingClientRect();
					return [round(box.left), round(box.top), round(box.width), round(box.height)];
				})
			};
		});

	const hidden = await measure();
	expect(
		Math.abs(hidden.wallBottom - hidden.stripBottom),
		`the strip stops ${(hidden.wallBottom - hidden.stripBottom).toFixed(1)}px short of the foot`
	).toBeLessThanOrEqual(1);

	/* Reached for, then rested on, which holds it up whatever the idle clock says. */
	await reachFor(page, 'bottom', page.locator('.stage-bar'));
	await page.locator('.stage-bar').hover();
	await stopped(page, page.locator('.strip'));
	const up = await measure();

	expect(up.barTop, 'the bar is not drawn').not.toBeNull();
	expect(up.stripBottom, 'the strip sits behind the bar').toBeLessThanOrEqual(up.barTop! + 1);
	expect(hidden.stripTall, 'the previews did not grow into the band').toBeGreaterThan(
		up.stripTall + 20
	);
	expect(
		Math.abs(up.stripTop - hidden.stripTop),
		'the strip slid rather than grew'
	).toBeLessThanOrEqual(1);
	/* The feeds start lower with the top bar back; the seam with the strip does not move. */
	expect(up.feeds.length).toBe(hidden.feeds.length);
	for (const [at, feed] of up.feeds.entries()) {
		const before = hidden.feeds[at]!;
		expect(
			Math.abs(feed[1]! + feed[3]! - (before[1]! + before[3]!)),
			`feed ${at + 1}'s foot moved when the chrome came back`
		).toBeLessThanOrEqual(1);
	}
});

/* Pointing at a facet washes the cell the panel is editing, and no other. */
test('pointing at a facet value washes the cell it will narrow, and only that one', async ({
	page
}) => {
	await page.route('**/api/assets/facets*', (route) => {
		const facet = new URL(route.request().url()).searchParams.get('facet') ?? '';
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ facet, values: [{ value: 'runway', count: 1 }] })
		});
	});
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);

	await page.getByRole('button', { name: 'Filter', exact: true }).click();
	const value = page.locator('.column .value').first();
	await expect(value).toBeVisible();

	await value.hover();
	await expect(cells(page).nth(0).locator('.aimed'), 'the chosen cell was not lit').toHaveCount(1);
	await expect(
		cells(page).nth(1).locator('.aimed'),
		'a cell the panel is not editing was lit'
	).toHaveCount(0);

	await page.mouse.move(1, 1);
	await expect(cells(page).nth(0).locator('.aimed')).toHaveCount(0);
});

test('at a laptop width the screen row holds the menus, drawn and pressable with the pointer off the wall', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 768 });
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await page.mouse.move(1361, 5);

	const sort = page.getByRole('button', { name: 'Sort by' });
	await expect(sort).toBeVisible();
	await sort.click({ timeout: 3000 });
	// A press before the row's handler is bound opens nothing: once more.
	const opened = await page
		.getByRole('listbox')
		.waitFor({ state: 'visible', timeout: 2000 })
		.then(
			() => true,
			() => false
		);
	if (!opened) await sort.click({ timeout: 3000 });
	await expect(page.getByRole('listbox')).toBeVisible();
});

test('the keyboard is told Portrait and Landscape, as the pointer is', async ({ page }) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await page.mouse.move(1, 1);

	await page.getByRole('button', { name: 'Layouts', exact: true }).focus();
	await page.keyboard.press('Enter');
	await page.keyboard.press('Home');
	const highlighted = page.locator('[role=option][data-highlighted]');
	await page.keyboard.press('ArrowDown');
	await expect(highlighted).toContainText('Grid 1x2 (P)');
	await expect(page.getByRole('tooltip').filter({ hasText: 'Portrait' })).toBeVisible();

	await page.keyboard.press('ArrowDown');
	await expect(highlighted).toContainText('Grid 1x3');
	await page.keyboard.press('ArrowDown');
	await expect(highlighted).toContainText('Grid 1x2 (L)');
	await expect(page.getByRole('tooltip').filter({ hasText: 'Landscape' })).toBeVisible();
});

test('the bar leads with the transport and keeps Shuffle in its drawer', async ({ page }) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await reachFor(page, 'bottom', page.locator('.stage-bar'));
	const labels = await page
		.locator('.stage-bar .row button')
		.evaluateAll((buttons) =>
			buttons.map((one) => one.getAttribute('aria-label') ?? one.textContent?.trim() ?? '')
		);
	expect(labels.slice(0, 2)).toEqual(['Nothing before this', 'Play']);
	expect(labels).not.toContain('Shuffle');
});

test('a filled 1x1 Portrait cell rises as the bars leave and never dips first', async ({
	page
}) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await chooseLayout(page, 'Grid 1x1');
	await expect(cells(page)).toHaveCount(1);
	await cells(page).first().click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Shape' }).hover();
	await page
		.getByRole('menuitem', { name: /Portrait/ })
		.first()
		.click();

	await page.keyboard.press('f');
	await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(true);
	await reachFor(page, 'top', page.getByRole('button', { name: 'Layouts', exact: true }));
	await settled(page);
	await stopped(page, cells(page).first());

	const sampling = page.evaluate(
		() =>
			new Promise<number[]>((resolve) => {
				const tops: number[] = [];
				const frame = () => {
					const cell = document.querySelector('section[aria-label="Cell 1"]')!;
					tops.push(cell.getBoundingClientRect().top);
					const moved = tops.some((top) => top !== tops[0]);
					const still = moved && tops.slice(-30).every((top) => top === tops.at(-1));
					if (still || tops.length > 600) resolve(tops);
					else requestAnimationFrame(frame);
				};
				requestAnimationFrame(frame);
			})
	);
	await page.keyboard.press('b');
	const tops = await sampling;

	const end = tops.at(-1)!;
	expect(end, 'the cell did not move when the bars left').toBeLessThan(tops[0]);
	const away = Math.max(...tops) - tops[0];
	expect(away, `the cell went ${away.toFixed(1)}px down before rising`).toBeLessThanOrEqual(0.5);
});

test('a refused folder is asked for on the token, and a name beginning with a minus is a name', async ({
	page
}) => {
	const asked: string[] = [];
	page.on('request', (request) => {
		const url = new URL(request.url());
		if (url.pathname === '/api/assets') asked.push(url.searchParams.get('q') ?? '');
	});
	await page.route('**/api/assets/facets*', (route) => {
		const facet = new URL(route.request().url()).searchParams.get('facet') ?? '';
		const value = { in: 'Raw Cuts', tags: '-cut' }[facet];
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ facet, values: value ? [{ value, count: 1 }] : [] })
		});
	});
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await page.getByRole('button', { name: 'Filter', exact: true }).click();

	// A tag named with a leading minus, picked: asked for in quotes, and its chip is not refused.
	await page.locator('.column .value', { hasText: '-cut' }).first().click();
	await expect.poll(() => asked.some((q) => q.includes('tags:"-cut"'))).toBe(true);
	await expect(page.locator('.value', { hasText: /^-cut$/ }).first()).toBeVisible();
	await expect(page.locator('.value.struck', { hasText: '-cut' })).toHaveCount(0);

	// A folder whose name holds a space, picked and then refused from its chip.
	await page.locator('.column .value', { hasText: 'Raw Cuts' }).first().click();
	await page
		.getByRole('button', { name: /^in:\s*Raw Cuts$/ })
		.first()
		.click();
	await expect.poll(() => asked.some((q) => q.includes('-in:"Raw Cuts"'))).toBe(true);
	expect(asked.some((q) => q.includes('in:"-Raw Cuts"'))).toBe(false);
	await expect(page.locator('.value.struck', { hasText: 'Raw Cuts' })).toHaveCount(1);
});
