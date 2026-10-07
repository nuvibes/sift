import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';
import { rewrite } from './routes';

/* The wall, in a real browser.
 *
 * Everything here is a claim no unit test can make. Filling the screen is the browser's own
 * fullscreen, which decides what still composites, and Theater's controls live on the shared
 * bar, so whether they survive it is the whole question. Building a wall is a grid laying itself
 * out, which jsdom has no opinion about at all: it reports every box as zero, so a shape that draws
 * as one column and a shape that draws as three would look identical to it.
 *
 * The library is intercepted rather than imported. What is under test is the wall.
 */

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
	// Without this the cell is drawn as still importing, and an absence assertion would pass
	// against a screen with nothing on it.
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
	/*
	 * A wall of this account's own, cleared per test.
	 *
	 * Every browser test shares ONE admin account, so per-account state leaks between them and the
	 * set of failures moves from run to run. Intercepted rather than cleared up afterwards, which is
	 * the only version of this that does not depend on the order tests happen to run in.
	 */
	await page.route('**/api/theater/arrangements', (route) =>
		route.request().method() === 'GET'
			? route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[]}' })
			: route.continue()
	);

	/*
	 * The wall's own preferences, held for this test alone.
	 *
	 * Theater remembers the shape it was left in as an account preference, and the account is the
	 * one every browser test shares. Tests inside a file run in parallel here, so the test that
	 * picks a four-cell layout would write that preference while the test that asserts the shape a
	 * wall opens on is loading the screen, and which of them failed would depend on how the machine
	 * felt.
	 *
	 * A store per test rather than a pin, because a pin is wrong in the other direction: picking a
	 * layout has to survive being read back within the same test, and a value forced to the default
	 * on every read means the screen never keeps anything anybody chose.
	 */
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
			// A write of anything else is somebody else's business and is passed through untouched.
			if (keys.length === 0 || !keys.every((key) => key in mine)) return route.continue();
			for (const key of keys) mine[key] = values[key];
			return route.fulfill({ status: 204, body: '' });
		}

		/* Walked rather than reached into by path: the shape of this response is the settings
		   screen's business and may change. What is stable is that a setting is an object
		   with a `key`. */
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
 * Wait for every animation that is going to END to end.
 *
 * Cells arrive rather than appearing, and a box measured mid-flight is in the place the animation
 * started from. Asked of the browser rather than slept for, so it is the real end of the movement.
 *
 * The filter is the whole of it. `getAnimations()` returns the REPEATING ones too (the pulse on a
 * cell that is still finding something to play), and their `finished` promise by definition never
 * settles, so waiting on it takes the whole test timeout. Under load the loading state is still up
 * when this runs. `endTime` is the browser's own answer to "will this stop", and it is `Infinity`
 * for exactly the animations that will not.
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

/**
 * Wait until an element's box has stopped moving.
 *
 * Two readings that agree, rather than a wait of any length. `settled` above asks the document for
 * the animations it is running AT THAT MOMENT, which is exactly nothing when the element has not been
 * drawn yet, and on a loaded machine that is the ordinary case.
 */
async function stopped(page: Page, target: ReturnType<Page['locator']>): Promise<void> {
	let previous = '';
	for (let attempt = 0; attempt < 40; attempt += 1) {
		const now = JSON.stringify(await target.boundingBox());
		if (now === previous && now !== 'null') return;
		previous = now;
		await page.waitForTimeout(50);
	}
}

/** How many cells the wall is drawing. */
function cells(page: Page) {
	return page.locator('section[aria-label^="Cell "]');
}

/*
 * REACH FOR THE CHROME, the way a hand does, and wait until it is there.
 *
 * The wall's bars answer the BANDS at the top and the foot of the wall rather than any movement
 * anywhere: the middle of a wall is picture and nothing else, so a bar that came back for a pointer
 * crossing it would be up almost all the time, lying across videos somebody is watching. A test
 * that wants a control on a bar has to reach for it where a hand would.
 *
 * Until it does, the row is not merely faded: it is out of the accessibility tree, so `getByRole`
 * finds NOTHING and the failure reads as the control having been deleted.
 *
 * Retried as a whole, because the chrome also runs an idle clock: a single move followed by a slow
 * assertion can watch the bar go again before it is looked at, which is a failure about how busy
 * the machine is. Two moves per attempt, since a pointer put back where it already is reports
 * nothing.
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
	// Wide enough for a wall. Theater refuses to draw one below a laptop width, and says so.
	await page.setViewportSize({ width: 1400, height: 900 });
});

/* Choosing a shape, through the menu on the screen bar (a workspace is a panel and a list you
 * pick one of is a menu), so the options are `option`s and the menu shuts on the choice.
 */
