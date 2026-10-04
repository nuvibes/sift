/*
 * Where the pager sits, and what a tile's badges look like.
 *
 * Both are geometry, so both are here rather than in the unit suite. A component test renders these
 * happily with no stylesheet at all and would agree with any arrangement whatsoever.
 *
 * ## The pager
 *
 * The pager is a footer track: OUTSIDE the box that scrolls and BELOW it. Sticky inside the
 * scroller, the rows pass underneath it; directly under the last row, a short page puts it in the
 * middle of the screen with the empty band below it instead, and a full wall has to be scrolled to
 * the bottom to find out where you are. The footer track does not make the body auto-height, which
 * is what would bring back a request loop; the loop's own guard is the last test here.
 *
 * ## The marks
 *
 * A row of badges in the corner of a tile. The claims are that the count comes first, that the row
 * is the same size at every notch of the slider, and that the crossed-out eye is drawn solid only
 * where the concealment is on that file rather than inherited from something above it.
 */
import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/** Every screen with a pager on it. Recently viewed has none and is not one of these. */
const WALLS = ['/browse', '/favorites', '/people', '/sites', '/tags', '/collections'] as const;

/** A short page of files, the shape every file wall reads. */
function files(count: number) {
	return Array.from({ length: count }, (_, at) => ({
		id: `f${at}`,
		media_type: 'video',
		width: 1920,
		height: 1080,
		duration_ms: 61_000,
		favorite: false,
		rating: null,
		concealed: false,
		thumb: true
	}));
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	/* Every file wall holds a few files. A file wall with nothing on it draws its empty state as
	   the whole screen and no pager under it (`AssetGrid`), and the library this server starts on
	   is empty, so without these the walls below would be measured with nothing to measure. The
	   entity walls draw their pager whatever they hold. */
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: files(3), total: 3, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
});

/**
 * The scrolling body and the footer, as boxes.
 *
 * `.frame-body` is the element that actually scrolls. See `PageFrame`, where the name is held
 * stable precisely so a measurement can find it.
 */
async function frame(page: Page) {
	return page.evaluate(() => {
		const box = (selector: string) => {
			const element = document.querySelector(selector);
			if (!element) return null;
			const rect = element.getBoundingClientRect();
			return { top: Math.round(rect.top), bottom: Math.round(rect.bottom) };
		};
		return {
			body: box('.frame-body'),
			footer: box('.frame-footer'),
			/* Whether the pager is a DESCENDANT of the scroller, which is the failure this
			   guards. */
			inside: Boolean(document.querySelector('.frame-body .pager, .frame-body [class*="pager"]'))
		};
	});
}

test('the pager is below the scrolling body, on every wall', async ({ page }) => {
	for (const wall of WALLS) {
		await page.goto(wall);
		await expect(page.locator('.frame-footer')).toBeVisible();

		const { body, footer, inside } = await frame(page);
		expect(body, `${wall} has no scrolling body`).not.toBeNull();
		expect(footer, `${wall} has no footer`).not.toBeNull();

		expect(inside, `${wall} draws its pager inside the scroller`).toBe(false);
		/*
		 * Below, not merely present. A footer track that collapsed to nothing, or one rendered above
		 * the body, would both still be "visible" and would both be the fault.
		 */
		expect(footer!.top, `${wall} draws its pager above the body`).toBeGreaterThanOrEqual(
			body!.bottom - 1
		);
	}
});

