import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	DownloadQueue,
	canCancel,
	canGoFirst,
	canPause,
	canRemove,
	canResume,
	canRetry,
	destinationWord,
	lines,
	needsYou,
	queueSortFrom,
	SORTS,
	type DownloadItem
} from './queue.svelte';
import { SITE_ORDER, SORT_OPTIONS, sortIcon } from '$lib/grid/sort-state.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

/* The queue reads when the connection says it moved, so the thing worth guarding is when it stops
 * asking: a guest who types the address gets a 403 from the list, and that is a stop, not a thing to
 * keep asking about. Everything else is the server being briefly unreachable, and the last good list
 * stays on screen while the next read tries again.
 */

function item(over: Partial<DownloadItem> = {}): DownloadItem {
	return {
		// Where in the line a waiting row stands; null for every other state.
		position: null,
		// The job that runs it, for the Activity screen's link; null once nothing does.
		job_id: null,
		// The failure sentence the server chooses; null is what every row not failed carries.
		sentence: null,
		id: 'd1',
		status: 'done',
		dest_folder_id: null,
		folder: null,
		site: 'TikTok',
		username: 'creator',
		asset_id: 'a1',
		error: null,
		created_at: 0,
		error_code: null,
		error_tier: null,
		site_key: null,
		site_name: null,
		creator_scope: null,
		via: null,
		via_address: null,
		url: null,
		shown_url: null,
		filename: null,
		remembered_filename: null,
		files_offered: null,
		files_left_out: null,
		size_bytes: null,
		finished_at: null,
		site_id: null,
		person_id: null,
		progress: null,
		...over
	};
}

type Reply = (url: string, init: RequestInit) => Response;
let reply: Reply;
const calls: { url: string; method: string; body?: unknown }[] = [];

beforeEach(() => {
	calls.length = 0;
	vi.stubGlobal(
		'fetch',
		vi.fn(async (input: URL | string, init: RequestInit = {}) => {
			const url = String(input);
			calls.push({
				url,
				method: init.method ?? 'GET',
				body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined
			});
			return reply(url, init);
		})
	);
});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

function servePage(downloads: DownloadItem[]): void {
	reply = () =>
		new Response(JSON.stringify({ downloads, total: downloads.length }), { status: 200 });
}

function refuse(status: number): void {
	reply = () => new Response(JSON.stringify({ detail: 'nope' }), { status });
}

