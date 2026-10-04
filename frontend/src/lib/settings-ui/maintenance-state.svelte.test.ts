import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	Maintenance,
	describeCloseness,
	formatBytes,
	formatDimensions,
	formatDuration,
	keyOf,
	type Group,
	type GroupFile
} from './maintenance-state.svelte';
import { CardPaging } from '$lib/grid/cards.svelte';

/** A page of each half read the way its panel reads it: through that panel's paging. */
async function readGroups(view: Maintenance, paging = new CardPaging(24), needsYou = false) {
	return await view.fillGroups(paging, needsYou);
}

async function readCopies(view: Maintenance, paging = new CardPaging(24)) {
	return await view.fillReclaim(paging);
}

/** Both halves, one after the other, as a screen showing both would ask. */
async function readBoth(view: Maintenance) {
	await readGroups(view);
	await readCopies(view);
}

/* Duplicate-finding, as the two cards see it.
 *
 * The interesting behaviour here is what the store refuses to do quietly. A queue that failed to
 * load looks exactly like a queue with nothing in it, and one of those means "nothing to review"
 * while the other means nothing at all. Same for reclaim, where an empty list reads as "no wasted
 * space", which is a reassuring thing to say when it is not true.
 *
 * The keeper RULE is not tested here and is not here to test: it is the server's, because a rule
 * that decides a whole library has to be applied where the library is. What
 * is left on this side is which file is MARKED (the server's mark, or somebody's override of it),
 * and that is what the press sends.
 */

function file(id: string, over: Partial<GroupFile> = {}): GroupFile {
	return {
		id,
		media_type: 'video',
		concealed: false,
		original_filename: `${id}.mp4`,
		where: `Videos/${id}.mp4`,
		size_bytes: 1000,
		width: 1920,
		height: 1080,
		duration_ms: 1000,
		container: 'mp4',
		added_at: 100,
		art: null,
		...over
	};
}

function group(over: Partial<Group> = {}): Group {
	return {
		files: [file('asset-a'), file('asset-b')],
		method: 'phash',
		distance: 1,
		keeper: 'asset-a',
		too_big: false,
		...over
	};
}

/** How alike, as `describeCloseness` reads it: the method's own scale, and a figure on it. */
function alike(method: string, distance: number) {
	return { method, distance };
}

/** The shape `/dedup/groups` answers with, so a test says only what it is about. */
function queue(over: Record<string, unknown> = {}) {
	return {
		groups: [group()],
		total: 1,
		offset: 0,
		needs_you: 0,
		matching: 1,
		pending_total: 1,
		concealed: 0,
		awaiting_fingerprint: 0,
		level: 'medium',
		max_duration_gap_ms: null,
		rule: 'higher_res',
		rules: [{ key: 'higher_res', label: 'Higher resolution' }],
		...over
	};
}

const fetchMock = vi.fn();

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	vi.stubGlobal('window', { location: { origin: 'http://sift.test' } });
	fetchMock.mockReset();
});

afterEach(() => {
	vi.unstubAllGlobals();
});

function answers(...responses: Array<{ ok: boolean; status?: number; body?: unknown }>) {
	for (const response of responses) {
		fetchMock.mockResolvedValueOnce({
			ok: response.ok,
			status: response.status ?? (response.ok ? 200 : 409),
			json: async () => response.body ?? {}
		});
	}
}

