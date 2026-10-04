/* The shell's chrome: the inset card, and the rail you can collapse.
 *
 * All geometry, so none of it is answerable without a layout engine: the unit environment renders
 * this markup happily whatever the stylesheet does with it.
 */
import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

const toggle = (page: Page) => page.getByRole('button', { name: /the sidebar/ });

/* The rail's width, once it has stopped moving.
 *
 * It animates between the two widths, so a single measurement taken straight after the click
 * lands somewhere in the middle. Polling waits for the value rather than for a guessed number of
 * milliseconds.
 */
/*
 * The rail's width, declared on `nav.rail`, the element that exists, so `--rail-width` reaches it
 * and the rail is not sized by its own contents.
 */
const railWidth = (page: Page) =>
	expect.poll(async () => (await page.locator('nav.rail').boundingBox())!.width);

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('the content sits on the sidebar as an inset card', async ({ page }) => {
	/* The gap is what separates the navigation from what is being navigated, in place of a border
	 * on the rail. If the card ever goes flush, that separation is gone and the two read as one
	 * surface again.
	 */
	await page.goto('/browse');

	const card = page.locator('.content');
	const box = (await card.boundingBox())!;

	expect(box.x + box.width, 'the card is flush with the right edge').toBeLessThan(1400);
	expect(box.y, 'the card is flush with the top').toBeGreaterThan(0);

	const radius = await card.evaluate((el) => getComputedStyle(el).borderTopLeftRadius);
	expect(parseFloat(radius), 'the card has square corners').toBeGreaterThan(8);

	/* Rounded at the top, square at the bottom, and flush with the bottom of the window.
	 *
	 * The rounding says "this is a panel sitting on the rail", and that only needs saying where the
	 * panel can be seen to end. Against the bottom of the screen a curve rounds the corner off the
	 * window itself and leaves a strip of rail colour under it holding nothing. */
	const bottom = await card.evaluate((el) => getComputedStyle(el).borderBottomLeftRadius);
	expect(parseFloat(bottom), 'the card is rounded at the bottom').toBe(0);
	expect(box.y + box.height, 'the card stops short of the bottom of the window').toBe(900);

	// And it does not scroll the whole page: the card is exactly as tall as the window less its
	// margins, so the top bar cannot be scrolled off.
	expect(box.height).toBeLessThanOrEqual(900);
	const scrolls = await page.evaluate(() => document.body.scrollHeight > window.innerHeight + 1);
	expect(scrolls, 'the page scrolls as a whole').toBe(false);
});

test('the expanded rail is tight around its labels, not a quarter of the window', async ({
	page
}) => {
	// 208 sits close around the longest label ("Collections"), with the boundaries loose enough
	// that a token nudge does not break this but something 240-shaped, with dead space beside the
	// words, would.
	await page.goto('/browse');

	await railWidth(page).toBeGreaterThan(180);
	await railWidth(page).toBeLessThan(224);
});

test('the rail collapses to icons and stays collapsed', async ({ page }) => {
	await page.goto('/browse');

	await railWidth(page).toBeGreaterThan(150);
	await expect(page.locator('nav.rail .label').first()).toBeVisible();
	/* Counted before, and asserted against itself after, rather than a number copied from
	 * `nav.ts`. Adding a row to the rail must not fail this. What the test is about is that
	 * collapsing hides the WORDS and keeps the destinations.
	 */
	const destinations = await page.locator('nav.rail a.item').count();
	expect(destinations).toBeGreaterThan(5);

	await toggle(page).click();

	await railWidth(page).toBeLessThan(100);
	await expect(page.locator('nav.rail .label').first()).toBeHidden();
	// Still all there: collapsed, not shortened.
	await expect(page.locator('nav.rail a.item')).toHaveCount(destinations);
});

test('and it is still collapsed after a reload', async ({ page }) => {
	// Remembered in this browser rather than on the account, so it is available before the first
	// paint and cannot flash the wrong width. See `rail-state.svelte.ts`.
	await page.goto('/browse');
	await toggle(page).click();
	await railWidth(page).toBeLessThan(100);

	await page.reload();

	await railWidth(page).toBeLessThan(100);
});

