import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * Corners, measured: a rounded box inside another takes the outer radius MINUS the gap, and the
 * gap is the same on all four sides. The gap is a layout fact, so the page is laid out and
 * measured. Pills, and anything inset by its parent's radius or more, are out of the rule.
 */

/** One pixel, for sub-pixel layout and browser rounding. */
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
 * Every nested rounded CORNER that is not concentric with the one it sits in, corner by corner:
 * only a child tucked into a corner (equally close to both edges, inside the outer radius) echoes
 * it. Measured against the nearest rounded ancestor, however deep.
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
			// Per corner: the distance from the same corner of the box it is in.
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
				// Overflowing the parent, or off one edge: not nested.
				if (across < -slack || down < -slack) continue;
				// Square into the corner, or the curves are unrelated.
				if (Math.abs(across - down) > slack) continue;
				const inset = Math.max(across, down);
				// The outer curve has already finished.
				if (inset >= parentRadius) continue;

				const wanted = parentRadius - inset;
				// A radius of half the box is a pill or a circle, not an echo of a corner.
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

/** Rows on every wall, seeded: an empty wall passes, and an order-dependent gate gets muted. Every
 * mark is on, since a badge is a small nested box. */
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

/** One transparent pixel: enough for a browser to lay a real thumbnail out. */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

test('including a grid with tiles on it, one of them picked', async ({ page }) => {
	/* The tile is where the rule breaks most easily: a real thumbnail, then the tile picked, which
	 * pulls the picture in behind the ring. */
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
	/* The shortcut hint and the clear cross are never both drawn, and share one corner. */
	await signInAsAdmin(page);
	await page.goto('/browse');
	await expect(page.locator('h1').first()).toBeVisible();

	// A combobox: it owns a listbox, so `searchbox` matches nothing.
	const box = page.getByRole('combobox', { name: 'Search' }).first();
	await box.fill('something');
	await expect(page.getByRole('button', { name: 'Clear the search' })).toBeVisible();

	const found = await offenders(page);
	expect(found, `browse with the search box in use\n${report(found)}`).toEqual([]);
});

test('and the check can tell when one is not', async ({ page }) => {
	/* The gate passes on an empty page, so a wrong corner is planted and must be found. */
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
		// Concentric would be 20 - 8 = 12.
		inner.style.cssText = 'width:100%;height:100%;border-radius:20px;background:#fff';
		outer.appendChild(inner);
		document.body.appendChild(outer);
	});

	const found = await offenders(page);
	expect(found.map((one) => one.where)).toContain(
		'div.corner-probe-outer > div.corner-probe-inner'
	);
});