describe('loading both lists', () => {
	it('takes the queue, the copies, and what they are costing', async () => {
		answers(
			{ ok: true, body: queue() },
			{
				ok: true,
				body: {
					assets: [
						{
							asset_id: 'asset-c',
							media_type: 'video',
							copies: [
								{
									location_id: 'loc-1',
									root_id: 'root',
									rel_path: 'a.mp4',
									filename: 'a.mp4',
									size_bytes: 1000
								},
								{
									location_id: 'loc-2',
									root_id: 'root',
									rel_path: 'b.mp4',
									filename: 'b.mp4',
									size_bytes: 1000
								}
							],
							reclaimable_bytes: 1000
						}
					],
					total: 1,
					total_reclaimable_bytes: 1000,
					concealed: 0
				}
			}
		);

		const view = new Maintenance();
		await readBoth(view);

		expect(view.groups).toHaveLength(1);
		expect(view.redundancies).toHaveLength(1);
		expect(view.totalRedundant).toBe(1);
		expect(view.totalReclaimable).toBe(1000);
		expect(view.problem).toBeNull();
	});

	it('does not claim it loaded when it did not', async () => {
		/* The distinction the screen leans on. `loaded` false is what stops it going on to say
		 * "nothing to review" and "no file is stored twice": two reassuring statements about a
		 * library that nobody managed to read. */
		answers({ ok: false, status: 403 });

		const view = new Maintenance();
		await readGroups(view);

		expect(view.loaded).toBe(false);
		expect(view.loading).toBe(false);
		expect(view.problem).not.toBeNull();
	});

	it('marks itself loaded once it really has', async () => {
		answers(
			{ ok: true, body: queue({ groups: [], total: 0 }) },
			{ ok: true, body: { assets: [], total_reclaimable_bytes: 0 } }
		);

		const view = new Maintenance();
		await readBoth(view);

		expect(view.loaded).toBe(true);
		expect(view.problem).toBeNull();
	});

	it('says so rather than showing an empty queue when it could not read one', async () => {
		/* Refused, not empty. "Nothing to review" and "we could not ask" must not look the same:
		 * one of them is a reason to stop worrying about duplicates. */
		answers({ ok: false, status: 403 });

		const view = new Maintenance();
		await readGroups(view);

		expect(view.problem).not.toBeNull();
		expect(view.groups).toEqual([]);
	});

	it('reads a page of groups in ONE request, facts and all', async () => {
		/* Asking for every file on its own would make a page of a hundred pairs two hundred
		 * requests, each a recursive permission walk on a self-hosted box. The facts arriving with
		 * the group is the whole reason the page is one request, so the count is what is asserted
		 * rather than merely the contents. */
		answers({ ok: true, body: queue() });

		const view = new Maintenance();
		await readGroups(view);

		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(view.groups[0].files[0].where).toBe('Videos/asset-a.mp4');
		expect(view.summary.rule).toBe('higher_res');
		expect(view.summary.rules).toHaveLength(1);
	});

	it('asks for the page and the narrowing it was given', async () => {
		answers({ ok: true, body: queue({ offset: 48, total: 60 }) });

		const view = new Maintenance();
		const paging = new CardPaging(24);
		paging.offset = 48;
		await readGroups(view, paging, true);

		const [url] = fetchMock.mock.calls[0];
		expect(String(url)).toContain('offset=48');
		expect(String(url)).toContain('limit=24');
		expect(String(url)).toContain('needs_you=true');
	});

	it('asks for the page by the group the address names, with where it was beside it', async () => {
		/* Coming back to the queue: the page is asked for by its first group's own name, and the
		   answer says where that group now is, which is where the paging lands. */
		answers({ ok: true, body: queue({ offset: 48, total: 60 }) });

		const view = new Maintenance();
		const paging = new CardPaging(24);
		paging.arrive({ from: 'phash:asset-a', near: 48 });
		await readGroups(view, paging);

		const [url] = fetchMock.mock.calls[0];
		expect(String(url)).toContain('from=phash%3Aasset-a');
		expect(String(url)).toContain('near=48');
		expect(String(url)).not.toContain('offset=');
		expect(paging.offset).toBe(48);
	});
});