test('the brand becomes the mark, and still goes home', async ({ page }) => {
	/* The lockup at 64px wide is four letters nobody can read. What must not change is that it is a
	 * link: "press the logo to get home" is a thing everybody already knows, and losing it at one
	 * width would be losing it unpredictably. */
	await page.goto('/tags');
	await toggle(page).click();

	const brand = page.locator('nav.rail a.brand');
	await expect(brand).toHaveAttribute('href', '/browse');

	/* Measured rather than named. The art is markup (an SVG behind a `src` is a separate
	 * document and cannot see this page's stylesheet, so the accent would never reach it), and
	 * there is no filename to match on. What tells the two apart is their shape: the mark is
	 * one letter and close to square, the lockup is that letter plus a word and much wider. Reading
	 * the shape is also the assertion that cannot be satisfied by the component agreeing with
	 * itself, which a class name or a test id would be. */
	const art = brand.locator('svg');
	const collapsed = await art.boundingBox();
	expect(collapsed).not.toBeNull();
	expect(collapsed!.width / collapsed!.height).toBeLessThan(1.5);

	await toggle(page).click();
	const expanded = await art.boundingBox();
	expect(expanded).not.toBeNull();
	expect(expanded!.width / expanded!.height).toBeGreaterThan(2);

	await toggle(page).click();
	await brand.click();
	/* The PATH, and deliberately not the whole address. Every wall writes the row it settled at
	   into its own query as `?from=`, so `/browse` grows one the moment the grid lands a page,
	   which it does or does not do inside the five seconds this waits, depending on what else the
	   suite has put in the library. Anchored on the whole string this would pass most runs and fail
	   perhaps one in eight, in a check that is about one thing: pressing the logo goes home. */
	await expect(page).toHaveURL((url) => url.pathname === '/browse');
});

test('a window with no room for labels collapses whatever the button last said', async ({
	page
}) => {
	// The narrow band is not a preference. There is no room for 208px of nav beside a usable grid,
	// and letting the button force it open would push the grid off the side of the screen.
	await page.goto('/browse');
	await expect(page.locator('nav.rail .label').first()).toBeVisible();

	await page.setViewportSize({ width: 900, height: 900 });

	await expect(page.locator('nav.rail .label').first()).toBeHidden();
	await railWidth(page).toBeLessThan(100);
});

test('the marker for where you are sits inside the rail', async ({ page }) => {
	/* Not bled off to the window's outermost pixel: with the content inset as a floating card,
	 * that would be a blue tick stuck to the side of the screen, attached to nothing.
	 */
	await page.goto('/browse');

	const marker = await page.locator('nav.rail a.item.active').evaluate((element) => {
		const before = getComputedStyle(element, '::before');
		return { left: before.left, rail: element.closest('nav')!.getBoundingClientRect().left };
	});

	// Inside the rail, not past its left edge.
	expect(parseFloat(marker.left)).toBeGreaterThan(-13);
	expect(marker.rail).toBe(0);
});

test('the tile size survives going to another screen and back', async ({ page }) => {
	// A `Grid` is built per screen, so the size must be kept outside it, or it would be thrown away
	// on every navigation and read as the size control not working.
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })
		})
	);
	await page.goto('/browse');

	const slider = page.locator('.size input[type="range"]');
	await slider.fill('0');
	const chosen = await slider.inputValue();

	await page.goto('/tags');
	await page.goto('/browse');

	await expect(page.locator('.size input[type="range"]')).toHaveValue(chosen);
});