test('the frame reaches the bottom of the window on every wall', async ({ page }) => {
	/*
	 * The frame is `block-size: 100%`, so ANY sibling rendered after it steals exactly that
	 * element's height from the bottom of the screen. The wall is then clipped mid-row, the pager
	 * rides up, and whatever the extra element is lands under the pager looking like it has come
	 * loose. A wall description written as a `<p>` after the grid instead of passed as the
	 * header's `lede` does this.
	 *
	 * ## Why this is not asked as "the document does not scroll"
	 *
	 * That CANNOT FAIL. The shell clips its own overflow, so `documentElement.scrollHeight` never
	 * grows past `clientHeight` however much is rendered past the bottom. Measuring where the frame
	 * ENDS asks the same question of something that actually moves.
	 */
	for (const wall of [...WALLS, '/recent']) {
		await page.goto(wall);
		await expect(page.locator('.frame')).toBeVisible();

		const bottom = await page.evaluate(() =>
			Math.round(document.querySelector('.frame')!.getBoundingClientRect().bottom)
		);
		const height = page.viewportSize()!.height;
		expect(
			height - bottom,
			`${wall} ends ${height - bottom}px above the window, so something is drawn outside the frame`
		).toBeLessThanOrEqual(1);
	}
});

test('the pager lands in the same place on every wall, to the pixel', async ({ page }) => {
	/*
	 * The whole of what was asked for: one spot, whether the wall fills the window or holds four
	 * cards. Two screens agreeing is a coincidence; six is the property.
	 *
	 * The header above them is NOT the same height everywhere (an entity wall carries a sentence
	 * under its title and Browse does not), so this is a real claim about the footer being laid out
	 * from the bottom rather than following whatever is above it.
	 */
	const bottoms = new Map<string, number>();
	for (const wall of WALLS) {
		await page.goto(wall);
		await expect(page.locator('.frame-footer')).toBeVisible();
		const { footer } = await frame(page);
		bottoms.set(wall, footer!.top);
	}

	const seen = [...new Set(bottoms.values())];
	expect(
		seen.length,
		`the pager starts at different heights: ${[...bottoms].map(([w, y]) => `${w}=${y}`).join(', ')}`
	).toBe(1);
});

test('the footer band is the pager and nothing else', async ({ page }) => {
	/*
	 * The pager carries its own height (`--page-footer-height`, 48px) and carries it for
	 * exactly this purpose: the space inside that box IS the space under the last row, on every
	 * screen that pages. Padding the footer track as well would count the same gap twice and take
	 * it off the wall on every screen at once.
	 *
	 * So the track is the pager exactly. If the band should ever be taller that is one number in
	 * `app.css`, and this test goes on holding: it compares the two to each other rather than to a
	 * figure written down here.
	 */
	for (const wall of WALLS) {
		await page.goto(wall);
		await expect(page.locator('.frame-footer')).toBeVisible();

		const { track, pager } = await page.evaluate(() => {
			const height = (selector: string) => {
				const element = document.querySelector(selector);
				return element ? Math.round(element.getBoundingClientRect().height) : -1;
			};
			return { track: height('.frame-footer'), pager: height('.pager') };
		});

		expect(pager, `${wall} draws no pager`).toBeGreaterThan(0);
		expect(track - pager, `${wall} pads ${track - pager}px around its pager`).toBe(0);
	}
});

test('a short page keeps the pager at the bottom, not under the last row', async ({ page }) => {
	/*
	 * A wall of three files cannot fill the window: check the pager has NOT climbed up to meet
	 * the last row.
	 *
	 * The threshold is the important part. It is not "the pager is at y=N": that is a number that
	 * changes with the window. It is that the pager sits in the bottom quarter of the screen, which
	 * is false for a pager that tracks a two-row wall and true for one in a footer.
	 */
	await page.goto('/browse');
	await expect(page.locator('.tile')).toHaveCount(3);
	await expect(page.locator('.frame-footer')).toBeVisible();

	const { footer } = await frame(page);
	const height = page.viewportSize()!.height;
	expect(footer!.top, 'the pager climbed up to the content').toBeGreaterThan(height * 0.75);
});

/*
 * A one-pixel PNG, so a mocked tile has a picture to draw.
 *
 * The marks are mocked rather than made, and that is the only way to ask this question: the fixture
 * library has nothing shared, nothing hidden and nothing opened, so no tile in it carries a mark at
 * all, and a test against that wall, with no `.marks` element on it anywhere, cannot fail.
 * What is under test here is the ROW: which mark
 * comes first and how tall it is. Where the facts came from is not part of the claim.
 */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

