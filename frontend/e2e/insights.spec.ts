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
			id: 'alongside',
			title: 'Alongside',
			floor_reached: false,
			statements: [said('Not enough yet to say.')],
			figures: [],
			chart: null,
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
	period: 'month:2026-09',
	title: 'Your September',
	span: 'September 2026',
	heading: 'September, in 4 cards',
	made_at: 1791500000000,
	first_day: '2026-09-28',
	hidden_line: null,
	cards: [
		{
			id: 'c1',
			kind: 'headline',
			headline: [],
			context: [],
			wall: null,
			accent_hue: null,
			figure: figure('Viewed', 3 * HOUR + 21 * 60000, 'ms', '3 h 21 min'),
			statement: said(
				'You viewed about 3 hours last week: 1 hour 19 minutes of videos, 29 minutes of pictures, 2 minutes of GIFs, 1 hour 30 minutes in Theater.'
			),
			cover: null,
			rows: [],
			chart: null,
			hidden_things: [],
			hidden: false,
			figures: [],
			calendar: null
		},
		{
			id: 'c2',
			kind: 'top_five',
			headline: [],
			context: [],
			wall: null,
			accent_hue: null,
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
			hidden: false,
			figures: [],
			calendar: null
		},
		{
			id: 'c3',
			kind: 'when',
			headline: [],
			context: [],
			wall: null,
			accent_hue: null,
			figure: figure('Busiest hour', 2 * HOUR, 'ms', '2 h'),
			statement: said('10 PM was your favourite time.'),
			cover: null,
			rows: [],
			chart: bars(
				Array.from({ length: 24 }, (_, hour) => `${hour}`),
				Array.from({ length: 24 }, (_, hour) => (hour === 22 ? 2 * HOUR : 0))
			),
			hidden_things: [],
			hidden: false,
			figures: [],
			calendar: null
		},
		{
			id: 'c4',
			kind: 'closing',
			headline: [],
			context: [],
			wall: null,
			accent_hue: null,
			figure: null,
			statement: said('That was September.'),
			cover: null,
			rows: [],
			chart: null,
			hidden_things: [],
			hidden: false,
			figures: [],
			calendar: null
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

test('Stats is panels under an index, keeps its period, and Back returns to Insights', async ({
	page: browser
}) => {
	await browser.setViewportSize({ width: 1400, height: 900 });
	await browser.goto('/insights?period=week');
	await browser.getByRole('button', { name: 'Stats' }).click();
	await expect(browser).toHaveURL(/\/insights\/stats\?period=week/);
	await expect(browser.locator('nav.section-index a').first()).toBeVisible();
	await expect(browser.locator('article').first()).toBeVisible();
	await browser.getByRole('button', { name: 'Copy' }).first().click();
	await expect(browser.getByText('Copied')).toBeVisible();
	const sideways = await browser.evaluate(
		() => document.documentElement.scrollWidth <= window.innerWidth
	);
	expect(sideways, 'Stats scrolls sideways at 1400').toBe(true);
	await browser.getByRole('button', { name: 'Earlier' }).click();
	await expect(browser).toHaveURL(/\/insights\/stats/);
	await browser.locator('a.back').click();
	await expect(browser).toHaveURL(/\/insights\?period=week/);
});

test('the board fills the width and a tile opens its own table on Stats', async ({
	page: browser
}) => {
	await browser.setViewportSize({ width: 1920, height: 1080 });
	await browser.goto('/insights?period=week');
	const board = browser.locator('.board');
	await expect(board).toBeVisible();
	const [frame, grid] = await Promise.all([
		browser.locator('.frame-body').first().boundingBox(),
		board.boundingBox()
	]);
	if (!frame || !grid) throw new Error('the board or its frame has no box');
	expect(grid.width, 'the board leaves the width unused').toBeGreaterThan(frame.width * 0.9);
	await board.locator('li.tile[data-slot="viewed"]').click();
	await expect(browser).toHaveURL(/\/insights\/stats\?period=week.*#overview/);
	await expect(
		browser.locator('article#overview.lit, article[id^="overview"].lit').first()
	).toBeVisible();
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

test('a figure says what it counts on a hover, and Alongside under its floor says so', async ({
	page: browser
}) => {
	await browser.goto('/insights?period=week');
	await browser.getByRole('button', { name: 'What counts' }).nth(1).hover();
	await expect(browser.getByRole('tooltip')).toContainText('What sessions counts.');
	await expect(browser.locator('[data-block="alongside"]')).toContainText('Not enough yet to say.');
	await browser.goto('/insights?period=day');
	await expect(browser.locator('[data-block="alongside"]')).toHaveCount(0);
});

test("See this week's recap says when no recap exists yet", async ({ page: browser }) => {
	await browser.goto('/insights?period=week');
	await browser.getByRole('button', { name: "See this week's recap" }).click();
	await expect(browser.getByText(/No recap of this week yet/)).toBeVisible();
});

test('a set of cards is saved one file each, the unticked card left out', async ({
	page: browser
}) => {
	await browser.goto('/insights/recaps/r-one');
	await expect(browser.getByRole('button', { name: 'Save all as pictures' })).toBeVisible();
	// The tick is the card in view's: turn to the last card and leave it out of the set.
	for (let turn = 0; turn < 3; turn += 1)
		await browser.getByRole('button', { name: 'Later' }).click();
	await browser.getByRole('checkbox', { name: 'This card in the set' }).click();
	const downloads: string[] = [];
	browser.on('download', (one) => downloads.push(one.suggestedFilename()));
	await browser.getByRole('button', { name: 'Save 3 as pictures' }).click();
	await expect.poll(() => downloads.length, { timeout: 20_000 }).toBe(3);
	expect(downloads.every((name) => name.endsWith('.png'))).toBe(true);
});

test('a deck is saved as a video through the server, frames in', async ({ page: browser }) => {
	let frames = 0;
	await browser.route('**/api/insights/recaps/r-one/video', async (route) => {
		const body = route.request().postDataBuffer();
		// One film of frames, each a picture: a two-card deck is far over ten kilobytes.
		frames = body ? Math.floor(body.length / 10_000) : 0;
		await route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.from('mp4') });
	});
	await browser.goto('/insights/recaps/r-one');
	const saved = browser.waitForEvent('download', { timeout: 60_000 });
	await browser.getByRole('button', { name: 'Save as video' }).click();
	const file = await saved;
	expect(file.suggestedFilename()).toMatch(/\.mp4$/);
	expect(frames).toBeGreaterThan(10);
});