test('the settings section title lines up with the top of the search box', async ({ page }) => {
	/* The search box heads the left column and the section's title heads the right, so the two
	 * columns start on one line: the pane stands on the search box's row rather than a row below
	 * it, with the same inset from that row's top.
	 *
	 * Measured as a shared top edge rather than as a pixel offset: the number is whatever the
	 * type scale makes it, and asserting the number would be asserting the font.
	 */
	await page.goto('/settings/library');

	/* The box as drawn: the field's frame around the input, not the input inside it. */
	const searchBox = page.getByRole('searchbox', { name: 'Search settings' }).locator('xpath=..');
	const sectionTitle = page.getByRole('heading', { name: 'Folders', level: 1 });
	await expect(searchBox).toBeVisible();
	await expect(sectionTitle).toBeVisible();

	/* Measured with the real typeface, not the one the browser starts with.
	 *
	 * Both boxes here are TEXT, and where the top of a line box sits is decided by the font's own
	 * metrics, so a measurement taken before the webfont arrives is a measurement of the
	 * fallback, and the two headings are at different sizes, so the fallback moves them by
	 * different amounts, just past the tolerance and only now and then. Waited for, then given
	 * two frames to paint with, and then ASKED. The last step is what turns a silent wrong
	 * measurement into a sentence.
	 */
	await page.evaluate(async () => {
		await document.fonts.ready;
		await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
	});
	expect(
		await page.evaluate(() => document.fonts.check('16px "Archivo Variable"', 'Folders')),
		'the interface font never arrived, so both measurements below would be of the fallback'
	).toBe(true);

	const box = (await searchBox.boundingBox())!;
	const title = (await sectionTitle.boundingBox())!;

	// Within a couple of pixels: a box's frame and a line of type begin at the same line without
	// being identical heights.
	expect(
		Math.abs(box.y - title.y),
		`the search box starts at ${box.y} and the section title at ${title.y}`
	).toBeLessThan(4);
});

test("a focus ring near the pane's own edge is not clipped by its scrollbar", async ({ page }) => {
	/* A focus ring is painted OUTSIDE the control's own box, the same way a scrollbar sits
	 * outside the text, and a scrolling container clips both at its own edge. So the pane
	 * reserves room on every side, or the first field in a section has its ring cut off flush
	 * along the top and the start edge.
	 *
	 * Measured directly against the pane's own edge rather than against a hard-coded pixel, so
	 * this does not care which section happens to be first or how wide the ring token is, only
	 * that there is room for one.
	 */
	await page.setViewportSize({ width: 1400, height: 900 });
	/* Profile, because the PIN field is at the very TOP of its pane, which is what this test
	   needs. */
	await page.goto('/settings/profile');

	const pin = page.getByLabel('New PIN');
	await pin.focus();

	/* The SCROLLING box, named by its role rather than by a class. `.pane` is on more than one
	   element inside a settings screen (the shell's scrolling section and, on some sections, a
	   block inside it), and it is the section's own edge that clips a ring. */
	const paneBox = (await page.getByRole('region', { name: 'Profile' }).boundingBox())!;
	const pinBox = (await pin.boundingBox())!;

	const RING_WIDTH = 4; // the outer layer of --focus-ring: `0 0 0 4px ...`
	expect(
		pinBox.x - paneBox.x,
		`the field sits only ${pinBox.x - paneBox.x}px from the pane's start edge`
	).toBeGreaterThanOrEqual(RING_WIDTH);
	expect(
		pinBox.y - paneBox.y,
		`the field sits only ${pinBox.y - paneBox.y}px from the pane's top edge`
	).toBeGreaterThanOrEqual(RING_WIDTH);
});

test('the screen name sits above both columns rather than inside the list', async ({ page }) => {
	// The structural half of the same rule, and what makes the alignment hold when the type scale
	// moves: the title is a row of the grid, not the first item of the nav.
	await page.goto('/settings/library');

	const title = page.locator('.settings .title');
	await expect(title).toHaveText('Settings');
	expect(
		await title.evaluate((el) => el.closest('nav') !== null),
		'the screen name is still inside the sections list'
	).toBe(false);
});

test('a rail row is not told its own name twice', async ({ page }) => {
	/* The tooltip is for a control with no visible name. Expanded, every row says what it is in
	 * words beside the icon, and pointing at one produced a small box next to it repeating the word
	 * already on screen, following the pointer down a list of them.
	 *
	 * Collapsed it is the only thing naming the icon, so it comes back. `aria-label` is on the link
	 * at both widths regardless: a tooltip describes rather than names, and only while it is open.
	 */
	await page.goto('/browse');

	const browse = page.getByRole('link', { name: 'Browse' });
	await expect(browse).toBeVisible();
	await browse.hover();
	await page.waitForTimeout(600);
	await expect(page.getByRole('tooltip')).toHaveCount(0);

	await toggle(page).click();
	await railWidth(page).toBeLessThan(100);

	await page.mouse.move(0, 0);
	await browse.hover();
	await expect(page.getByRole('tooltip')).toHaveText('Browse');
});