/** The marks along a tile's top, the row the count leads (`TOP_ROW` in `Tile`). */
const TOP_MARKS = [
	'appearance.tile.pinned',
	'appearance.tile.views',
	'appearance.tile.o_count',
	'appearance.tile.sharing',
	'appearance.tile.hidden'
];

/** A wall of files, each carrying every mark there is, so the row is drawn at its widest. */
async function wallOfMarkedTiles(page: Page, count = 24) {
	const items = Array.from({ length: count }, (_, at) => ({
		id: `m${at}`,
		media_type: 'video',
		width: 1920,
		height: 1080,
		duration_ms: 61_000,
		favorite: false,
		rating: null,
		concealed: false,
		thumb: true,
		/* Every other tile carries a sharing mark, so the row is a different LENGTH tile to tile:
		   the arrangement a count in a fixed position would move about in. */
		shared: at % 2 === 0,
		shared_here: at % 2 === 0,
		hidden: at % 3 === 0,
		hidden_here: at % 6 === 0,
		views: at + 1
	}));
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: count, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	/*
	 * A notch up before anything is measured, and it is not incidental.
	 *
	 * These are 16:9 tiles, so at the default row height they land under 140px tall (exactly the
	 * size at which the marks are deliberately not drawn at all), and an assertion on a wall whose
	 * every mark is `display: none` measures nothing.
	 */
	await page.locator('.size input[type="range"]').fill('2');
	await expect(page.locator('.marks').first()).toBeVisible();
}

test('the count is the first mark on a tile, whatever else is drawn', async ({ page }) => {
	/*
	 * The count comes first: after the sharing mark, the number would move sideways from one tile
	 * to the next depending on whether anything had been said about that file, and a number that
	 * changes position is a number you have to look for rather than read.
	 *
	 * Read as ORDER within the row rather than as an x coordinate: the row is a flex row and the
	 * marks either side of it come and go, so the only durable claim is which child is first. The
	 * fixture alternates the sharing mark on and off for exactly that reason: a wall where every
	 * row held the same marks would pass with the count in any fixed position.
	 *
	 * Every mark along the top answers the same (on hover, the default), because a mark drawn
	 * always stands before every mark waiting for the hover, by design (`restingFirst`). The
	 * claim is about the order within one answer, so the account's answers are pinned here
	 * rather than read from whatever the shared account was left holding.
	 */
	await page.route('**/api/settings', async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		const response = await route.fetch();
		const body = (await response.json()) as {
			sections: { settings?: { key: string; value: unknown }[] }[];
		};
		for (const section of body.sections) {
			for (const one of section.settings ?? []) {
				if (TOP_MARKS.includes(one.key)) one.value = 'hover';
			}
		}
		await route.fulfill({ response, json: body });
	});
	await wallOfMarkedTiles(page);

	/*
	 * Read THROUGH the tooltip wrapper. Every mark is wrapped in one, so `.marks > *` is a `.wrap`
	 * and reading its class finds nothing to distinguish the marks by.
	 */
	const rows = await page.evaluate(() =>
		[...document.querySelectorAll('.marks')].map((row) =>
			[...row.children].map((child) => {
				const mark = child.classList.contains('mark') ? child : child.querySelector('.mark');
				return (mark ?? child).className.toString();
			})
		)
	);

	expect(rows.length, 'no tile on this wall carries any mark').toBeGreaterThan(4);
	/* The rows genuinely differ, or "the count is first" is true of a list of identical things. */
	expect(
		new Set(rows.map((row) => row.length)).size,
		'every row is the same shape'
	).toBeGreaterThan(1);
	for (const row of rows) {
		expect(row[0], `the count is not first in [${row.join(', ')}]`).toContain('seen');
	}
});