describe('DownloadQueue', () => {
	it('shows the downloads the list returns', async () => {
		servePage([item(), item({ id: 'd2', status: 'running' })]);
		const queue = new DownloadQueue();
		await queue.refresh();
		expect(queue.items.map((d) => d.id)).toEqual(['d1', 'd2']);
		expect(queue.total).toBe(2);
		expect(queue.problem).toBeNull();
	});

	it('stops asking when the caller is not an admin', async () => {
		refuse(403);
		const queue = new DownloadQueue();
		await queue.refresh();
		expect(queue.problem).toMatch(/admin/);

		// And it means it: another announcement makes no further request, because there is nothing
		// to come back for on behalf of somebody who is never going to be let in.
		const before = calls.length;
		await queue.refresh();
		expect(calls.length).toBe(before);
	});

	it('keeps the last good list when a read fails transiently', async () => {
		servePage([item()]);
		const queue = new DownloadQueue();
		await queue.refresh();
		refuse(500);
		await queue.refresh();
		expect(queue.items.map((d) => d.id)).toEqual(['d1']); // unchanged
		expect(queue.problem).not.toBeNull();
	});

	it('submits a pasted link and refreshes', async () => {
		reply = (url, init) => {
			if (init.method === 'POST')
				return new Response(JSON.stringify({ id: 'd9' }), { status: 201 });
			return new Response(JSON.stringify({ downloads: [item({ id: 'd9' })], total: 1 }), {
				status: 200
			});
		};
		const queue = new DownloadQueue();
		const refusal = await queue.submit('https://www.tiktok.com/@a/video/1');
		expect(refusal).toBeUndefined();
		expect(queue.items.map((d) => d.id)).toEqual(['d9']);
		expect(calls.some((c) => c.method === 'POST')).toBe(true);
	});

	// Both halves, and the second is the one that matters: the field the screen draws from has to
	// be set, not only returned: a returned message is not a shown message.
	it('shows a refused submit under the box as well as handing it back', async () => {
		reply = () =>
			new Response(JSON.stringify({ detail: 'Log in with your password first.' }), {
				status: 409
			});
		const queue = new DownloadQueue();
		const refusal = await queue.submit('https://www.tiktok.com/@a/video/1');
		expect(refusal).toBe('Log in with your password first.');
		expect(queue.submitError).toBe('Log in with your password first.');
	});

	it('shows a refused bulk queue under the box as well as handing it back', async () => {
		reply = () =>
			new Response(JSON.stringify({ detail: 'Sift does not ask Instagram for a whole profile.' }), {
				status: 400
			});
		const queue = new DownloadQueue();
		const refusal = await queue.queueAll('https://www.instagram.com/someone/');
		expect(refusal).toMatch(/whole profile/);
		expect(queue.submitError).toBe(refusal);
	});

	it('clears a previous refusal once a submit succeeds', async () => {
		refuse(400);
		const queue = new DownloadQueue();
		await queue.submit('https://example.com/nope');
		expect(queue.submitError).not.toBeUndefined();

		reply = (url, init) => {
			if (init.method === 'POST')
				return new Response(JSON.stringify({ id: 'd9' }), { status: 201 });
			return new Response(JSON.stringify({ downloads: [item({ id: 'd9' })], total: 1 }), {
				status: 200
			});
		};
		await queue.submit('https://www.tiktok.com/@a/video/1');
		expect(queue.submitError).toBeUndefined();
	});

	it('cancels a download and refreshes to the settled status', async () => {
		reply = (url, init) => {
			if (init.method === 'POST' && url.includes('/cancel'))
				return new Response(null, { status: 204 });
			return new Response(JSON.stringify({ downloads: [item({ status: 'canceled' })], total: 1 }), {
				status: 200
			});
		};
		const queue = new DownloadQueue();
		await queue.cancel('d1');
		expect(calls.some((c) => c.method === 'POST' && c.url.includes('/downloads/d1/cancel'))).toBe(
			true
		);
		expect(queue.items[0].status).toBe('canceled');
	});

	it('fetches a skipped link anyway and refreshes to the re-queued status', async () => {
		// The address is asserted, not just the method: a component that assembles the wrong one
		// looks identical from the payload's side, and nothing else here would notice.
		servePage([item({ status: 'skipped' })]);
		const queue = new DownloadQueue();
		await queue.refresh();
		const before = queue.items[0].status;

		reply = (url, init) => {
			if (init.method === 'POST' && new URL(url).pathname.endsWith('/anyway'))
				return new Response(null, { status: 204 });
			return new Response(JSON.stringify({ downloads: [item({ status: 'queued' })], total: 1 }), {
				status: 200
			});
		};

		await queue.anyway('d1');

		// The whole path, not a substring of it: `/anyways` contains `/anyway`, so an `includes`
		// check passes against an address the server does not answer.
		expect(
			calls
				.filter((c) => c.method === 'POST')
				.map((c) => new URL(c.url).pathname)
				.some((path) => path.endsWith('/downloads/d1/anyway'))
		).toBe(true);
		// Moved away from where it started, so the assertion is about the refresh rather than about
		// the state the list happened to open in.
		expect(before).toBe('skipped');
		expect(queue.items[0].status).toBe('queued');
	});

	it('leaves the row alone when fetching anyway is refused', async () => {
		servePage([item()]);
		const queue = new DownloadQueue();
		await queue.refresh();
		refuse(500);

		await queue.anyway('d1');

		expect(queue.items.map((d) => d.id)).toEqual(['d1']);
	});
});

/* The page decides for THIS paste: its folder and its switch go with the paste, on every route
   the box can take, and nothing chosen is sent as null, which the server reads as "follow the
   setting". A route that dropped them would quietly put the stored default back in charge. */
it('sends what the page chose for this paste on every route the box uses', async () => {
	reply = (url, init) =>
		init.method === 'POST'
			? new Response(JSON.stringify({ id: 'd1', queued: 1, refused: [], duplicates: 0 }), {
					status: 201
				})
			: new Response(JSON.stringify({ downloads: [], total: 0 }), { status: 200 });
	const queue = new DownloadQueue();
	const choices = { dest: 'f-2', remember: false };
	await queue.submit('https://a/1', choices);
	await queue.submitMany(['https://a/2', 'https://a/3'], choices);
	await queue.queueAll('https://a/4', choices);
	await queue.submit('https://a/5');
	const sent = calls.filter((one) => one.method === 'POST').map((one) => one.body);
	const chosen = { dest_folder_id: 'f-2', remember: false };
	expect(sent).toEqual([
		{ url: 'https://a/1', ...chosen },
		{ urls: ['https://a/2', 'https://a/3'], ...chosen },
		{ url: 'https://a/4', ...chosen },
		{ url: 'https://a/5', dest_folder_id: null, remember: null }
	]);
});

