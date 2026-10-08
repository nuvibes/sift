import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * Insights in a real browser: the Stats view, a chart read from the keyboard, and the deck's cards
 * at one size. The page's answer is fabricated: what is under test is the screen, not the counting,
 * which the server's own tests hold to the ledger.
 */

const HOUR = 3_600_000;
const piece = (text: string, kind: string | null = null, id: string | null = null) => ({
	text,
	kind,
	id,
	href: null,
	gone: false,
	rest: [],
	lead: ''
});
const said = (text: string) => [piece(text)];
const figure = (label: string, value: number, unit: string, words: string) => ({
	label,
	value,
	unit,
	hidden_part: 0,
	said: words,
	hidden_said: '',
	trend: [],
	caption: [],
	defines: said(`What ${label.toLowerCase()} counts.`)
});
const bars = (labels: string[], values: number[]) => ({
	kind: 'bars',
	unit: 'ms',
	bars: labels.map((label, at) => ({
		label,
		parts: [{ kind: 'video', value: values[at], said: `${values[at] / HOUR} h` }],
		said: `${values[at] / HOUR} h`
	})),
	today: null,
	caption: []
});
const page = {
	period: 'week',
	from: '2026-09-28',
	to: '2026-10-04',
	today_is_live: false,
	first_sentences: [said('You viewed about 3 hours last week.')],
	blocks: [
		{
			id: 'overview',
			title: 'Overview',
			floor_reached: true,
			statements: [said('You viewed about 3 hours last week.')],
			figures: [figure('Viewed', 3 * HOUR, 'ms', '3 h'), figure('Sessions', 12, 'count', '12')],
			chart: bars(
				['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
				[HOUR, 2 * HOUR, 0, 0, 0, 0, 0]
			),
			lists: [],
			calendar: null,
			notes: []
		},
		{
			id: 'when',
			title: 'When',
			floor_reached: true,
			statements: [said('10 PM was your favourite time.')],
			figures: [figure('Busiest hour', 2 * HOUR, 'ms', '2 h')],
			chart: bars(
				Array.from({ length: 24 }, (_, hour) => `${hour}`),
				Array.from({ length: 24 }, (_, hour) => (hour === 22 ? 2 * HOUR : hour === 23 ? HOUR : 0))
			),
			lists: [],
			calendar: null,
			notes: []
		}
	]
};

const names = [
	'Wren Talbot',
	'Orla Finch',
	'Dax Marlowe',
	'Ione Vesper',
	'Teo Larkin',
	'Mira Holt'
];
const recap = {
	id: 'r-one',
	period: 'week:2026-W40',
	title: 'Your week',
	span: 'September 28 to October 4, 2026',
	heading: 'Last week, in 4 cards',
	made_at: 1791500000000,
	first_day: '2026-09-28',
	hidden_line: null,
	cards: [
		{
			id: 'c1',
			kind: 'headline',
			figure: figure('Viewed', 3 * HOUR + 21 * 60000, 'ms', '3 h 21 min'),
			statement: said(
				'You viewed about 3 hours last week: 1 hour 19 minutes of videos, 29 minutes of pictures, 2 minutes of GIFs, 1 hour 30 minutes in Theater.'
			),
			cover: null,
			rows: [],
			chart: null,
			hidden_things: [],
			hidden: false
		},
		{
			id: 'c2',
			kind: 'top_five',
			figure: null,
			statement: said('These five were the people you viewed most.'),
			cover: null,
			rows: names.map((name, at) => ({
				piece: piece(name, 'person', `p${at}`),
				value: (6 - at) * HOUR,
				unit: 'ms',
				cover: null,
				said: `${6 - at} h`
			})),
			chart: null,
			hidden_things: [],
			hidden: false
		},
		{
			id: 'c3',
			kind: 'when',
			figure: figure('Busiest hour', 2 * HOUR, 'ms', '2 h'),
			statement: said('10 PM was your favourite time.'),
			cover: null,
			rows: [],
			chart: bars(
				Array.from({ length: 24 }, (_, hour) => `${hour}`),
				Array.from({ length: 24 }, (_, hour) => (hour === 22 ? 2 * HOUR : 0))
			),
			hidden_things: [],
			hidden: false
		},
		{
			id: 'c4',
			kind: 'closing',
			figure: null,
			statement: said('That was last week.'),
			cover: null,
			rows: [],
			chart: null,
			hidden_things: [],
			hidden: false
		}
	]
};

test.beforeEach(async ({ page: browser }) => {
	await signInAsAdmin(browser);
	await browser.route('**/api/insights?*', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(page) })
	);
	await browser.route('**/api/insights/recaps', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ recaps: [], announced: null })
		})
	);
	await browser.route('**/api/insights/recaps/r-one', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(recap) })
	);
});

test('Stats shows every figure as tables and keeps its period', async ({ page: browser }) => {
	await browser.goto('/insights?period=week');
	await browser.getByRole('button', { name: 'Stats' }).click();
	await expect(browser).toHaveURL(/\/insights\/stats\?period=week/);
	await expect(browser.getByText('What counts').first()).toBeVisible();
	await browser.getByRole('button', { name: 'Copy' }).first().click();
	await expect(browser.getByText('Copied')).toBeVisible();
	await browser.getByRole('button', { name: 'Earlier' }).click();
	await expect(browser).toHaveURL(/\/insights\/stats/);
});

test('a chart is read from the keyboard, bar by bar, and Escape lets go', async ({
	page: browser
}) => {
	await browser.goto('/insights?period=week');
	const plot = browser.getByRole('group', { name: /^Overview/ });
	await plot.focus();
	await browser.keyboard.press('ArrowRight');
	await expect(browser.getByRole('tooltip')).toContainText('Tue');
	await browser.keyboard.press('Escape');
	await expect(browser.getByRole('tooltip')).toHaveCount(0);
});

test("the hour ring's End lands on the last hour", async ({ page: browser }) => {
	await browser.goto('/insights?period=week');
	const ring = browser.getByRole('group', { name: /^When/ });
	await ring.focus();
	await browser.keyboard.press('End');
	await expect(browser.getByRole('tooltip')).toContainText('11');
});

test("a recap's cards are one size and Later never moves", async ({ page: browser }) => {
	await browser.goto('/insights/recaps/r-one');
	await expect(browser.getByRole('button', { name: 'Later' })).toBeVisible();
	const boxes: { w: number; h: number }[] = [];
	const presses: { x: number; y: number }[] = [];
	for (let at = 0; at < 4; at += 1) {
		await browser.waitForTimeout(700);
		const shown = browser.locator('.cards li:not([hidden]) article').first();
		const box = await shown.boundingBox();
		const later = await browser.getByRole('button', { name: 'Later' }).boundingBox();
		if (!box || !later) throw new Error('card or press not drawn');
		boxes.push({ w: Math.round(box.width), h: Math.round(box.height) });
		presses.push({ x: Math.round(later.x), y: Math.round(later.y) });
		if (at < 3) await browser.getByRole('button', { name: 'Later' }).click();
	}
	for (const box of boxes) expect(box).toEqual(boxes[0]);
	for (const press of presses) expect(press).toEqual(presses[0]);
	expect(boxes[0].h).toBeGreaterThan(boxes[0].w);
});
