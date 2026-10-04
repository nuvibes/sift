import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * Corners, measured.
 *
 * A rounded box drawn inside another rounded box has to take the outer radius MINUS the gap between
 * them, or the two curves are merely both round and the eye reads the inner one as wrong without
 * being able to say why.
 *
 * Nothing static can check this. The gap between two boxes is a layout fact (it depends on
 * padding, on borders, on what the element's size turned out to be), so the only way to know is to
 * lay the page out and measure. A radius token changed in one place and not the other is invisible
 * in review and obvious on screen.
 *
 * The rule has two halves and this checks both:
 *
 *   1. the inner radius is the outer radius minus the gap, and
 *   2. the gap is the SAME on all four sides.
 *
 * The second is what a `scale: 0.94` on a selected tile would break: a percentage inset takes more
 * off the long axis than the short one, so a landscape thumbnail gets a frame with four different
 * margins that change with the tile-size slider.
 *
 * ## What is deliberately not measured
 *
 * A pill (`--radius-full`) is not part of the rule: its curve is its height, it is not trying to
 * echo anything, and a chip inside a card is not a concentric pair. Anything whose gap is at least
 * its parent's radius is out too: at that distance the curves are independent and any radius is as
 * right as another.
 */

/** How far off a radius may be. One pixel, for sub-pixel layout and browser rounding. */
const SLACK = 1;

interface Offender {
	where: string;
	corner: string;
	parentRadius: number;
	childRadius: number;
	wanted: number;
	inset: number;
}

/**
 * Every nested rounded CORNER on the page that is not concentric with the one it sits inside.
 *
 * Corner by corner, and that is the part that matters. Comparing whole elements
 * flags every button that happens to sit a few pixels below the top of a rounded page container,
 * near one edge and eight hundred pixels from the other, which is not a corner and not a pair. A
 * child echoes an outer corner only when it is tucked INTO that corner: the same short distance
 * from both of its edges, and closer than the outer radius. Then, and only then, the two curves are
 * read together and the inner one has to be the outer minus that distance.
 *
 * Runs in the browser because that is where the layout is. It walks every element that draws a
 * radius and measures against the nearest ancestor that draws one too, so a control three levels
 * down inside a card is still compared with the card, which is what the eye compares it to.
 */
async function offenders(page: Page): Promise<Offender[]> {
	return page.evaluate((slack) => {
		const PILL = 100;
		const CORNERS = [
			{ name: 'top-left', style: 'borderTopLeftRadius' },
			{ name: 'top-right', style: 'borderTopRightRadius' },
			{ name: 'bottom-right', style: 'borderBottomRightRadius' },
			{ name: 'bottom-left', style: 'borderBottomLeftRadius' }
		] as const;

		const radii = (element: Element): number[] => {
			const style = getComputedStyle(element);
			return CORNERS.map((corner) => parseFloat(style[corner.style]) || 0);
		};

		const drawn = (element: Element): boolean => {
			const style = getComputedStyle(element);
			if (style.display === 'none' || style.visibility === 'hidden') return false;
			const box = element.getBoundingClientRect();
			return box.width > 0 && box.height > 0;
		};

		const name = (element: Element): string => {
			const classes = (element.className || '').toString().trim().split(/\s+/).filter(Boolean);
			return element.tagName.toLowerCase() + (classes.length ? '.' + classes.join('.') : '');
		};

		const found: {
			where: string;
			corner: string;
			parentRadius: number;
			childRadius: number;
			wanted: number;
			inset: number;
		}[] = [];

		for (const element of Array.from(document.querySelectorAll('*'))) {
			if (!drawn(element)) continue;
			const mine = radii(element);
			if (mine.every((value) => value <= 0)) continue;

			let parent = element.parentElement;
			while (parent && (!drawn(parent) || radii(parent).every((value) => value <= 0))) {
				parent = parent.parentElement;
			}
			if (!parent) continue;
			const theirs = radii(parent);

			const inner = element.getBoundingClientRect();
			const outer = parent.getBoundingClientRect();
			// Per corner: how far this box's corner is from the same corner of the box it is in.
			const distances = [
				[inner.left - outer.left, inner.top - outer.top],
				[outer.right - inner.right, inner.top - outer.top],
				[outer.right - inner.right, outer.bottom - inner.bottom],
				[inner.left - outer.left, outer.bottom - inner.bottom]
			];

			for (const [index, corner] of CORNERS.entries()) {
				const parentRadius = theirs[index];
				const childRadius = mine[index];
				if (parentRadius <= 0 || parentRadius >= PILL) continue;
				if (childRadius >= PILL) continue;
				const [across, down] = distances[index];
				// Overflowing the parent, or hanging off one edge: not a nested corner.
				if (across < -slack || down < -slack) continue;
				// Square into the corner, or the two curves have nothing to do with each other.
				if (Math.abs(across - down) > slack) continue;
				const inset = Math.max(across, down);
				// Far enough in that the outer curve has already finished.
				if (inset >= parentRadius) continue;

				const wanted = parentRadius - inset;
				// A radius of half a box IS that box's shortest side rounded off: a pill, or a
				// circle if it is square. Past that point the child has stopped echoing anybody's
				// corner and the rule has nothing to say: a 20px chip four pixels inside a 14px
				// corner would have to be a circle to satisfy it, which is not what "concentric"
				// looks like to anybody.
				if (wanted >= Math.min(inner.width, inner.height) / 2) continue;
				if (Math.abs(childRadius - wanted) > slack) {
					found.push({
						where: `${name(parent)} > ${name(element)}`,
						corner: corner.name,
						parentRadius,
						childRadius,
						wanted: Math.round(wanted * 100) / 100,
						inset: Math.round(inset * 100) / 100
					});
				}
			}
		}
		return found;
	}, SLACK);
}

function report(found: Offender[]): string {
	return found
		.map(
			(one) =>
				`${one.where} [${one.corner}]: radius ${one.childRadius} inside ${one.parentRadius} ` +
				`at an inset of ${one.inset}, wanted ${one.wanted}`
		)
		.join('\n');
}

const SCREENS = ['/browse', '/collections', '/people', '/tags', '/sites', '/settings'];

/**
 * Rows on every wall, so there is something to measure.
 *
 * The walls are seeded rather than left to whatever the rest of the suite happened to create. An
 * empty wall has no cards on it, so the check walks over nothing and passes. And an order-
 * dependent gate is worse than none: it goes green on its own and red in a full run, which reads as
 * flakiness and gets muted. Everything a card can draw is turned on here, the sharing marks
 * included, because a badge is exactly the sort of small nested box this rule is about.
 */
async function fillTheWalls(page: Page): Promise<void> {
	const json = (body: unknown) => ({
		status: 200,
		contentType: 'application/json',
		body: JSON.stringify(body)
	});
	const entity = (id: string, name: string, marks: { shared: boolean; restricted: boolean }) => ({
		id,
		name,
		cover_asset_id: null,
		favorite: false,
		rating: null,
		vault: false,
		asset_count: 3,
		item_count: 3,
		account_count: 1,
		alias_count: 0,
		kind: 'site',
		...marks
	});
	const both = { shared: true, restricted: false };
	const kept = { shared: false, restricted: true };

	for (const [path, rows] of [
		['**/api/collections*', [entity('c1', 'A shared collection', both)]],
		['**/api/tags*', [entity('t1', 'a-shared-tag', both), entity('t2', 'a-closed-tag', kept)]],
		['**/api/people*', [entity('p1', 'A Shared Person', both)]],
		['**/api/sites*', [entity('s1', 'A Shared Site', kept)]]
	] as const) {
		await page.route(path, (route) =>
			route.request().method() === 'GET' ? route.fulfill(json(rows)) : route.continue()
		);
	}
}

test('every rounded box inside another one is concentric with it', async ({ page }) => {
	await signInAsAdmin(page);
	await fillTheWalls(page);

	for (const where of SCREENS) {
		await page.goto(where);
		await expect(page.locator('h1').first()).toBeVisible();
		const found = await offenders(page);
		expect(found, `${where}\n${report(found)}`).toEqual([]);
	}
});

/** One transparent pixel, as PNG: enough for a browser to lay a real thumbnail out. */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

test('including a grid with tiles on it, one of them picked', async ({ page }) => {
	/* The screens above are mostly chrome, and an empty one has almost no nested corners to measure,
	 * which is a green that proves nothing. The tile is where the rule is easiest to break, so
	 * the tile is drawn here: a real thumbnail inside a real
	 * tile, and then the same tile selected, which is what pulls the picture in behind the ring. */
	await signInAsAdmin(page);

	const items = [1, 2, 3, 4].map((n) => ({
		id: `c${n}`,
		media_type: 'video',
		width: n % 2 === 0 ? 1920 : 1080,
		height: n % 2 === 0 ? 1080 : 1920,
		duration_ms: 61_000,
		favorite: false,
		rating: null,
		concealed: false,
		thumb: true,
		shared: n === 1,
		restricted: n === 2,
		shared_here: n === 1,
		restricted_here: false,
		original_filename: `clip-${n}.mp4`
	}));

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
	const tiles = page.locator('.tile');
	await expect(tiles.first()).toBeVisible();

	let found = await offenders(page);
	expect(found, `browse with tiles\n${report(found)}`).toEqual([]);

	// And picked, which insets the picture inside the tile's own corner.
	await tiles.first().click({ modifiers: ['ControlOrMeta'] });
	await expect(page.getByRole('region', { name: 'Selection' })).toBeVisible();
	found = await offenders(page);
	expect(found, `browse with a tile picked\n${report(found)}`).toEqual([]);
});

test('and with the search box in use, which swaps what sits in its corner', async ({ page }) => {
	/* The two controls at the end of the search field are never both drawn: the shortcut hint is
	 * there until the box is used and the clear cross only once there is something to clear. So a
	 * sweep that never types has seen one of them and not the other, and they sit in the same
	 * place, at the same inset, inside the same corner. */
	await signInAsAdmin(page);
	await page.goto('/browse');
	await expect(page.locator('h1').first()).toBeVisible();

	// A COMBOBOX and not a searchbox: it owns a listbox of suggestions, and ARIA gives that role to
	// the input that owns one. `searchbox` matches nothing here whatever the page contains.
	const box = page.getByRole('combobox', { name: 'Search' }).first();
	await box.fill('something');
	await expect(page.getByRole('button', { name: 'Clear the search' })).toBeVisible();

	const found = await offenders(page);
	expect(found, `browse with the search box in use\n${report(found)}`).toEqual([]);
});

test('and the check can tell when one is not', async ({ page }) => {
	/* The gate above passes on an empty page, on a page whose boxes are all square, and on one it
	 * failed to measure. So on its own it is three ways of proving nothing. This puts a box with
	 * the wrong corner on a real screen and insists the check finds it. */
	await signInAsAdmin(page);
	await page.goto('/browse');
	await expect(page.locator('h1').first()).toBeVisible();

	await page.evaluate(() => {
		const outer = document.createElement('div');
		outer.className = 'corner-probe-outer';
		outer.style.cssText =
			'position:fixed;left:10px;top:10px;width:200px;height:120px;' +
			'border-radius:20px;padding:8px;background:#000;z-index:9999';
		const inner = document.createElement('div');
		inner.className = 'corner-probe-inner';
		// Concentric would be 20 - 8 = 12. This is not that.
		inner.style.cssText = 'width:100%;height:100%;border-radius:20px;background:#fff';
		outer.appendChild(inner);
		document.body.appendChild(outer);
	});

	const found = await offenders(page);
	expect(found.map((one) => one.where)).toContain(
		'div.corner-probe-outer > div.corner-probe-inner'
	);
});