describe('marking a keeper, and pressing a page', () => {
	it("takes the server's mark, and an override wins over it", () => {
		const view = new Maintenance();
		view.groups = [group()];

		expect(view.keeperOf(view.groups[0])).toBe('asset-a');

		view.choose(view.groups[0], 'asset-b');

		expect(view.keeperOf(view.groups[0])).toBe('asset-b');
	});

	it('ignores an override naming a file that is not in the group', () => {
		/* One reader for the tick on screen and the file the press sends, so the two can never be
		   two different answers. A stale override (the page reloaded and the group changed shape)
		   must fall back to the mark rather than sending a file that is not there. */
		const view = new Maintenance();
		view.groups = [group()];
		view.chosen = { 'asset-a': 'asset-gone' };

		expect(view.keeperOf(view.groups[0])).toBe('asset-a');
	});

	it('tells two groups apart when they share their smallest file', () => {
		/* A GIF is fingerprinted by `videohash` and by `video_phash`, so the same two files are a
		   group under each, and the two groups have the same smallest file. Keyed on the file
		   alone, an override on one would mark the other, and the screen's `{#each}` would throw on
		   the duplicate key and draw nothing at all. */
		const view = new Maintenance();
		const byFrames = group({ method: 'videohash', keeper: 'asset-a' });
		const byVideo = group({ method: 'video_phash', keeper: 'asset-a' });
		view.groups = [byFrames, byVideo];

		view.choose(byFrames, 'asset-b');

		expect(view.keeperOf(byFrames)).toBe('asset-b');
		expect(view.keeperOf(byVideo)).toBe('asset-a');
		expect(keyOf(byFrames)).not.toBe(keyOf(byVideo));
	});

	it('leaves a group the rule could not settle unmarked', () => {
		const view = new Maintenance();
		view.groups = [group({ keeper: null })];

		expect(view.keeperOf(view.groups[0])).toBeNull();
		expect(view.marked).toEqual([]);
	});

	it('never counts a chain as marked, however it was answered', () => {
		/* A component past the cap is a chain of pairs whose ends may look nothing alike. It is
		   never pre-marked on the server, and it must not become pressable by being overridden. */
		const view = new Maintenance();
		view.groups = [group({ too_big: true, keeper: null })];
		view.choose(view.groups[0], 'asset-b');

		expect(view.marked).toEqual([]);
	});

	it('says what confirming would delete: every file of every marked group but its keeper', () => {
		const view = new Maintenance();
		view.groups = [
			group(),
			group({ files: [file('c'), file('d'), file('e')], keeper: 'c' }),
			group({ files: [file('f'), file('g')], keeper: null })
		];

		expect(view.wouldDelete.map((one) => one.id)).toEqual(['asset-b', 'd', 'e']);
	});

	it('sends one request for the whole page, naming each group and its keeper', async () => {
		/* One request rather than one per group: a page is one press and one decision, and
		   twenty-four requests would leave a half-settled page behind if the connection dropped in
		   the middle. */
		answers(
			{ ok: true, body: { settled: 1, removed: 1, refused: 0, unknown: 0 } },
			{
				ok: true,
				body: queue({ groups: [], total: 0 })
			}
		);
		const view = new Maintenance();
		view.groups = [group()];

		const outcome = await view.confirmMarked(view.marked);

		expect(outcome).toEqual({ settled: 1, removed: 1, refused: 0, unknown: 0 });
		const [url, options] = fetchMock.mock.calls[0];
		expect(String(url)).toContain('/dedup/groups/confirm');
		expect(JSON.parse(options.body)).toEqual({
			groups: [{ ids: ['asset-a', 'asset-b'], keep: 'asset-a' }]
		});
	});

	it("reads nothing back itself: which page comes next is the panel's paging to ask", async () => {
		/* Read back from the front here, by offset, beside a panel holding its own offset, the two
		   would disagree about where the page was, and a single group dismissed on page three would
		   send the list to page one under a pager still saying three. */
		answers({ ok: true, body: { settled: 1, removed: 1, refused: 0, unknown: 0 } });
		const view = new Maintenance();
		view.groups = [group()];

		await view.confirmMarked(view.marked);

		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it('dismissing names the group and no keeper, because nothing is deleted', async () => {
		answers(
			{ ok: true, body: { settled: 1, removed: 0, refused: 0, unknown: 0 } },
			{
				ok: true,
				body: queue({ groups: [], total: 0 })
			}
		);
		const view = new Maintenance();
		const one = group({ keeper: null });
		view.groups = [one];

		await view.dismissGroups([one]);

		const [url, options] = fetchMock.mock.calls[0];
		expect(String(url)).toContain('/dedup/groups/dismiss');
		expect(JSON.parse(options.body).groups[0].keep).toBeNull();
	});

	it('asks for nothing at all when nothing is marked', async () => {
		const view = new Maintenance();

		expect(await view.confirmMarked([])).toEqual({
			settled: 0,
			removed: 0,
			refused: 0,
			unknown: 0
		});
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it('hands back a refusal rather than pretending it worked', async () => {
		answers({ ok: false, status: 403 });
		const view = new Maintenance();
		view.groups = [group()];

		const outcome = await view.confirmMarked(view.marked);

		expect(outcome.problem).toBeTruthy();
		expect(outcome.settled).toBe(0);
	});

	it('forgets the overrides when a page is replaced', async () => {
		/* A mark belongs to the group in front of somebody. Carrying one across a page turn would
		   put a keeper on a group they have not seen, which the next press would act on. */
		answers({ ok: true, body: queue() });
		const view = new Maintenance();
		view.chosen = { 'asset-a': 'asset-b' };

		await readGroups(view);

		expect(view.chosen).toEqual({});
	});
});

describe('letting go of one copy', () => {
	it('names the copy, not the file', async () => {
		/* The whole point: the asset survives in its other places. A request that named only the
		 * asset would be asking for the file itself to go. */
		answers({ ok: true, body: {} });

		const view = new Maintenance();
		await view.release('asset-c', 'loc-2');

		const [url, options] = fetchMock.mock.calls[0];
		expect(String(url)).toContain('/reclaim/asset-c/release');
		expect(JSON.parse(options.body)).toEqual({ location_id: 'loc-2' });
	});
});

describe('how the numbers are written', () => {
	it('scales bytes to something a person reads', () => {
		expect(formatBytes(512)).toBe('512 B');
		expect(formatBytes(1500)).toBe('1.5 kB');
		expect(formatBytes(2_400_000)).toBe('2.4 MB');
		expect(formatBytes(15_000_000_000)).toBe('15 GB');
	});

	it('says unknown rather than nothing when a size was never recorded', () => {
		/* Zero would be a claim. This screen exists to state a number accurately, so the absence of
		 * one is said out loud. */
		expect(formatBytes(null)).toBe('unknown');
	});

	it('writes a length and a shape only when there is one', () => {
		expect(formatDuration(125_000)).toBe('2:05');
		expect(formatDuration(null)).toBe('');
		expect(formatDimensions(1920, 1080)).toBe('1920 x 1080');
		expect(formatDimensions(null, 1080)).toBe('');
	});
});

describe('saying how alike a group is', () => {
	it('uses ONE vocabulary, whatever measured it', () => {
		/* A phrase per scale (a photograph "Nearly identical", a video "Almost certainly the same",
		   a GIF "26 of 30 frames match") has no order anybody can see between them, and some are a
		   sentence about confidence rather than about likeness. A person reading a page of these is
		   asking one question, so the answer has to mean the same thing on every card. */
		const rungs = ['Identical', 'Almost identical', 'Very similar', 'Similar'];
		for (const method of ['phash', 'video_phash', 'videohash']) {
			for (let distance = 0; distance < 12; distance += 1) {
				expect(rungs).toContain(describeCloseness(alike(method, distance)));
			}
		}
	});

	it('puts zero at the top rung on every scale', () => {
		expect(describeCloseness(alike('phash', 0))).toBe('Identical');
		expect(describeCloseness(alike('video_phash', 0))).toBe('Identical');
		expect(describeCloseness(alike('videohash', 0))).toBe('Identical');
	});

	it('turns over exactly where the closeness dial does', () => {
		/* The dial has four positions and the server gives each a figure per fingerprint: 0,
		   2, 4 and the widest. The rungs turn over at the same figures, so a queue read at High
		   shows nothing worse than "Almost identical" and the dial's own words said so before it
		   moved. That is what makes them trustworthy rather than decorative.

		   !! THREE IS NOT DECORATION. With every figure a BOUNDARY and none between two, moving
		   the middle edge from 2 to 3 would change the answer for three alone and every other
		   assertion here would still pass. A rung's EDGES being right is not the same claim as
		   its WIDTH being right, and only the second one is what somebody reading the queue
		   relies on. */
		for (const method of ['phash', 'video_phash', 'videohash']) {
			expect(describeCloseness(alike(method, 0))).toBe('Identical');
			expect(describeCloseness(alike(method, 2))).toBe('Almost identical');
			expect(describeCloseness(alike(method, 3))).toBe('Very similar');
			expect(describeCloseness(alike(method, 4))).toBe('Very similar');
			expect(describeCloseness(alike(method, 5))).toBe('Similar');
		}
	});

	it('never runs out of rungs, however far apart the figure is', () => {
		expect(describeCloseness(alike('phash', 63))).toBe('Similar');
		expect(describeCloseness(alike('videohash', 30))).toBe('Similar');
	});
});

/* The reclaim list is a PAGE, and the number over it is the whole library.
 *
 * The route does not hand back every redundant asset in the library on every read: thousands of
 * them, with every path under each, polled on a timer by a mark that only wants to know whether
 * the count has grown.
 *
 * The two must stay apart. A screen that decided "nothing to reclaim" from the length of a page
 * would say it over a library with thousands of duplicates in it the moment the page
 * came back empty for any other reason.
 */

function redundancy(id: string) {
	return {
		asset_id: id,
		media_type: 'video',
		copies: [
			{
				location_id: `${id}-1`,
				root_id: 'root',
				rel_path: 'a.mp4',
				filename: 'a.mp4',
				size_bytes: 10
			},
			{
				location_id: `${id}-2`,
				root_id: 'root',
				rel_path: 'b.mp4',
				filename: 'b.mp4',
				size_bytes: 10
			}
		],
		reclaimable_bytes: 10
	};
}

describe('a page of the copies, and the whole library over it', () => {
	it('keeps how many there are apart from how many are drawn', async () => {
		answers({
			ok: true,
			body: {
				assets: [redundancy('a-1'), redundancy('a-2')],
				total: 3566,
				total_reclaimable_bytes: 99_000,
				concealed: 0
			}
		});

		const view = new Maintenance();
		await readCopies(view);

		expect(view.redundancies).toHaveLength(2);
		expect(view.totalRedundant).toBe(3566);
		expect(view.totalReclaimable).toBe(99_000);
	});

	it('turns a page rather than appending, so both tabs move the same way', async () => {
		/* One way to move through one job: a pager, as the near-duplicate tab beside it has,
		   not a *Show more* on one tab and a pager on the other. */
		answers(
			{ ok: true, body: { assets: [redundancy('a-1')], total: 2, total_reclaimable_bytes: 20 } },
			{
				ok: true,
				body: { assets: [redundancy('a-2')], total: 2, total_reclaimable_bytes: 20, offset: 1 }
			}
		);
		const view = new Maintenance();
		const paging = new CardPaging(1);
		await readCopies(view, paging);

		paging.step(1, view.totalRedundant);
		await readCopies(view, paging);

		expect(view.redundancies.map((one) => one.asset_id)).toEqual(['a-2']);
		expect(view.reclaimOffset).toBe(1);
		expect(String(fetchMock.mock.calls[1][0])).toContain('offset=1');
	});

	it('stays on the page it was on after letting a copy go', async () => {
		/* Unlike a confirm over a whole page of groups, which empties the page it was on, releasing
		   one copy leaves the rest of the page where it was, so going back to the start would
		   throw away the position of somebody working down a long list. The panel reads it back
		   through the same paging, which is still where it was. */
		answers(
			{
				ok: true,
				body: { assets: [redundancy('a-2')], total: 30, total_reclaimable_bytes: 90, offset: 24 }
			},
			{ ok: true, body: {} },
			{
				ok: true,
				body: { assets: [redundancy('a-3')], total: 29, total_reclaimable_bytes: 80, offset: 24 }
			}
		);
		const view = new Maintenance();
		const paging = new CardPaging(24);
		paging.offset = 24;
		await readCopies(view, paging);

		await view.release('a-2', 'a-2-2');
		await readCopies(view, paging);

		expect(String(fetchMock.mock.calls[2][0])).toContain('offset=24');
		expect(view.reclaimOffset).toBe(24);
	});

	it('says how many of the page the vault is hiding', async () => {
		/* An empty page and an empty library draw the same screen. This is what lets the sentence
		   over the list say which one it is looking at. */
		answers({
			ok: true,
			body: { assets: [], total: 4, total_reclaimable_bytes: 40, concealed: 4 }
		});

		const view = new Maintenance();
		await readCopies(view);

		expect(view.concealed).toBe(4);
		expect(view.totalRedundant).toBe(4);
	});

	it('asks for nothing but the release when letting a copy go', async () => {
		/* The review queue is a different card, and the page of copies is read back by the panel
		   through its paging. A read here would be a second copy of "which page", beside the
		   paging's. */
		answers(
			{ ok: true, body: { assets: [redundancy('a-1')], total: 1, total_reclaimable_bytes: 10 } },
			{ ok: true, body: {} }
		);
		const view = new Maintenance();
		await readCopies(view);

		await view.release('a-1', 'a-1-2');

		expect(fetchMock).toHaveBeenCalledTimes(2);
		expect(String(fetchMock.mock.calls[1][0])).toContain('/reclaim/a-1/release');
	});
});
