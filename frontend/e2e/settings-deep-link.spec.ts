import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test, type Locator, type Page } from '@playwright/test';
import { REGISTRY_HOME, resolveAddress, settingsPath } from '../src/lib/settings-ui/sections';
import { signInAsAdmin } from './admin';

/*
 * The settings deep link lands on its row and rings it, through EVERY door, every time.
 *
 * A deep link is an address with a row in it: `/settings/{section}#{key}`. It is made by five doors
 * (a `SettingLink` in a sentence, a search result, an address typed or bookmarked, an address
 * that was retired and redirects, and a row that is on a sub-page or behind a fold), and it can
 * fail in one of two silent ways: the hunt for the row gives up while a big pane is still loading,
 * or the row is hidden and the ring lights something nobody can see.
 *
 * `settings-anchor.svelte.test.ts` holds the hunt and `sections.test.ts` the one resolver every
 * door goes through; `test_every_settings_result_points_at_something.py` proves statically that
 * every search entry and every `SettingLink` resolves to an element on the pane it opens. What only
 * a real browser can say is that the row is ON SCREEN and RUNG when the person arrives: one
 * journey per door, below.
 */

/** The row an address names, found by its id (which has dots in it) and its ring. */
function row(page: Page, key: string) {
	return page.locator(`[id="${key}"]`);
}

async function rung(page: Page, key: string) {
	const one = row(page, key);
	await expect(one).toBeVisible({ timeout: 15_000 });
	await expect(one).toHaveAttribute('data-sift-found', '', { timeout: 15_000 });
	await expect(one).toBeInViewport();
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1600, height: 1000 });
});

test('a SettingLink in a sentence opens the pane and rings the row', async ({ page }) => {
	/* A file page whose playback repair is off says so on its stage and ends the sentence with the
	   switch as the link. Served as a fake so the page is the same on every checkout: the DESIGN
	   GALLERY, which draws the link's specimen, is its own repository and is not in a clean one. */
	await page.route('**/api/assets/d1', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				id: 'd1',
				media_type: 'video',
				width: 1280,
				height: 720,
				duration_ms: 12_000,
				favorite: false,
				rating: null,
				concealed: false,
				enriched_by: [],
				playback_repair: 'off',
				original_filename: 'clip.mp4'
			})
		})
	);
	await page.route('**/api/assets/d1/plan*', (route) => route.fulfill({ status: 404 }));
	await page.goto('/asset/d1');
	/* The stage keeps drawing while the fake's clip never arrives, so the link is pressed by its
	   own event rather than waited on to hold still: what is proved is where the link lands. */
	const link = page.getByRole('link', { name: 'Turn it on' }).first();
	await expect(link).toBeAttached({ timeout: 15_000 });
	await link.dispatchEvent('click');

	/* The link names Performance; the row is drawn on Importing, so the address it opens is
	   Importing's: the resolver's row map, followed from a link. */
	await expect(page).toHaveURL(/\/settings\/importing#performance\.repair_playback$/);
	await rung(page, 'performance.repair_playback');
});