describe('a paste of several links', () => {
	function answering(body: unknown): void {
		reply = (url) =>
			url.includes('/downloads/links')
				? new Response(JSON.stringify(body), { status: 201 })
				: new Response(JSON.stringify({ downloads: [], total: 0 }), { status: 200 });
	}

	it('queues them all and says nothing was wrong', async () => {
		answering({ queued: 3, refused: [], duplicates: 0, left_over: [] });
		const queue = new DownloadQueue();
		const answer = await queue.submitMany(['https://a/1', 'https://a/2', 'https://a/3']);
		expect(answer?.queued).toBe(3);
		expect(queue.submitError).toBeUndefined();
		expect(calls.some((one) => one.url.includes('/downloads/links'))).toBe(true);
	});

	it('names the lines that were not links rather than losing the paste', async () => {
		// The behaviour that decides whether anybody uses this instead of pasting one at a time. A
		// list copied out of a page has a stray line in it, and "2 were refused" tells nobody which.
		answering({
			queued: 2,
			refused: [{ url: 'not a link', reason: 'That line has no website in it.' }],
			duplicates: 0,
			left_over: []
		});
		const queue = new DownloadQueue();
		await queue.submitMany(['https://a/1', 'not a link', 'https://a/2']);
		expect(queue.submitError).toContain('not a link');
		expect(queue.submitError).toContain('2 queued');
	});

	it('says what it silently dropped as well as what it refused', async () => {
		// A repeat and an overflow both mean a link somebody pasted did not become a download. Said
		// nowhere, the count on screen simply does not match the paste and there is nothing to read.
		answering({ queued: 2, refused: [], duplicates: 1, left_over: ['https://a/9'] });
		const queue = new DownloadQueue();
		await queue.submitMany(['https://a/1', 'https://a/1', 'https://a/2', 'https://a/9']);
		expect(queue.submitError).toContain('2 queued');
		expect(queue.submitError).toContain('already in the list');
		expect(queue.submitError).toContain("1 didn't fit in one paste");
	});

	it('says nothing at all when the whole paste went in', async () => {
		answering({ queued: 2, refused: [], duplicates: 0, left_over: [] });
		const queue = new DownloadQueue();
		await queue.submitMany(['https://a/1', 'https://a/2']);
		expect(queue.submitError).toBeUndefined();
	});
});

/* The chips, the filtering and the verbs a row can take.
 *
 * ## Why the counts are of the QUEUE and not of the search
 *
 * A chip whose count changed as somebody typed would be answering "how many failures match this
 * search", and the question a chip answers is "how many failures are there". The list under it is
 * what the search filters. The two are tested apart for that reason.
 */