async function chooseLayout(page: Page, named: string) {
	await page.getByRole('button', { name: 'Layouts', exact: true }).click();
	await page.getByRole('option', { name: named, exact: true }).click();
}

test('a wall opens on two feeds, laid out side by side', async ({ page }) => {
	/*
	 * The shape, end to end and in a browser that actually lays a grid out. The assertion is on
	 * where the cells LAND. A wall with the right number of cells that puts them all in one column
	 * is the failure this catches, and jsdom reports every box as zero so nothing else can see it.
	 */
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	/*
	 * A cell ARRIVES: it fades and scales in, so it is measurably in the wrong place for the
	 * length of the animation, and two cells side by side then differ vertically and read as
	 * stacked.
	 *
	 * `settled` is not enough on its own: it awaits the animations RUNNING WHEN IT IS CALLED, so on
	 * a loaded machine, where the cells are drawn a beat after the count is satisfied, it awaits an
	 * empty list and returns immediately. Waiting for the boxes themselves to stop moving asks the
	 * question of the thing being measured: two readings that agree is what "settled" means here.
	 */
	await settled(page);
	await stopped(page, cells(page).nth(1));

	const one = await cells(page).nth(0).boundingBox();
	const two = await cells(page).nth(1).boundingBox();
	expect(one).not.toBeNull();
	expect(two).not.toBeNull();
	// Side by side: the second starts to the right of the first and they share a top edge.
	expect(two!.x).toBeGreaterThan(one!.x);
	expect(Math.abs(two!.y - one!.y)).toBeLessThan(2);
});

test('a feed is removed from its own menu, and the last one stays', async ({ page }) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);

	await cells(page).nth(1).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Remove this feed' }).click();
	await expect(cells(page)).toHaveCount(1);

	// The last one cannot go: a wall of nothing is not a state anybody meant to reach, and there
	// would be nothing left on screen to pick a shape for.
	await cells(page).first().click({ button: 'right' });
	await expect(page.getByRole('menuitem', { name: 'Remove this feed' })).toBeDisabled();
});

test('the controls survive filling the screen, and stay above it', async ({ page }) => {
	/*
	 * Why the bar sits inside the wall's fullscreen element.
	 *
	 * Filling the screen draws the fullscreen element and its contents and NOTHING else on the
	 * page. Theater's controls are on the shared bar, so the bar has to be inside whatever fills
	 * the window, and this is the only test that can tell.
	 */
	await page.goto('/theater');
	const layout = page.getByRole('button', { name: 'Layouts', exact: true });
	await expect(layout).toBeVisible();

	const before = await layout.boundingBox();
	await page.keyboard.press('f');
	await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(true);

	/* Woken first: the bar goes quiet on its own after two and a half seconds of nothing
	   happening, which is the test below. Filling the screen and then measuring can take longer
	   than that on a loaded machine, and a bar gone by the time it is measured is a null box
	   that says nothing about what went wrong. A move re-arms the clock and the measurement
	   lands well inside it. */
	await page.mouse.move(700, 500);
	await expect(layout, 'the controls went off the window with the rest of the page').toBeVisible();
	const after = await layout.boundingBox();
	const height = page.viewportSize()!.height;
	expect(before!.y, 'the bar was not at the top to begin with').toBeLessThan(height / 2);
	expect(after!.y, 'the bar left the top of the window').toBeLessThan(height / 2);

	/* And it is IN FLOW rather than over the wall: absolute along the bottom it would cover the
	   bottom of every feed together, worse on a wall than shortening them, because a wall is
	   several pictures. The wall starting below the bar is the whole difference. */
	const wall = await cells(page).first().boundingBox();
	expect(wall!.y, 'the bar is drawn over the wall rather than above it').toBeGreaterThanOrEqual(
		after!.y + after!.height - 1
	);
});

