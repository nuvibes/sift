import { expect, test } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * Every settings section is titled, with its own icon, and its groups are headed one way.
 *
 * ## Why this is measured here
 *
 * The fault it guards is only visible as a MEASUREMENT of drawn text: headings in several sizes,
 * sections without a title, a section calling itself something the list does not. The source gate
 * (`scripts/check_settings_headings.js`) refuses a heading written by hand; this is the other half,
 * which says what the one mechanism actually draws: a title naming the section the list has lit,
 * the list's icon beside it in the list's accent ink, every group heading at 17px, a hairline over
 * every group after the first and none over the first. A group is a heading or a row, so a heading
 * under an unheaded first group of rows wears the hairline too.
 *
 * The icon's colour is compared with the LIT ROW's icon on the same screen rather than with a
 * number, so the claim survives a change of accent. It is "the same mark as the list", which is
 * what the title is for.
 */

/** Every section, by the id its address uses. `settings-ui/sections.ts` is the list. */
const SECTIONS = [
	'library',
	'importing',
	'downloads',
	'sites',
	'editing',
	'playback',
	'theater',
	'faces',
	'semantic',
	'watermarks',
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
	'logs',
	'ledger',
	'schedule',
	'maintenance',
	'backup',
	'general',
	'updates'
];

/** The contract's section header, `--text-h2`. */
const GROUP_PX = 17;

/** A subheading (`SectionHeading level={3}`), `--text-h3`: one step down, the size of a row. */
const SUB_PX = 13.5;

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1600, height: 1000 });
});

for (const section of SECTIONS) {
	test(`${section} is titled with its own icon, and its groups are headed one way`, async ({
		page
	}) => {
		await page.goto(`/settings/${section}`);
		await expect(page.locator('.section-body h1').first()).toBeVisible();
		// The pane fetches its own settings; groups drawn after that arrive are the ones that matter.
		await page.waitForTimeout(400);

		const seen = await page.evaluate(() => {
			const body = document.querySelector('.section-body')!;
			const titles = [...body.querySelectorAll('h1')];
			const lit = document.querySelector('.settings nav .item.active');
			const iconColour = (root: Element | null) =>
				root?.querySelector('.icon') ? getComputedStyle(root.querySelector('.icon')!).color : null;
			/* A band (`SectionHeading band`, the small heading over a block inside a group, such as a
			   day of the log) is not a group boundary, so it is read on its own below. */
			const shown = (one: Element) => (one as HTMLElement).offsetParent !== null;
			const heads = [...body.querySelectorAll('.section-heading:not(.band)')].filter(shown);
			const bands = [...body.querySelectorAll('.section-heading.band')].filter(shown);
			const drawn = (one: Element) => {
				const rule = one.querySelector('.section-rule');
				return rule !== null && getComputedStyle(rule).display !== 'none'
					? rule.getBoundingClientRect().height
					: 0;
			};
			/* Every heading element that is not a title: none may be smaller than the rows (13.5px),
			   and every group heading is exactly the contract's size. */
			const others = [...body.querySelectorAll('h2, h3, h4, h5, h6')].filter(
				(one) => shown(one) && one.closest('.section-heading.band') === null
			);
			return {
				titles: titles.map((one) => one.textContent?.trim() ?? ''),
				litLabel: lit?.textContent?.trim() ?? '',
				titleIcon: iconColour(titles[0] ?? null),
				litIcon: iconColour(lit),
				titleInk: titles[0] ? getComputedStyle(titles[0]).color : null,
				groupSizes: heads
					.filter((one) => one.querySelector('h2') !== null)
					.map((one) => parseFloat(getComputedStyle(one.querySelector('h2')!).fontSize)),
				subSizes: heads
					.filter((one) => one.querySelector('h3') !== null)
					.map((one) => parseFloat(getComputedStyle(one.querySelector('h3')!).fontSize)),
				bandSizes: bands.map((one) =>
					parseFloat(getComputedStyle(one.querySelector('h2, h3')!).fontSize)
				),
				smallest: Math.min(
					Infinity,
					...others.map((one) => parseFloat(getComputedStyle(one).fontSize))
				),
				rules: heads.map(drawn),
				/* Whether a group of rows stands before the first heading on the same page. The
				   contract makes a group a heading OR a row, so a heading after an unheaded first
				   group of rows opens the second group, hairline and all. */
				rowsBefore: (() => {
					const first = heads[0];
					const stack = first?.closest('.section-stack');
					if (!first || !stack) return false;
					return [...stack.querySelectorAll('.ruled-row')].some(
						(row) =>
							shown(row) &&
							(row.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
					);
				})()
			};
		});

		// One title, naming the section the list has lit.
		expect(seen.titles, `${section}: the titles drawn`).toHaveLength(1);
		expect(seen.titles[0]).toContain(seen.litLabel);
		// The lit row's icon, in the lit row's ink, and the words NOT in it.
		expect(seen.titleIcon, `${section}: the title has no icon`).not.toBeNull();
		expect(seen.titleIcon).toBe(seen.litIcon);
		expect(seen.titleInk).not.toBe(seen.titleIcon);
		// One group size, the contract's, and nothing under the title smaller than a row.
		for (const size of seen.groupSizes) expect(size, `${section}: a group heading`).toBe(GROUP_PX);
		// A subheading over a block inside a group is one step down the scale, never a third size.
		for (const size of seen.subSizes) expect(size, `${section}: a subheading`).toBe(SUB_PX);
		if (seen.smallest !== Infinity) expect(seen.smallest).toBeGreaterThanOrEqual(13.5);
		// And every band in one face: the small heading has one size wherever it is drawn.
		expect(new Set(seen.bandSizes).size, `${section}: bands in two sizes`).toBeLessThanOrEqual(1);
		// No hairline over the first group; one over every group after it. The first heading heads
		// the second group when a group of rows stands above it without one.
		if (seen.rules.length > 0) {
			if (seen.rowsBefore) {
				expect(seen.rules[0], `${section}: no rule after the first group of rows`).toBeGreaterThan(
					0
				);
			} else {
				expect(seen.rules[0], `${section}: a rule over the first group`).toBe(0);
			}
			for (const [at, height] of seen.rules.slice(1).entries()) {
				expect(height, `${section}: no rule over group ${at + 2}`).toBeGreaterThan(0);
			}
		}
	});
}

/*
 * The known positive. Every per-section claim above is about groups, and a pane that stopped
 * drawing any would pass them all by having nothing to check.
 */
test('and enough groups are drawn across Settings for that to have meant anything', async ({
	page
}) => {
	let groups = 0;
	for (const section of SECTIONS) {
		await page.goto(`/settings/${section}`);
		await expect(page.locator('.section-body h1').first()).toBeVisible();
		await page.waitForTimeout(400);
		groups += await page.locator('.section-body .section-heading:not(.band):visible').count();
	}
	expect(groups).toBeGreaterThanOrEqual(40);
});