test('the filters control says what it is, and says why when it cannot act', async ({ page }) => {
	await page.goto('/browse');

	/*
	 * The row is glyphs, and that is a decision. So this asserts what has to be true of a glyph.
	 *
	 * The bar's marks are distinct by construction, so no words are needed to tell them apart. What
	 * the words would carry is checked here: the control still has a NAME (what a screen reader
	 * reads and what this locator resolves by), and the tooltip still says it in words for anybody
	 * who points at it, because on a dimmed trigger that sentence is the only place the reason can
	 * be read.
	 */
	const filter = page.getByRole('button', { name: 'Filter', exact: true });
	await expect(filter).toBeVisible();
	await expect(filter).toHaveAttribute('aria-label', 'Filter');

	/* Shut to begin with, and it says so where a screen reader can hear it. The panel is drawn over
	   the screen rather than in the column with it, so nothing about the page moving says whether it
	   is open, which is exactly why the trigger has to. */
	await expect(filter).toHaveAttribute('aria-expanded', 'false');
	await filter.click();
	await expect(filter).toHaveAttribute('aria-expanded', 'true');
	await page.keyboard.press('Escape');

	/*
	 * And the words, on the screen where they are the only place the reason can be read.
	 *
	 * Not on a wall of files: pointing at the control there OPENS the panel, deliberately, so there
	 * is no hovering it without acting on it. On a screen with nothing to narrow it is dimmed, it
	 * opens nothing, and the tooltip is the whole of what a person gets.
	 *
	 * A wall rather than Settings: reaching Settings from a screen opens it as a PANEL, and the
	 * veil over the page is what the pointer lands on. The review queues, because a wall that grows
	 * facets stops being the example; this is a screen that is not a wall of things at all.
	 */
	await page.goto('/organize');
	const dimmed = page.getByRole('button', { name: 'Filter', exact: true });
	await expect(dimmed).toBeDisabled();
	await dimmed.hover();
	/* The SCREEN's own reason where it has one, not the general sentence, which is the half that
	   tells somebody whether narrowing is missing here or merely not built yet. */
	await expect(page.getByRole('tooltip')).toHaveText(
		'This screen lists things to review, not files'
	);
});

test('every settings section title is the same size', async ({ page }) => {
	/* One size, set one way: an unresolved custom property in a `font:` shorthand makes the whole
	 * declaration invalid, so a heading asking for a token that does not exist silently inherits
	 * body size. Nothing warns about a missing custom property.
	 */
	const sections = ['library', 'jobs', 'downloads', 'playback', 'privacy', 'appearance', 'about'];
	const sizes: Record<string, string> = {};

	for (const section of sections) {
		await page.goto(`/settings/${section}`);
		const title = page.locator('.settings .pane h1').first();
		await expect(title).toBeVisible();
		sizes[section] = await title.evaluate((el) => getComputedStyle(el).fontSize);
	}

	const distinct = new Set(Object.values(sizes));
	expect(distinct.size, `sizes were ${JSON.stringify(sizes)}`).toBe(1);
	// And it is the larger one, not the body size everything could have agreed on by accident.
	expect(parseFloat([...distinct][0])).toBeGreaterThan(20);
});

test('the settings close button is a circle, not an oval', async ({ page }) => {
	// A fixed square with a round border (a circle) and no stacked "Esc" hint making it taller
	// than it is wide (Escape still closes it). The close button is on the PANEL (settings opened
	// over a screen), not on the standalone page. Reach it the way a person does: from the rail,
	// which opens the panel.
	await page.goto('/browse');
	await page.getByRole('link', { name: 'Settings' }).click();
	const close = page.locator('.close');
	await expect(close).toBeVisible();
	const box = (await close.boundingBox())!;
	expect(
		Math.abs(box.width - box.height),
		`close is ${box.width}x${box.height}`
	).toBeLessThanOrEqual(1);
});

