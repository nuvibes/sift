import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * Every control on a settings pane stops at the same edge, measured from the drawn controls
 * themselves: `.control` is a fixed column and would always agree, and `gate:settings-row` reads
 * the source rather than the boxes.
 */

/** The panes, by their address id (`settings-ui/sections.ts`). */
const SECTIONS = [
	'library',
	'downloads',
	'sites',
	'editing',
	'playback',
	'theater',
	'faces',
	'semantic',
	'stash-boxes',
	'music',
	'profile',
	'get-to-know',
	'appearance',
	'privacy',
	'shortcuts',
	'users',
	'performance',
	'jobs',
	'maintenance',
	'backup',
	'general',
	'updates'
];

/** What counts as a control rather than a wrapper or a piece of prose. */
const CONTROLS = [
	'button',
	'input',
	'select',
	'textarea',
	'[role="combobox"]',
	'[role="switch"]',
	'[role="slider"]',
	'[role="spinbutton"]'
].join(',');

/** The known positive: with no rows every group of edges agrees, so a broken selector passes. */
const AT_LEAST = 80;

async function edgesOn(page: Page, section: string) {
	await page.goto(`/settings/${section}`);
	await expect(page.locator('.settings-pane, .row').first()).toBeVisible();
	// The pane fetches its own settings; nothing passes every claim below.
	await page.waitForTimeout(400);

	return page.evaluate((controls) => {
		/* What is DRAWN, clipped by whatever clips it: an overlong menu value is not on screen. */
		const drawnRight = (element: Element, within: Element) => {
			let edge = element.getBoundingClientRect().right;
			for (let above = element.parentElement; above; above = above.parentElement) {
				if (getComputedStyle(above).overflowX !== 'visible') {
					edge = Math.min(edge, above.getBoundingClientRect().right);
				}
				if (above === within) break;
			}
			return edge;
		};

		const rows: { edge: number; what: string }[] = [];
		for (const row of document.querySelectorAll('.row')) {
			const control = row.querySelector('.control');
			if (!control) continue;
			// Only rows holding a control: a fact ends where its words do.
			if (!control.querySelector(controls)) continue;
			// The widest thing in the column: a slider's track stops where its readout begins.
			let right: number | null = null;
			let widest = '';
			for (const element of control.querySelectorAll('*')) {
				const box = element.getBoundingClientRect();
				if (box.width === 0 || box.height === 0) continue;
				const edge = drawnRight(element, control);
				if (right === null || edge > right) {
					right = edge;
					widest =
						element.tagName.toLowerCase() + (element.className ? `.${element.className}` : '');
				}
			}
			if (right !== null) {
				rows.push({ edge: Math.round(right), what: `${row.id || '(no id)'} ${widest}` });
			}
		}
		return rows;
	}, CONTROLS);
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	// Wider than the pane's maximum, so the layout decides the edge.
	await page.setViewportSize({ width: 1600, height: 1000 });
});

for (const section of SECTIONS) {
	test(`every control on ${section} stops at the same edge`, async ({ page }) => {
		const rows = await edgesOn(page, section);
		if (rows.length === 0) return; // a pane with no rows of its own, which several are

		const edges = [...new Map(rows.map((one) => [one.edge, one.what]))];
		expect(
			edges.length,
			`${section} draws its controls at ${edges.length} different edges:\n` +
				edges.map(([edge, what]) => `  ${edge}  ${what}`).join('\n')
		).toBe(1);
	});
}

test('and there are enough rows on those panes for that to have meant anything', async ({
	page
}) => {
	let counted = 0;
	for (const section of SECTIONS) {
		counted += (await edgesOn(page, section)).length;
	}
	expect(counted, 'the rows this measures have stopped being found at all').toBeGreaterThanOrEqual(
		AT_LEAST
	);
});

/* The section list's edge: the search field starts where every row does, and a row's highlight
 * stops short of the column's floating scrollbar. */
test('the search box and the list under it start and stop on the same two lines', async ({
	page
}) => {
	await page.goto('/settings/appearance');
	await expect(page.locator('.search-slot input')).toBeVisible();
	/* Waited for: the scroller mounts its bar only once it has measured an overflow. */
	await expect(page.locator('.sections-slot .scroll-bar')).toBeAttached();

	const measured = await page.evaluate(() => {
		const field = document.querySelector('.search-slot input')!.getBoundingClientRect();
		const rows = [...document.querySelectorAll('.settings nav .item')].map((one) =>
			one.getBoundingClientRect()
		);
		/* Found by where it lives, not by where it is. */
		const bar = [...document.querySelectorAll('.sections-slot .scroll-bar')].map(
			(one) => one.getBoundingClientRect().left
		);
		return {
			field: { left: Math.round(field.left), right: Math.round(field.right) },
			rows: rows.map((one) => ({ left: Math.round(one.left), right: Math.round(one.right) })),
			bar: bar.length > 0 ? Math.round(Math.min(...bar)) : null
		};
	});

	expect(measured.rows.length, 'the section list was not found at all').toBeGreaterThan(10);

	const lefts = new Set([measured.field.left, ...measured.rows.map((one) => one.left)]);
	expect(
		[...lefts],
		'the search box and the rows under it start on different vertical lines'
	).toHaveLength(1);

	const rights = new Set([measured.field.right, ...measured.rows.map((one) => one.right)]);
	expect([...rights], 'the search box is a different width from the rows').toHaveLength(1);

	/* Twenty sections at a thousand pixels tall must have a bar. */
	expect(measured.bar, 'the section list drew no scrollbar to be clear of').not.toBeNull();
	expect(
		measured.rows[0].right,
		`a row runs to ${measured.rows[0].right} and the bar starts at ${measured.bar}`
	).toBeLessThanOrEqual(measured.bar!);
});