test('the bar goes quiet while filled, and reaching for it brings it back', async ({ page }) => {
	/* It goes on its own on an idle clock and comes back when somebody reaches for it (see
	   `reachFor`); `B` is the same dismissal from the keyboard, for a machine with no pointer to
	   move.

	   The bar is put UP before either height is read: measured with the bar already down, "with"
	   and "without" would differ by a coincidence of a fraction of a pixel. */
	await page.goto('/theater');
	// The key belongs to the screen, which binds it on mount: pressed before the wall is drawn it
	// reaches nothing at all.
	await expect(cells(page)).toHaveCount(2);
	await page.keyboard.press('f');
	await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(true);

	const layout = page.getByRole('button', { name: 'Layouts', exact: true });
	/* The WALL rather than a cell. A cell is the shape of its picture and can be limited by the
	   width rather than by the height, in which case a taller wall does not make it taller. So
	   a cell is the wrong thing to measure this with. */
	const wall = page.locator('.wall');

	await reachFor(page, 'top', layout);
	const tallWithBar = (await wall.boundingBox())!.height;

	await page.keyboard.press('b');
	await expect(layout).toBeHidden();

	/* And the wall takes the space. `display: none` rather than a fade is the point of it: a bar
	   that is merely invisible holds its row open and the wall stays the smaller size for a reason
	   nobody can see. */
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
	/*
	 * Panels open over the top of the wall, and this is the measurement that tells that apart from
	 * panels in the page's flow.
	 *
	 * In the flow, opening one pushes everything under it down the screen: a couple of hundred
	 * pixels each way, on a wall of moving pictures, twice per decision. Over the top, the wall
	 * does not move at all.
	 *
	 * Both halves are asserted. Without the second, a panel that failed to open at all would pass:
	 * nothing moving is exactly what this expects.
	 */
	await page.goto('/theater');
	const wall = cells(page).first();
	/* Settled BEFORE the first measurement as well as after it. A cell is sized by the picture in it,
	   so the wall grows as the feeds arrive, and a `before` taken the moment the element exists is
	   the wall part-way through settling, which would make the comparison below a race with loading
	   rather than a statement about the panel. */
	await settled(page);
	const before = await wall.boundingBox();

	/* Cell Groups rather than Layout, because Layout is a MENU, and a menu is not what this
	   rule is about. What has to stay out of the flow is a panel: a drawer the width of the row that
	   is open while somebody works in it. Theater has two and this is its own. */
	await page.getByRole('button', { name: 'Saved Layouts', exact: true }).click();
	const panel = page.locator('.drawer').first();
	await expect(panel).toBeVisible();
	// The panel animates in, and a measurement taken mid-animation is the first frame rather than
	// the end of it.
	await settled(page);

	/*
	 * The SIZE, not the position. A cell is centred in the wall, so its top edge moves when anything
	 * about the box it is centred in changes, including the chips row above collapsing, which is a
	 * separate rule of its own. What "pushed down" would cost is space: a panel in the flow makes the
	 * wall shorter for as long as it is open, and that is the thing to hold.
	 */
	const after = await wall.boundingBox();
	expect(
		Math.round(after!.height),
		'the panel took height from the wall, so it is in the flow rather than over it'
	).toBeGreaterThanOrEqual(Math.round(before!.height));

	/*
	 * And the mechanism, rather than an overlap that happens to be visible on this screen.
	 *
	 * A short panel over a tall wall may not reach it (the Layout panel is one row of choices and
	 * ends well above the first cell), so "does it overlap" answers no on a screen where the rule is
	 * being obeyed. What is true either way is that the panel is out of the flow: that is the whole
	 * of the rule, and a panel put back in the flow fails this whatever its height.
	 */
	const position = await page
		.locator('.drawer')
		.first()
		.evaluate((element) => getComputedStyle(element).position);
	expect(position, 'the panel is in the flow, so it pushes whatever is under it').toBe('absolute');
});