test('the sharing legend in Settings shows every mark the app can draw', async ({ page }) => {
	/* A legend is only worth having if it is the truth, and the way it stops being the truth is a
	 * new state added to the marks and not added here. It is built from `markFor`, the same function
	 * the tiles call, so this checks the rendered result rather than the list: every glyph the app
	 * has, in both its filled and hollow forms, with words beside it.
	 */
	await signInAsAdmin(page);
	await page.goto('/settings/appearance');

	const legend = page.locator('.legend');
	await expect(legend).toBeVisible();

	// The three answers...
	await expect(legend).toContainText('guests you chose can see this');
	await expect(legend).toContainText('guests you chose can never see this');
	await expect(legend).toContainText('some guests and restricted from others');
	// ...the fact that every one of them is a button...
	await expect(legend).toContainText('Press to see what decides this.');
	// ...and the vault, which is a different kind of fact wearing the same corner.
	await expect(legend).toContainText('unhide with your PIN');

	/* Drawn as the mark a TILE draws, ground and all, and one of them wears the amber that says
	   look closer. The class is `.on-a-tile`, not `.chip`: a chip is a label you press or remove
	   and this is a badge with a scrim behind it. The count is asserted, so a selector that
	   matches nothing says so plainly. */
	await expect(legend.locator('.on-a-tile')).toHaveCount(6);
	await expect(legend.locator('.on-a-tile.both')).toHaveCount(1);
	await expect(legend.locator('.on-a-tile.restricted')).toHaveCount(2);
});

test('every control in Settings starts at the same place, beside the name it belongs to', async ({
	page
}) => {
	/* A switch drawn under its heading reads as a stray toggle floating below a sentence, and
	 * rows whose controls each size their own column line up with nothing.
	 *
	 * The rule is one grid, laid out by the row rather than by each pane, so what this really
	 * guards is the next control somebody adds. Measured across every kind of control rather than
	 * only switches: a menu and a number box agreeing with each other is the half that matters.
	 */
	await signInAsAdmin(page);

	/* Panes with controls on them. Updates is not one: its rows are facts (a version, the licence)
	   and its one control stands there only while a version is hidden. */
	for (const section of ['appearance', 'playback', 'privacy', 'performance']) {
		await page.goto(`/settings/${section}`);
		// Drawn once the stored values land, not with the section.
		await expect(page.locator('.row .control').first()).toBeVisible();

		const rows = await page.evaluate(() => {
			const out: {
				label: string;
				left: number;
				right: number;
				figure: boolean;
				controlTop: number;
				nameBottom: number;
			}[] = [];
			/* `.loose` rows are excluded, and that is the rule rather than an exception to it.
			 *
			 * A loose row states a FACT (the card's name, a version, a licence), and its right
			 * column is `auto` on purpose: a short fact pinned to the far edge of a fifteen-rem
			 * column reads as a table with a hole in the middle, so a fact sits beside the words it
			 * is a fact about. A fixed column is what makes a stack of CONTROLS line up, and
			 * controls are what this measures. Reading the two together would go red on any
			 * pane that holds both. */
			for (const row of Array.from(document.querySelectorAll('.row:not(.loose)'))) {
				const control = row.querySelector('.control');
				const name = row.querySelector('.name');
				if (!control || !name || control.childElementCount === 0) continue;
				const mine = control.getBoundingClientRect();
				const words = name.getBoundingClientRect();
				out.push({
					label: (name.textContent ?? '').slice(0, 40),
					left: Math.round(mine.left),
					right: Math.round(mine.right),
					// An action row's figure (`ActionRow`'s note) beside its press.
					figure: control.querySelector(':scope > .note') !== null,
					controlTop: mine.top,
					nameBottom: words.bottom
				});
			}
			return out;
		});

		expect(rows.length, `${section} should have a control to measure`).toBeGreaterThan(0);
		/* Every control ENDS on one edge. Every control STARTS at one place too, except an action
		   row's whose figure and press need more than the column on one line: that column grows
		   to the left to hold them, so the figure never pushes the press under it. */
		const ends = new Set(rows.map((row) => row.right));
		expect(
			ends.size,
			`${section}: controls end at ${[...ends].join(', ')}, for ${rows
				.map((row) => row.label)
				.join(' | ')}`
		).toBe(1);
		const plain = rows.filter((row) => !row.figure);
		const edges = new Set(plain.map((row) => row.left));
		expect(
			edges.size,
			`${section}: controls start at ${[...edges].join(', ')}, for ${plain
				.map((row) => row.label)
				.join(' | ')}`
		).toBeLessThanOrEqual(1);
		const start = Math.min(...plain.map((row) => row.left));
		for (const row of rows.filter((one) => one.figure)) {
			expect(row.left, `${section}: ${row.label} starts after the column`).toBeLessThanOrEqual(
				start
			);
		}
		for (const row of rows) {
			/* Beside the words that name it, rather than under them.
			 *
			 * Overlap rather than a shared centre line: a row's name has its help text under it,
			 * so the control sits centred against the PAIR and is a few pixels below the name's
			 * own middle. That is the layout working. What must never happen is the control
			 * starting below where the name ends.
			 */
			expect(row.controlTop, `${section}: ${row.label}`).toBeLessThan(row.nameBottom);
		}
	}
});