describe('what the state tabs show', () => {
	function mixed(): DownloadItem[] {
		return [
			item({ id: 'a', status: 'running' }),
			item({ id: 'b', status: 'queued' }),
			item({ id: 'c', status: 'blocked' }),
			item({ id: 'd', status: 'failed' }),
			item({ id: 'e', status: 'done' }),
			item({ id: 'f', status: 'canceled' })
		];
	}

	async function loaded(): Promise<DownloadQueue> {
		servePage(mixed());
		const queue = new DownloadQueue();
		await queue.refresh();
		return queue;
	}

	it('counts each part of the queue from the rows until the server counts it', async () => {
		const queue = await loaded();
		expect(queue.tabs).toEqual([
			{ id: 'all', label: 'All', count: 6 },
			{ id: 'active', label: 'Active', count: 3 },
			{ id: 'needs', label: 'Needs you', count: 2 },
			{ id: 'done', label: 'Done', count: 1 },
			{ id: 'failed', label: 'Failed', count: 1 }
		]);
	});

	/* The page holds fifty and the queue holds hundreds: a tab counting its page would say a few
	   dozen over hundreds. */
	it('counts the WHOLE queue inside the chosen Site, not the page', async () => {
		reply = () =>
			new Response(
				JSON.stringify({
					downloads: [item({ id: 'e', status: 'done' })],
					total: 700,
					matched: 600,
					counts: { done: 600, failed: 40, blocked: 60 },
					sites: [{ name: 'YouTube', count: 12 }],
					summary: { running: 0, queued: 0, bytes_per_second: 0, by_state: { done: 600 } }
				}),
				{ status: 200 }
			);
		const queue = new DownloadQueue();
		await queue.refresh();
		expect(queue.tabs.map((tab) => [tab.id, tab.count])).toEqual([
			['all', 700],
			['active', 60],
			['needs', 100],
			['done', 600],
			['failed', 40]
		]);
		expect(queue.matched).toBe(600);
		expect(queue.inSites).toBe(700);
	});

	/* With a Site left out of the filter, the heading must not say the whole queue while All says
	   the count inside the chosen Sites: both figures read the one count inside the chosen Sites. */
	it('gives the heading the All tab count inside the chosen Sites, not the whole queue', async () => {
		reply = () =>
			new Response(
				JSON.stringify({
					downloads: [item({ id: 'e', status: 'done' })],
					total: 900,
					matched: 800,
					counts: { done: 780, failed: 20 },
					sites: [{ name: 'YouTube', count: 11 }],
					summary: { running: 0, queued: 0, bytes_per_second: 0, by_state: { done: 790 } }
				}),
				{ status: 200 }
			);
		const queue = new DownloadQueue();
		await queue.refresh();
		expect(queue.total).toBe(900);
		expect(queue.inSites).toBe(800);
		expect(queue.inSites).toBe(queue.tabs[0].count);
	});

	/* Every filter is a parameter of the one read. Filtering the fifty rows on hand would answer
	   a question about a page under a label asking about the queue. */
	it('asks the server for the tab, the Site, the search and the order, from the first page', async () => {
		const queue = await loaded();
		await queue.turn(100);
		await queue.narrow({
			filter: 'needs',
			siteNames: ['YouTube'],
			search: ' clip ',
			sort: 'largest'
		});
		const asked = new URL(calls.at(-1)?.url ?? '', 'http://x');
		expect(asked.pathname).toBe('/api/downloads');
		expect(Object.fromEntries(asked.searchParams)).toEqual({
			limit: '50',
			offset: '0',
			show: 'needs',
			site: 'YouTube',
			q: 'clip',
			sort: 'largest'
		});
	});

	/* The filter panel's Site column can tick two, and the server reads a repeated `site` as either
	   of them: the same "or" every entity wall's column is. None ticked sends no `site` at all. */
	it('asks for every ticked Site as a repeated parameter, and none for every Site', async () => {
		const queue = await loaded();
		await queue.narrow({ siteNames: ['YouTube', 'TikTok'] });
		let asked = new URL(calls.at(-1)?.url ?? '', 'http://x');
		expect(asked.searchParams.getAll('site')).toEqual(['YouTube', 'TikTok']);
		await queue.narrow({ siteNames: [] });
		asked = new URL(calls.at(-1)?.url ?? '', 'http://x');
		expect(asked.searchParams.has('site')).toBe(false);
	});

	it('turns the page by offset, keeping what is narrowed', async () => {
		const queue = await loaded();
		await queue.narrow({ filter: 'done' });
		await queue.turn(50);
		const asked = new URL(calls.at(-1)?.url ?? '', 'http://x');
		expect(asked.searchParams.get('offset')).toBe('50');
		expect(asked.searchParams.get('show')).toBe('done');
	});

	it('keeps only the newest answer when two reads cross', async () => {
		const answers: ((value: Response) => void)[] = [];
		vi.stubGlobal(
			'fetch',
			vi.fn(
				(input: URL | string) =>
					new Promise<Response>((resolve) => {
						calls.push({ url: String(input), method: 'GET' });
						answers.push(resolve);
					})
			)
		);
		const queue = new DownloadQueue();
		const first = queue.narrow({ filter: 'done' });
		const second = queue.narrow({ filter: 'failed' });
		const page = (id: string) =>
			new Response(JSON.stringify({ downloads: [item({ id })], total: 1 }), { status: 200 });
		answers[1](page('failed-row'));
		answers[0](page('done-row'));
		await Promise.all([first, second]);
		expect(queue.items.map((one) => one.id)).toEqual(['failed-row']);
	});

	it('takes a settled row off the list', async () => {
		servePage([item({ id: 'a', status: 'done' })]);
		const queue = new DownloadQueue();
		await queue.remove('a');
		expect(calls.some((one) => one.url.includes('/downloads/a/remove'))).toBe(true);
	});

	it('counts a held row under Active rather than leaving it out of every tab', async () => {
		servePage([item({ id: 'a', status: 'paused' })]);
		const queue = new DownloadQueue();
		await queue.refresh();
		expect(queue.tabs.find((tab) => tab.id === 'active')?.count).toBe(1);
	});
});