test('the marks are the same size at every notch of the slider', async ({ page }) => {
	/*
	 * A corner that stepped down below a 180px tile (4px of chip height, a step of type, a step of
	 * padding and of radius) would split the size control's four notches two either side of that
	 * breakpoint. The same badge on the same file would be one size at one notch and another at the
	 * next, and moving the slider would grow and shrink the badges along with the
	 * pictures.
	 *
	 * The smallest notch is excluded deliberately and is not an exception being papered over: below
	 * 140px the marks are not shrunk, they are GONE, which is a different
	 * decision. What is checked is that wherever they ARE drawn, they are one size.
	 */
	/*
	 * 1440 wide, and the width is load-bearing rather than arbitrary.
	 *
	 * The justified grid fits a whole number of tiles per row, so a notch does not map to a tile
	 * height. It maps to a target the row then quantises. At 1280 notches 1 and 2 both land on
	 * the same side of 180px, so a size step at 180 would have nothing to cross and nothing to
	 * catch. Across widths:
	 *
	 *     1024   notch 1 -> 139px tile, marks not drawn   notch 2 -> 283px
	 *     1440   notch 1 -> 162px tile                    notch 2 -> 217px
	 *     1920   notch 1 -> 182px tile                    notch 2 -> 229px
	 *     2560   notch 1 -> 180px tile                    notch 2 -> 254px
	 *
	 * 1440 is chosen because it straddles 180 with both notches drawing a mark.
	 */
	await page.setViewportSize({ width: 1440, height: 900 });
	await wallOfMarkedTiles(page);
	const slider = page.locator('.size input[type="range"]');
	await expect(slider).toBeEnabled();

	const heights: number[] = [];
	/* Notches 1 upward. Zero is the one that removes the marks rather than resizing them. */
	for (const notch of [1, 2, 3]) {
		await slider.fill(String(notch));
		await page.waitForTimeout(300);
		/* The mark itself, not the tooltip wrapping it: the wrapper takes its child's height today and
		   would go on agreeing with any height the mark happened to have. */
		const height = await page.evaluate(() => {
			const mark = document.querySelector('.marks .mark');
			return mark ? Math.round(mark.getBoundingClientRect().height) : 0;
		});
		if (height > 0) heights.push(height);
	}

	expect(heights.length, 'no notch drew a mark at all').toBeGreaterThan(1);
	expect(new Set(heights).size, `the marks measured ${heights.join(', ')} across the notches`).toBe(
		1
	);
});

test('the vault mark is solid only where the file itself is the one hidden', async ({ page }) => {
	/*
	 * The fill means one thing everywhere in this app: the switch is on THIS thing rather than on
	 * something above it. The sharing mark draws it that way, and so does the hidden panel behind
	 * the glyph, and so must the tile's own crossed-out eye, from `hidden_here` on the wire.
	 * Drawn solid from `hidden` alone, a wall of files under one hidden person would each wear a
	 * badge asserting somebody had hidden that file.
	 *
	 * The fixture sets `hidden` on more tiles than `hidden_here`, so a rule that ignored the second
	 * one would draw too many solid glyphs and fail here rather than passing on a set where the two
	 * happen to agree.
	 *
	 * A window with room for the whole fixture, because the fixture is what the claim rests on. The
	 * wall draws what fits and no more, so at the runner's default window the second notch on the
	 * ladder leaves six tiles on screen, which cannot answer the question. Twenty-four tiles fit
	 * here, eight of them hidden and four of those hidden in their own right.
	 */
	await page.setViewportSize({ width: 1600, height: 1200 });
	await wallOfMarkedTiles(page);

	const eyes = await page.evaluate(() =>
		[...document.querySelectorAll('.mark.vaulted')].map((mark) => {
			const glyph = mark.querySelector('.icon, [class*="icon"]');
			const weight = glyph ? getComputedStyle(glyph).fontVariationSettings : '';
			return { filled: /FILL.\s*1/.test(weight) };
		})
	);

	expect(eyes.length, 'no tile carries a vault mark').toBeGreaterThan(2);
	const solid = eyes.filter((eye) => eye.filled).length;
	expect(solid, 'every vault mark is solid, so the fill says nothing').toBeLessThan(eyes.length);
	expect(solid, 'no vault mark is solid, so the fill says nothing').toBeGreaterThan(0);
});