test('pressing a mark on a tile opens the sharing panel', async ({ page }) => {
	/* The tooltip only helps somebody who already suspected the glyph meant something. Pressing one
	 * takes them to the panel that answers both halves (who, and where the decision was made),
	 * and it is why the marks are siblings of the tile's button rather than children of it: a
	 * button inside a button is not valid markup. The legend that explains the glyphs is in
	 * Settings, and is a thing you read once. */
	await signInAsAdmin(page);

	const items = [
		{
			id: 'm1',
			media_type: 'video',
			width: 1920,
			height: 1080,
			duration_ms: 61_000,
			favorite: false,
			rating: null,
			concealed: false,
			thumb: true,
			shared: true,
			shared_here: true
		}
	];
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: 1, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'image/png',
			body: Buffer.from(
				'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
				'base64'
			)
		})
	);

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	/* The tooltip first, because the press has to take it away with it.
	 *
	 * Settled, then pointed at from somewhere else, and both halves are needed. The label opens on a
	 * pointer MOVE rather than on entering: a control REBUILT under a stationary hand fires enter
	 * with nobody having pointed at anything, and suppressing that is the whole reason for the rule.
	 * The grid rebuilds its tiles as the first page of files settles, so a hover fired before that
	 * lands on a chip that is then replaced, and no move follows onto the new one. It passes alone
	 * and fails in a loaded suite, which is what that looks like. */
	/* `.mark`, not `.chip`: a chip is a label you press or remove, and this is a badge on a
	   picture. The poll below asks whether a box has stopped moving, so a selector that matched
	   nothing would read as a tile that would not settle. */
	const mark = page.locator('.marks .mark').first();
	await expect
		.poll(async () => {
			const first = await mark.boundingBox();
			await page.waitForTimeout(120);
			const second = await mark.boundingBox();
			return first && second && first.x === second.x && first.y === second.y;
		})
		.toBe(true);
	await page.mouse.move(0, 0);
	/* Through the tile: the marks wait for the pointer by default and take no press or hover
	   until the tile is under it. */
	await page.locator('.tile-frame').first().hover();
	await mark.hover();
	await expect(page.getByRole('tooltip')).toBeVisible();

	/* A real click, which is what makes this reproduce.
	 *
	 * Pressing the mark focuses it, and a panel RESTORES FOCUS to whatever opened it when it closes.
	 * So the control gets focus back a moment after the panel goes away, with the pointer nowhere
	 * near it, and the label must not appear on its own. */
	await mark.click();

	await expect(page.locator('.share-sheet')).toBeVisible();
	// ...and it did not open the asset on the way past.
	await expect(page).not.toHaveURL(/\/asset\//);

	/* And the label stays gone once the panel is shut again.
	 *
	 * A tooltip is dismissed by the pointer LEAVING, and pressing a control that opens a panel
	 * over itself takes the pointer out of the picture without it ever crossing the trigger's
	 * edge. So the bubble could sit behind the panel and still be there when it closed, or come
	 * straight back, because the pointer never went anywhere. A press latches it shut until the
	 * pointer has actually been somewhere else.
	 */
	await expect(page.getByRole('tooltip')).toBeHidden();

	/* Shut the panel by clicking beside it, which matters here: closing with Escape is a KEYBOARD
	 * interaction, and after one of those the browser rightly treats the restored focus as
	 * keyboard focus, and a tooltip then is correct. Clicking away is mouse modality, the pointer
	 * ends up nowhere near the mark, and nothing should appear.
	 */
	/* Waited for the panel to have finished APPEARING, and that is a real condition rather than a
	 * sleep. A panel registers its click-away listener once it is up, not while it is arriving:
	 * otherwise the very click that opened it would close it again on the way past. So a click
	 * landing inside that window (about fifty milliseconds, reachable only by a test clicking as
	 * fast as a machine can) is ignored, correctly. `data-starting-style` is the panel's own mark
	 * for "still coming in".
	 */
	await expect(page.locator('.share-sheet')).not.toHaveAttribute('data-starting-style');
	await page.mouse.click(4, 400);
	await expect(page.locator('.share-sheet')).toBeHidden();

	// And it must STAY gone. Focus comes back to the mark about here, which is what was showing it,
	// and the tile must not light up either, which is the same distinction one level out.
	await page.waitForTimeout(500);
	await expect(page.getByRole('tooltip')).toBeHidden();

	/* And the tile itself must not be lit: focus put back by a closing panel must not match
	 * `:focus-within` and give the tile its hover treatment. Measured as the shadow the tile
	 * actually has.
	 */
	const lit = await page
		.locator('.tile')
		.first()
		.evaluate((tile) => getComputedStyle(tile).boxShadow);
	expect(lit === 'none' || lit === '').toBe(true);
});