test('a layout is chosen from the menu and applies immediately', async ({ page }) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);

	await chooseLayout(page, '2x2');

	await expect(cells(page)).toHaveCount(4);
	// And it goes back, which is what a list you pick one of has to be able to do.
	await chooseLayout(page, '1x2 (P)');
	await expect(cells(page)).toHaveCount(2);

	/* The retired shapes are still ACCEPTED by the server, because saved walls are filed under
	   them. See the gate that holds those two lists apart. */
	await page.getByRole('button', { name: 'Layouts', exact: true }).click();
	for (const gone of ['One', 'Stacked', 'Stacked three', 'One above two', 'Two above one']) {
		await expect(page.getByRole('option', { name: gone, exact: true })).toHaveCount(0);
	}
	await expect(page.getByRole('option', { name: '2x2', exact: true })).toBeVisible();
	await expect(page.getByRole('option', { name: '1x2 (L)', exact: true })).toBeVisible();
});

test('the whole wall goes to the corner, not one cell of it', async ({ page }) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);

	/* The corner and the screen are on the wall's OWN bar, at the foot of the wall, so that a
	   filled wall is not the one screen with no way off it. That bar is down until it is reached
	   for. See `reachFor`. */
	const corner = page.getByRole('button', { name: 'Open mini player', exact: true });
	await reachFor(page, 'bottom', corner);
	await corner.click();

	const panel = page.getByRole('region', { name: 'Mini player' });
	await expect(panel).toBeVisible();
	// Both cells, in the panel, not a wall cut down to whichever cell was in front, which is a
	// different thing from what was being watched.
	await expect(panel.locator('section[aria-label^="Cell "]')).toHaveCount(2);
});