/* Holding one where it is, letting it go again, and the way back from Remove.
 *
 * The addresses are asserted whole rather than by `includes`: `/pauses` contains `/pause`, so a
 * substring check passes against an address the server does not answer. */
describe('holding a download, and putting a removed one back', () => {
	function pathsPosted(): string[] {
		return calls.filter((one) => one.method === 'POST').map((one) => new URL(one.url).pathname);
	}

	it.each([
		['pause', 'paused'],
		['resume', 'queued']
	] as const)('%s asks the one address and re-reads the row', async (verb, becomes) => {
		reply = (url, init) =>
			init.method === 'POST'
				? new Response(null, { status: 204 })
				: new Response(JSON.stringify({ downloads: [item({ status: becomes })], total: 1 }), {
						status: 200
					});
		const queue = new DownloadQueue();
		await queue[verb]('d1');
		expect(pathsPosted()).toContain(`/api/downloads/d1/${verb}`);
		expect(queue.items[0].status).toBe(becomes);
	});

	/* The toast offers an Undo, and this asserts that the route is actually called rather than
	   the button merely being drawn. */
	it('offers one press back, and that press restores exactly what went', async () => {
		reply = (url, init) =>
			init.method === 'POST'
				? new Response(null, { status: 204 })
				: new Response(JSON.stringify({ downloads: [], total: 0 }), { status: 200 });
		const queue = new DownloadQueue();
		await queue.removeRows(['a', 'b']);

		expect(pathsPosted()).toEqual(['/api/downloads/a/remove', '/api/downloads/b/remove']);
		const said = toasts.items.at(-1);
		expect(said?.message).toBe('2 removed from the list');
		expect(said?.action?.label).toBe('Undo');

		said?.action?.run();
		await vi.waitFor(() => {
			expect(pathsPosted()).toContain('/api/downloads/b/restore');
		});
		expect(pathsPosted()).toContain('/api/downloads/a/restore');
	});

	it('says it in the singular for one row', async () => {
		reply = (url, init) =>
			init.method === 'POST'
				? new Response(null, { status: 204 })
				: new Response(JSON.stringify({ downloads: [], total: 0 }), { status: 200 });
		const queue = new DownloadQueue();
		await queue.removeRows(['a']);
		expect(toasts.items.at(-1)?.message).toBe('Removed from the list');
	});
});

/* A row waiting on a person, and the verbs a row can take.
 *
 * These are here rather than on the row because the selection bar reads them too, and its whole
 * rule is that it offers only what EVERY picked row can take. Two copies of these would agree until
 * the day one of them learned a new state. */
