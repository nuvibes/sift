import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * The recognized pile on /organize/tagger: its two states, Waiting and Answered, each paged.
 *
 * A tab reading 0 beside a large enriched count looks switched off when every recognized file has
 * been answered. So the pile has two states at the far end of the tab line (`TaggerPanel`'s
 * `stateChips`), the state lives in the address (`?show=answered`), and a page of the waiting rows
 * is not a page of the answered ones: turning to the other state starts it at its own first page.
 *
 * MOCKED, and that is the honest limit of this file: a row in this pile is a stash-box's answer
 * about a file, and the suite has no stash-box to ask (no spec seeds one). So the pile is served
 * here in the wire's own shape (`MatchList` / `MatchView`), and what is proven is the screen: which
 * state and which page it ASKS for, and that it draws what it is handed under the right chip. The
 * server's half (that `state=answered` returns the answered rows) is the Python suite's.
 */

const WAITING = 30;
const ANSWERED = 5;

type Ask = { state: string; offset: number; limit: number };

function match(state: 'waiting' | 'answered', at: number) {
	const name = `${state}-row-${String(at).padStart(2, '0')}`;
	return {
		art: null,
		asset_id: `${state}-asset-${at}`,
		box_id: 'box-1',
		box_name: 'Example Box',
		changes: [],
		creates: [],
		decided_at: state === 'answered' ? 1_700_000_000_000 : null,
		found_at: 1_700_000_000_000,
		grade: 'strong',
		record: {
			confidence: 1,
			disambiguation: null,
			every_word: true,
			extra: {},
			fields: { title: name },
			file_count: null,
			icon_slug: null,
			image_url: null,
			name,
			remote_id: `remote-${state}-${at}`,
			source_id: 'box-1',
			source_name: 'Example Box',
			subject: 'scene'
		},
		remote_id: `remote-${state}-${at}`,
		state: state === 'answered' ? 'confirmed' : 'pending'
	};
}

/*
 * The board, with the tagger's queue on it. The real board is asked and kept whole; only when it has
 * no `tagger` queue (a library with no stash-box has none, which is this suite's) is one added,
 * modelled on a queue the real board DID answer, so every field the route reads is the server's.
 */
async function withTaggerQueue(page: Page): Promise<void> {
	await page.route('**/api/workbench', async (route) => {
		const real = await route.fetch();
		const board = (await real.json()) as { queues: Record<string, unknown>[] };
		if (!board.queues.some((queue) => queue.name === 'tagger') && board.queues.length > 0) {
			board.queues.push({
				...board.queues[0],
				name: 'tagger',
				title: 'Files a stash-box recognized',
				count: WAITING
			});
		}
		await route.fulfill({ response: real, json: board });
	});
}

function servePile(page: Page): Ask[] {
	void withTaggerQueue(page);
	const asks: Ask[] = [];
	void page.route('**/api/stash-boxes/matches?*', async (route) => {
		const url = new URL(route.request().url());
		const state = url.searchParams.get('state') ?? 'waiting';
		const offset = Number(url.searchParams.get('offset') ?? '0');
		const limit = Number(url.searchParams.get('limit') ?? '24');
		asks.push({ state, offset, limit });
		const total = state === 'answered' ? ANSWERED : WAITING;
		const count = Math.max(0, Math.min(limit, total - offset));
		await route.fulfill({
			json: {
				answered: ANSWERED,
				total,
				// Where the page starts, which the pager's readout is drawn from.
				offset,
				matches: Array.from({ length: count }, (_, i) =>
					match(state === 'answered' ? 'answered' : 'waiting', offset + i)
				)
			}
		});
	});
	return asks;
}

async function readout(page: Page): Promise<string> {
	const where = page.locator('.frame-footer button.where');
	await expect(where).toBeVisible();
	return (await where.innerText()).replace(/\s+/g, ' ').trim();
}

test('Waiting and Answered are two states of one pile, each with its own pages', async ({
	page
}) => {
	const asks = servePile(page);
	await signInAsAdmin(page);
	await page.goto('/organize/tagger');

	const waiting = page.getByRole('link', { name: 'Waiting', exact: true });
	const answered = page.getByRole('link', { name: 'Answered', exact: true });
	await expect(waiting).toBeVisible();
	await expect(answered).toBeVisible();

	// Waiting, first page.
	await expect(page.getByText('waiting-row-00').first()).toBeVisible();
	await expect.poll(() => readout(page)).toMatch(/^1-24 of 30/);
	expect(asks.at(-1)).toMatchObject({ state: 'waiting', offset: 0 });

	// Paged.
	await page.getByRole('button', { name: 'Next page' }).click();
	await expect(page.getByText('waiting-row-24').first()).toBeVisible();
	await expect.poll(() => readout(page)).toMatch(/^25-30 of 30/);
	expect(asks.at(-1)).toMatchObject({ state: 'waiting', offset: 24 });

	// The other state, in the address, from ITS first page.
	await answered.click();
	/* The STATE in the address, read by name: the pile also writes where it was left (`from`,
	   `near`) once a page lands, and that is a position, not which pile is showing. */
	await expect.poll(() => new URL(page.url()).searchParams.get('show')).toBe('answered');
	await expect(page.getByText('answered-row-00').first()).toBeVisible();
	await expect(page.getByText('waiting-row-24')).toHaveCount(0);
	await expect.poll(() => readout(page)).toMatch(/^1-5 of 5/);
	expect(asks.at(-1)).toMatchObject({ state: 'answered', offset: 0 });

	// And back.
	await waiting.click();
	await expect(page).toHaveURL(/\/organize\/tagger(\?|$)/);
	await expect.poll(() => new URL(page.url()).searchParams.get('show')).toBeNull();
	await expect(page.getByText('waiting-row-00').first()).toBeVisible();
	expect(asks.at(-1)).toMatchObject({ state: 'waiting', offset: 0 });
});