test('the wall in the corner grows and shrinks with the panel', async ({ page }) => {
	/*
	 * The wall inside the panel resizes with it.
	 *
	 * The wall asks to be `flex: 1` of whatever holds it, so the panel's picture box has to be a
	 * flex container; as an ordinary block the flex is ignored and the wall takes its height from
	 * its own contents. That is circular: a feed's size is worked out FROM the height of the wall,
	 * so the two agree on whatever they started at and stay there, and dragging the corner moves
	 * the panel and nothing in it.
	 *
	 * Only a browser can answer this. Every part of it (the flex, the measured box, the height
	 * fed back into the grid) is layout, and layout is exactly what a component test does not do.
	 */
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

	/* The panel arrives from the full-size wall on a transition, so it is measured once it has
	   come to rest: two looks a tenth of a second apart that agree. */
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

	// Drag the bottom-right corner out, the way somebody makes the panel bigger.
	const grip = await box(panel.locator('.corner.se'));
	await page.mouse.move(grip.x + 8, grip.y + 8);
	await page.mouse.down();
	await page.mouse.move(grip.x + 8 - 320, grip.y + 8 - 260, { steps: 6 });
	await page.mouse.move(grip.x + 8 + 320, grip.y + 8 + 260, { steps: 12 });
	await page.mouse.up();

	const grewTo = await box(panel);
	expect(grewTo.width, 'the panel itself did not resize').toBeGreaterThan(panelAt.width + 100);

	/* The feed has to have grown WITH it, and by roughly as much: a feed that gained a few pixels
	   while the panel gained three hundred is the same fault wearing a smaller number. */
	await expect
		.poll(async () => (await box(feed)).height, {
			message: 'the wall did not grow with the panel it is in'
		})
		.toBeGreaterThan(startedAt.height + 100);

	// And back down again. Growing but never shrinking is what a missing `min-block-size: 0` looks
	// like, and it passes the half of this test above.
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
	/*
	 * A scrollable box inside a cell is always a mistake here. A cell is sized by the grid and
	 * everything in it is meant to fit, so anything that can scroll is something that has been
	 * drawn larger than the space it was given.
	 */
	await page.goto('/theater');
	await chooseLayout(page, '1x3');
	await expect(cells(page)).toHaveCount(3);
	await cells(page).first().hover();
	await settled(page);

	const scrollers = await page.evaluate(() => {
		const found: string[] = [];
		for (const cell of document.querySelectorAll('section[aria-label^="Cell "]')) {
			for (const node of cell.querySelectorAll('*')) {
				const box = node as HTMLElement;
				const style = getComputedStyle(box);
				// A BAR, not merely content that does not fit. `hidden` clips without drawing one, and
				// clipping is what a stage is for. What must not happen is a strip of browser chrome
				// down the side of a picture.
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
	/*
	 * A whole bar going dead is the kind of fault where nothing is hidden, nothing is disabled, and
	 * every control still lights up on hover: a transparent layer over the picture (the one the
	 * four add-a-feed arrows sit on), given `pointer-events: auto` by a rule meant to fade it,
	 * drawn after the bar at the same stacking level, wins every hit test on the bar underneath.
	 *
	 * Asked as "does this control receive its own click", which is the only question that catches a
	 * fault whose whole nature is that the screen looks right.
	 */
	await page.goto('/theater');
	await cells(page).first().hover();
	await settled(page);

	const swallowed = await page.evaluate(() => {
		const bar = document.querySelector('.player-bar');
		if (!bar) return ['there is no bar at all'];
		const lost: string[] = [];
		for (const node of bar.querySelectorAll('button, input')) {
			/* Only what is actually SHOWING. The volume slider lives in a popup that is laid out at
			   full size and hidden with `visibility`, so it has a box the whole time and cannot be
			   clicked until the speaker is hovered, which is correct, and would read here as the
			   very fault this test exists to catch. */
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
	/*
	 * The two halves of "the same bar" that a shared component does not guarantee on its own.
	 *
	 * The play button is deliberately larger than the rest of the row, and a rule one class less
	 * specific than the rule dressing every icon on the bar would lose, silently, drawing a play
	 * button the size of everything else with every test still green. Measured rather than read: a
	 * size is a fact about the running page.
	 */
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
 * PORTRAIT FEEDS REACH THE FOOT OF A FILLED SCREEN, AND STOP 8PX ABOVE THE BAR WHILE IT IS UP.
 *
 * Without a strip the grid holds the bar's band only while the bar is up, so with the chrome
 * hidden nothing is reserved.
 *
 * Wide enough that three 9:16 feeds are limited by the HEIGHT, not the width. At 1400 across they
 * would come down to fit the width, and a wall that is width-limited never reaches the foot of the
 * screen whatever the bar does, which would make this pass or fail for the wrong reason.
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
	await chooseLayout(page, '1x3');
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
	/*
	 * A cell is the picture's own shape.
	 *
	 * Fitting the picture inside a cell of a fixed width leaves ground down either side of a
	 * portrait clip; filling that cell instead has no ground and cuts the sides off the picture. A
	 * cell that is the picture's own shape has neither, and it is a claim only a browser can
	 * check, because it is a grid resolving a track's width from a row's height.
	 */
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await expect(cells(page).first().locator('video, img')).toBeAttached();
	await settled(page);

	/* Measured against what the LIBRARY says the file is, which is where the cell takes its shape
	   from: the fixture's stream is a 404, so the element never loads and never reports a size of
	   its own. That is the case worth pinning: a cell has to be the right shape for a file that
	   turns out not to play, or a wall of broken files is a wall of differently-wrong rectangles. */
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
		// And nothing is asked to crop, at any width. The cell matching means it never has to.
		expect(one.fit).toBe('contain');
	}
});

test('a bar on a narrow cell stays on that cell', async ({ page }) => {
	/*
	 * A cell is as wide as its picture, so a wall of landscape clips leaves each one far less room
	 * than the bar wants. The row does not wrap and the frame does not scroll, so the controls at
	 * its right-hand end would hang past the edge of the cell and over the NEXT feed: still clickable,
	 * on the wrong picture, and half of one clipped away while the other half worked.
	 */
	await page.goto('/theater');
	await chooseLayout(page, '1x3');
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
 * A PRESS ON A CELL HAS TO WORK EVERYWHERE ON THE CELL.
 *
 * A press chooses a feed or a preview, and a double press on a preview brings it up into the wall,
 * all handled by the picture. The number badge and the `you are hearing this` glyph sit over that picture's top-left
 * corner and must not take the pointer, or every press that lands on them reaches nothing: no mark,
 * no sound, no cell chosen, nothing on screen to say why.
 *
 * The badge spends nearly all its life at nought opacity, which would make it worse than a visible
 * hole: an invisible square eating presses aimed at a picture, and worst on the small preview tiles
 * somebody aims at.
 *
 * Only a browser can make this claim. jsdom reports every box as zero and hit-tests nothing, so no
 * unit test can tell a badge that takes the pointer from one that does not.
 */
test('a press lands on the cell even where its number sits', async ({ page }) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await settled(page);
	await stopped(page, cells(page).nth(1));

	/* The badge's own box, from the page rather than from the stylesheet: what is being asked is
	   whether the pointer reaches the cell THERE, and only the browser knows where there is. */
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
	/* The hit test on its own, first: it says WHY when the press below fails, instead of leaving a
	   failure that could equally be the wall having ignored a perfectly good click. */
	expect(corner!.reaches, 'something over the cell takes the pointer where the number sits').toBe(
		true
	);

	await page.mouse.click(corner!.x, corner!.y);
	await expect(cells(page).nth(1)).toHaveClass(/chosen/);
});

/*
 * EVERY PREVIEW IN THE STRIP IS INSIDE THE STRIP, however wide the pictures are.
 *
 * The strip is a centred row that declares its own sideways scrolling, and those two do not agree
 * by default: once a row is wider than its box there is no free space left to centre into, so a
 * centred row overflows at BOTH ends, and a scroll container cannot be scrolled back past its own
 * start edge. Whatever is pushed off the start is then reachable by nothing at all.
 *
 * That trap does NOT bite today: a cell in the strip carries `min-inline-size: 0`, so the previews
 * SHRINK to whatever the strip gives them rather than overflowing it (`scrollWidth` equals
 * `clientWidth` even with the very wide clip below).
 *
 * So this measures the property that has to hold whichever way the sizing goes (every preview
 * inside the strip's own box), and it is the thing that would break the day somebody takes the
 * shrink off. `justify-content: safe center` on the strip is the guard on the other side of it.
 */
test('every preview stays inside the strip, at any picture width', async ({ page }) => {
	/* A far wider clip than the shared fixture, registered AFTER `serveLibrary` so it wins:
	   Playwright matches routes in reverse order of registration. Five previews of this shape want
	   about three thousand pixels, against a strip of roughly eleven hundred. */
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
	await chooseLayout(page, 'Center stage 1x1');
	// One in focus and five underneath.
	await expect(cells(page)).toHaveCount(6);
	await settled(page);
	await stopped(page, cells(page).nth(5));

	const escaped = await page.evaluate(() => {
		const strip = document.querySelector('.strip');
		if (!strip) return null;
		// Wound back to the start, which is as far as anybody can get it.
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

/*
 * THE STRIP FILLS THE FOOT WHILE THE CHROME IS HIDDEN, AND COMES BACK UP WITH IT.
 *
 * The band under the previews is the bar's; with the bar gone it is handed to the strip's own
 * height, so the previews use the whole foot of the screen.
 *
 * Both states are measured, and the property that makes the change safe is measured with them: the
 * strip's TOP edge and every feed in the grid stay exactly where they were.
 */
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
	await chooseLayout(page, 'Center stage 1x3');
	// Three in focus and five underneath.
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

	/* The bar comes back: reached for at the foot, then rested on, which holds it up whatever the
	   idle clock says, so the measurement is of the chrome being up rather than of a race. */
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
	/* The top bar is part of the page's layout when it returns, so the feeds start 70px lower and
	   are drawn that much shorter; what must not move is the SEAM: every feed's foot, where the
	   strip begins, is where it was. */
	expect(up.feeds.length).toBe(hidden.feeds.length);
	for (const [at, feed] of up.feeds.entries()) {
		const before = hidden.feeds[at]!;
		expect(
			Math.abs(feed[1]! + feed[3]! - (before[1]! + before[3]!)),
			`feed ${at + 1}'s foot moved when the chrome came back`
		).toBeLessThanOrEqual(1);
	}
});

/*
 * POINTING AT A FACET LIGHTS THE CELL IT WILL NARROW.
 *
 * The same wash a hover over the play or skip button gives, on the cell the panel is editing and on
 * no other.
 */
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

	// Off the columns, and the wash goes with the pointer.
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
	await expect(highlighted).toContainText('1x2 (P)');
	await expect(page.getByRole('tooltip').filter({ hasText: 'Portrait' })).toBeVisible();

	await page.keyboard.press('ArrowDown');
	await expect(highlighted).toContainText('1x2 (L)');
	await expect(page.getByRole('tooltip').filter({ hasText: 'Landscape' })).toBeVisible();
});

test('a filled 1x1 Portrait cell rises as the bars leave and never dips first', async ({
	page
}) => {
	await page.goto('/theater');
	await expect(cells(page)).toHaveCount(2);
	await chooseLayout(page, '1x1');
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