test('a search result opens the pane it lands on and rings the row', async ({ page }) => {
	await page.goto('/settings/appearance');
	await page.locator('.search-slot input').fill('play the next file after');
	await page.locator('[aria-label="Search results"] .under button').first().click();

	await expect(page).toHaveURL(/\/settings\/playback#theater\.timer_seconds$/);
	await rung(page, 'theater.timer_seconds');
});

test('an address typed or bookmarked rings its row on arrival', async ({ page }) => {
	await page.goto('/settings/editing#editing.delete.ask');

	await rung(page, 'editing.delete.ask');
});

test('a RETIRED section lands on the pane that inherited it, rewrites the address, and rings', async ({
	page
}) => {
	await page.goto('/settings/theater#theater.layout');

	await expect(page).toHaveURL(/\/settings\/playback#theater\.layout$/);
	await rung(page, 'theater.layout');
});

test('a retired section that became a TAB lands on that tab', async ({ page }) => {
	await page.goto('/settings/ledger');

	await expect(page).toHaveURL(/\/settings\/tasks\?show=history$/);
	await expect(page.getByRole('tab', { name: 'App History', selected: true })).toBeVisible();
});

test('a row on a pane still LOADING long past six seconds is rung once it is drawn', async ({
	page
}) => {
	/* A pane that takes longer to draw than a fixed wait would make the link give up before the
	   row exists. Held back eight seconds here, well past any such wait. */
	await page.route('**/api/settings', async (route) => {
		await new Promise((settle) => setTimeout(settle, 8000));
		await route.continue();
	});
	await page.goto('/settings/performance#performance.repair_playback');

	await expect(page).toHaveURL(/\/settings\/importing#performance\.repair_playback$/);
	await rung(page, 'performance.repair_playback');
});

test('a row on a SUB-PAGE opens the sub-page and rings it', async ({ page }) => {
	/* The stash-box field rules are one level down, behind their group's Edit. */
	await page.goto('/settings/stash-boxes#enrich.person.name');

	await rung(page, 'enrich.person.name');
});

test("a task's When, chosen in the menu beside Run now, opens that menu", async ({ page }) => {
	/* The row is rung and its answers are behind the chevron: the ring opens the menu, with the
	   answer in force under the focus, so nobody is left wondering where to press. */
	await page.goto('/settings/schedule#tasks.duplicates.when');

	await rung(page, 'tasks.duplicates.when');
	const door = row(page, 'tasks.duplicates.when').locator(
		'[data-setting-door] [aria-haspopup="menu"]'
	);
	await expect(door).toHaveAttribute('aria-expanded', 'true');
	await expect(page.getByRole('menu')).toBeVisible();
	await expect(page.getByRole('menuitemcheckbox', { checked: true }).first()).toBeVisible();
});

test('a task row named on the old Activity address opens the Tasks tab and rings it', async ({
	page
}) => {
	await page.goto('/settings/jobs#tasks.duplicates.when');

	await expect(page).toHaveURL(/\/settings\/tasks\?show=now#tasks\.duplicates\.when$/);
	await rung(page, 'tasks.duplicates.when');
	await expect(page.getByRole('tab', { name: 'Tasks', selected: true })).toBeVisible();
});

/*
 * EVERY ADDRESS SETTINGS GIVES OUT, walked: each registered setting and each row a pane declares
 * for itself, opened by its address. Each lands on its row, open, on screen and rung, or the pane
 * says in a sentence why the row is not drawn this moment; none says the setting is not on the page.
 *
 * The rows a pane declares are read from the `SEARCHABLE` declarations beside each pane (the
 * search's second feeder), as text, since the index itself is a client module.
 */
const NOT_ON_THE_PAGE = "That setting isn't on this page.";

function declaredRows(): { section: string; key: string }[] {
	const here = dirname(fileURLToPath(import.meta.url));
	const folder = join(here, '..', 'src', 'lib', 'settings-ui');
	const found: { section: string; key: string }[] = [];
	for (const file of readdirSync(folder).filter((name) => name.endsWith('.search.ts'))) {
		const text = readFileSync(join(folder, file), 'utf8');
		for (const block of text.matchAll(/\{[^{}]*\}/g)) {
			const key = block[0].match(/\bkey:\s*'([^']+)'/)?.[1];
			const section = block[0].match(/\bsection:\s*'([^']+)'/)?.[1];
			if (key && section) found.push({ section, key });
		}
	}
	return found;
}

/* The ring is set as the scroll to its row begins, and the scroll is a smooth one: the row is given
   the time the scroll takes to arrive rather than measured part of the way there. */
async function arrives(lit: Locator): Promise<boolean> {
	try {
		await expect(lit).toBeInViewport({ timeout: 5_000 });
		return true;
	} catch {
		return false;
	}
}

test('every address settings gives out lands on a row that is seen, or says why not', async ({
	page
}) => {
	test.setTimeout(15 * 60_000);
	const answer = await page.request.get('/api/settings');
	const { sections } = (await answer.json()) as {
		sections: { name: string; settings?: { key: string; label?: string }[] }[];
	};
	const registered = sections.flatMap((one) =>
		(one.settings ?? [])
			.filter((entry) => entry.label)
			.map((entry) => ({
				section: REGISTRY_HOME[one.name] ?? one.name.toLowerCase(),
				key: entry.key
			}))
	);
	const walked = new Set<string>();
	const unseen: string[] = [];
	for (const named of [...registered, ...declaredRows()]) {
		const to = resolveAddress(named.section, named.key);
		const address = settingsPath(to);
		if (walked.has(address)) continue;
		walked.add(address);
		/* Each address opened as one typed or bookmarked is: a page load of its own. Followed from
		   the last one's page instead, a ring or a toast left by the address before is what this
		   would read. */
		await page.goto('about:blank');
		await page.goto(address);
		const lit = page.locator('[data-sift-found]').first();
		const said = page.locator('.toast .message').first();
		// Either, or both: a row can be rung while a toast says what about it is folded away.
		await expect(lit.or(said).first()).toBeVisible({ timeout: 20_000 });
		if (await said.isVisible()) {
			const sentence = (await said.textContent()) ?? '';
			if (sentence.includes(NOT_ON_THE_PAGE)) unseen.push(`${address}: ${sentence}`);
			continue;
		}
		const ownRow = row(page, to.key ?? '');
		if ((await ownRow.count()) > 0 && !(await ownRow.first().isVisible()))
			unseen.push(`${address}: rung and not visible`);
		else if (!(await lit.isVisible())) unseen.push(`${address}: rung and hidden`);
		else if (!(await arrives(lit))) unseen.push(`${address}: rung off screen`);
	}
	expect(walked.size).toBeGreaterThan(150);
	expect(unseen).toEqual([]);
});
