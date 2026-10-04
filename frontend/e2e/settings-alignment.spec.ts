import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * Every control on a settings pane stops at the same edge.
 *
 * ## Why this is here and not in a unit test
 *
 * Because it is a measurement of drawn boxes. `gate:settings-row` counts stacked form fields in the
 * SOURCE, which is a cause rather than the symptom, and `page-alignment.spec.ts` measures page
 * TITLES. Nothing else stops a `Field` dropped into a pane by hand (or a control given a width of
 * its own) putting an edge back. It also catches a button placed in a row's figure column, and a
 * menu's chosen value wider than the box it is drawn in, which shows as an ellipsis and says
 * nothing.
 *
 * ## What it measures, and the trap it avoids
 *
 * The CONTROL, never the box around it. `.control` is the fixed-width column every row has in
 * common and could only ever answer zero; taken from the controls themselves, a pane can have many
 * edges, one of them past the pane's own right-hand side.
 */

/** The panes, by the id their own address uses. `settings-ui/sections.ts` is where these come from. */
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

/**
 * How few rows the whole of Settings may draw before this is measuring nothing.
 *
 * The known positive, and it is not optional. Every claim below is about a GROUP of edges, and an
 * empty one has a single distinct value in it. So a selector that stops matching, a class that is
 * renamed, or a pane that stops rendering turns twenty passing tests into twenty tests that check
 * nothing, silently and for ever.
 */
const AT_LEAST = 80;

async function edgesOn(page: Page, section: string) {
	await page.goto(`/settings/${section}`);
	await expect(page.locator('.settings-pane, .row').first()).toBeVisible();
	// The pane fetches its own settings, so give the rows a moment to arrive: reading too early
	// finds nothing, and nothing passes every claim below.
	await page.waitForTimeout(400);

	return page.evaluate((controls) => {
		/* What is DRAWN, which is not the same as the element's box: clipped wherever something
		   clips it. A menu whose chosen value is too long for its trigger has an inner span
		   wider than the button around it, and not one of those pixels is on the screen. A
		   control that genuinely overflows (with nothing clipping it) still reports where it
		   really ends, which is the fault this catches. */
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
			// A row is part of the claim only if it HOLDS a control. About and Users draw facts
			// through the same row, and a fact ends where its words do.
			if (!control.querySelector(controls)) continue;
			// The edge is the widest-right thing drawn inside the control column, control or not: a
			// slider's track stops where its readout begins and the pair is flush, so measuring only the
			// track reports the row as 45px short of a column it is in.
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
	// Wide, and wider than the pane's own maximum, so the column edge is decided by the layout rather
	// than by the window running out.
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

/*
 * The COLUMN on the left, which is the other edge on this screen.
 *
 * The list beside the pane is the edge the eye actually follows: a vertical run of labels down
 * the side of the panel. So the search field's box and text must start where every row's do. And
 * a row's hover highlight must stop short of the column's floating scrollbar, or its last pixels
 * are drawn underneath it.
 */
test('the search box and the list under it start and stop on the same two lines', async ({
	page
}) => {
	await page.goto('/settings/appearance');
	await expect(page.locator('.search-slot input')).toBeVisible();
	/* WAIT for the bar, do not assume it. The shared scroller only MOUNTS one once its resize
	   observer has measured an overflow, so reading straight after the box appears is a race.
	   The known positive below is unchanged: if it never arrives, the wait times out and says
	   so. */
	await expect(page.locator('.sections-slot .scroll-bar')).toBeAttached();

	const measured = await page.evaluate(() => {
		const field = document.querySelector('.search-slot input')!.getBoundingClientRect();
		const rows = [...document.querySelectorAll('.settings nav .item')].map((one) =>
			one.getBoundingClientRect()
		);
		/* The list's OWN bar, found by where it lives rather than by where it is: looking for a
		   bar between the field's two edges would stop matching the moment the rows are inset
		   clear of it. */
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

	/* And clear of the bar. Null where the column is short enough not to have one, which is not a
	   failure. But it IS one on a list of twenty sections at a thousand pixels tall, so the
	   measurement says which case it got rather than passing on an absence. */
	expect(measured.bar, 'the section list drew no scrollbar to be clear of').not.toBeNull();
	expect(
		measured.rows[0].right,
		`a row runs to ${measured.rows[0].right} and the bar starts at ${measured.bar}`
	).toBeLessThanOrEqual(measured.bar!);
});