describe('what a row needs and what it can take', () => {
	it('counts a blocked row and a failed one as needing somebody', () => {
		expect(needsYou(item({ status: 'blocked' }))).toBe(true);
		expect(needsYou(item({ status: 'failed' }))).toBe(true);
	});

	/* Somebody already decided the cancelled one, and the same bytes would arrive and be
	   quarantined again, so neither is a thing waiting for a person. */
	it('does not count a decision already taken', () => {
		expect(needsYou(item({ status: 'canceled' }))).toBe(false);
		expect(needsYou(item({ status: 'quarantined' }))).toBe(false);
		expect(needsYou(item({ status: 'done' }))).toBe(false);
	});

	it('offers each verb only where it means something', () => {
		expect(canGoFirst(item({ status: 'queued' }))).toBe(true);
		expect(canGoFirst(item({ status: 'running' }))).toBe(false);
		expect(canCancel(item({ status: 'blocked' }))).toBe(true);
		expect(canCancel(item({ status: 'done' }))).toBe(false);
		expect(canRetry(item({ status: 'failed' }))).toBe(true);
		expect(canRetry(item({ status: 'done' }))).toBe(false);
		expect(canRemove(item({ status: 'done' }))).toBe(true);
		expect(canRemove(item({ status: 'running' }))).toBe(false);
	});

	/* Both halves of each, because the bar's rule is that it offers only what EVERY picked row can
	   take: a predicate that is true too widely puts a verb on the bar that does nothing to half the
	   pick, and one that is false too widely takes the verb away from the rows that want it. */
	it('holds only what is going to move, and lets go only what is held', () => {
		expect(canPause(item({ status: 'running' }))).toBe(true);
		expect(canPause(item({ status: 'queued' }))).toBe(true);
		// Already stopped, and waiting on a person rather than on the machine: pausing it would
		// change nothing and would take away the badge that says what it is waiting for.
		expect(canPause(item({ status: 'blocked' }))).toBe(false);
		expect(canPause(item({ status: 'paused' }))).toBe(false);
		for (const status of ['done', 'failed', 'canceled', 'skipped', 'duplicate', 'quarantined']) {
			expect(canPause(item({ status })), `pause offered on ${status}`).toBe(false);
		}

		expect(canResume(item({ status: 'paused' }))).toBe(true);
		for (const status of ['queued', 'running', 'blocked', 'done', 'failed', 'canceled']) {
			expect(canResume(item({ status })), `resume offered on ${status}`).toBe(false);
		}
	});

	/* A held row is live work, not history, and the three answers below all come off that one fact.
	   Taking it off the list would leave a job holding a staging folder with nothing on screen
	   saying so, which is the same reason a running row cannot be removed. */
	it('counts a held row as live rather than as history', () => {
		expect(canCancel(item({ status: 'paused' }))).toBe(true);
		expect(canRemove(item({ status: 'paused' }))).toBe(false);
		expect(needsYou(item({ status: 'paused' }))).toBe(false);
	});
});

/* One splitter, because the box counts the lines to label its button and the page counts them to
   decide whether to send one link or a list. Two copies is two answers to "how many links". */
describe('reading a paste', () => {
	it('drops blank lines and trims each address', () => {
		expect(lines('  https://a/1 \n\n\r\nhttps://a/2\n')).toEqual(['https://a/1', 'https://a/2']);
	});

	it('never splits one address into two', () => {
		expect(lines('https://a/one%20two')).toEqual(['https://a/one%20two']);
	});
});

/* "Saving to" only while it is saving. One case per state the server shows, so a state added
   later that nobody thought about lands on "Save to" by the rule rather than by whichever branch
   happened to catch it. */
describe('what a download calls its folder', () => {
	it.each([
		['running', 'Saving to'],
		['done', 'Saved to'],
		['duplicate', 'Saved to'],
		['queued', 'Save to'],
		['blocked', 'Save to'],
		['paused', 'Save to'],
		['failed', 'Save to'],
		['canceled', 'Save to'],
		['skipped', 'Save to'],
		['quarantined', 'Save to']
	])('a %s download says %s', (status, word) => {
		expect(destinationWord(status)).toBe(word);
	});
});

/* The order menu is the top bar's, and every other wall fills it from the one shared list: the
 * words, the keys and (looked up by key) the glyphs. A list of this screen's own would say
 * different words under no glyph.
 */
describe('the orders the queue offers', () => {
	it('are the shared entries, word for word, and the Site order declared beside them', () => {
		const everyShared = [...SORT_OPTIONS, SITE_ORDER];
		for (const order of SORTS) {
			const same = everyShared.find((one) => one.value === order.value);
			expect(same, `no shared order is spelled ${order.value}`).toBeDefined();
			expect(order.label).toBe(same!.label);
		}
		expect(SORTS.map((one) => one.value)).toEqual([
			'newest',
			'oldest',
			'name_az',
			'name_za',
			'largest',
			'smallest',
			'site'
		]);
	});

	it('each wear the glyph every other wall draws beside that order', () => {
		for (const order of SORTS) {
			expect(sortIcon(order.value), `${order.value} has no mark`).toBeTruthy();
		}
		expect(sortIcon('site')).toBe('public');
	});

	it('reads an address kept from before the shared keys as the order it meant', () => {
		expect(queueSortFrom('name')).toBe('name_az');
		expect(queueSortFrom('size')).toBe('largest');
		expect(queueSortFrom('smallest')).toBe('smallest');
		expect(queueSortFrom('nonsense')).toBe('newest');
		expect(queueSortFrom(null)).toBe('newest');
	});
});