test('and pressing the crossed-out eye opens Hidden rather than Sharing', async ({ page }) => {
	/*
	 * The two glyphs sit side by side and call different handlers: somebody pressing a crossed-out
	 * eye is asking what is keeping this off their OWN screen, and hiding reaches nobody else at
	 * all: the Sharing panel is the right panel for the wrong question.
	 *
	 * Asserted BOTH ways round. "The Hidden panel opened" alone would pass on a build that opened
	 * both, and the whole point of the split is that the sharing sheet is not what you get.
	 */
	await signInAsAdmin(page);

	const items = [
		{
			id: 'h1',
			media_type: 'video',
			width: 1920,
			height: 1080,
			duration_ms: 61_000,
			favorite: false,
			rating: null,
			concealed: false,
			thumb: true,
			hidden: true,
			hidden_here: true
		}
	];
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: 1, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'image/png',
			body: Buffer.from(
				'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
				'base64'
			)
		})
	);

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	/* Settled before it is pressed. The grid rebuilds its tiles as the first page lands, so a press
	   aimed before that lands on a mark that is then replaced. */
	const eye = page.locator('.marks .mark.vaulted').first();
	await expect
		.poll(async () => {
			const first = await eye.boundingBox();
			await page.waitForTimeout(120);
			const second = await eye.boundingBox();
			return first && second && first.x === second.x && first.y === second.y;
		})
		.toBe(true);
	/* The marks wait for the pointer by default: until the tile is under it they take no press,
	   so the tile is pointed at first, the way a person reaches the eye. */
	await page.locator('.tile-frame').first().hover();
	await eye.click();

	const panel = page.getByRole('dialog').filter({ hasText: 'Hidden is about your own screen' });
	await expect(panel).toBeVisible();
	await expect(page.locator('.share-sheet')).toHaveCount(0);
	// ...and it did not open the file on the way past.
	await expect(page).not.toHaveURL(/\/asset\//);
});
