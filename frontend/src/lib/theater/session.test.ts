/*
 * The record of sitting in front of a wall: the session's two reports, and every cell's piece.
 *
 * What is asserted is what the client SAYS: the server keeps it (`theater/sessions.py`, and
 * `plays` for the cells), and its own tests say what it does with it.
 */

import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { api } from '$lib/api/client';
import { BEAT_EVERY_MS, TheaterSession } from './session';
import { countView, showing } from './wall.svelte';
import { Cell } from './cell.svelte';

let clock = 0;
let post: MockInstance<(path: string, options?: unknown) => Promise<unknown>>;

beforeEach(() => {
	clock = 1_000;
	vi.spyOn(performance, 'now').mockImplementation(() => clock);
	vi.spyOn(api, 'get').mockImplementation(async () => ({ items: [], total: 0 }));
	post = vi.spyOn(api, 'post').mockImplementation(async () => ({})) as unknown as typeof post;
});

afterEach(() => vi.restoreAllMocks());

async function settle() {
	for (let turn = 0; turn < 4; turn++) await Promise.resolve();
}

function sent(): Array<{ path: string; body: Record<string, unknown>; keepalive?: boolean }> {
	return reports().map((call) => {
		const options = call[1] as { body: Record<string, unknown>; keepalive?: boolean };
		return { path: String(call[0]), body: options.body, keepalive: options.keepalive };
	});
}

/** What was said, without the beats an open wall sends beside it. */
function reports() {
	return post.mock.calls.filter((call) => call[0] !== '/theater/watching');
}

function beats(): number {
	return post.mock.calls.filter((call) => call[0] === '/theater/watching').length;
}

const FACTS = {
	layout: 'grid',
	cells: 4,
	arrangement: 'a-saved-wall',
	sources: ['', 'in:holiday', 'loops:any', 'tags:beach']
};

describe('a Theater session', () => {
	it('beats while it is open and on screen, never once it has closed', async () => {
		vi.useFakeTimers();
		const seen = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
		try {
			const session = new TheaterSession();
			session.open();
			expect(beats()).toBe(1);
			vi.advanceTimersByTime(BEAT_EVERY_MS);
			expect(beats()).toBe(2);
			seen.mockReturnValue('hidden');
			vi.advanceTimersByTime(BEAT_EVERY_MS);
			expect(beats()).toBe(2);
			seen.mockReturnValue('visible');
			session.close(FACTS);
			vi.advanceTimersByTime(3 * BEAT_EVERY_MS);
			expect(beats()).toBe(2);
		} finally {
			vi.useRealTimers();
		}
	});

	it('says it has opened, with nothing on it but the name', async () => {
		const session = new TheaterSession();

		session.open();
		await settle();

		expect(sent()).toEqual([
			{ path: `/theater/sessions/${session.id}`, body: {}, keepalive: undefined }
		]);
	});

	it('says what the wall was when it closes, with keepalive, and only once', async () => {
		const session = new TheaterSession();
		session.shown('one');
		session.shown('two');
		session.shown('one');
		clock += 90_000;

		session.close(FACTS);
		session.close(FACTS);
		await settle();

		expect(sent()).toHaveLength(1);
		const [{ body, keepalive }] = sent();
		expect(keepalive, 'a window closing on a running wall would lose the end').toBe(true);
		expect(body).toEqual({
			elapsed_ms: 90_000,
			ended: true,
			layout: 'grid',
			cells: 4,
			arrangement: 'a-saved-wall',
			sources: FACTS.sources,
			// Different files, not appearances: the same file twice is one.
			files: 2
		});
	});

	it('is recorded at the ceiling rather than refused when left open past it', async () => {
		/* A refusal on a keepalive nobody reads would lose the whole session. */
		const session = new TheaterSession();
		clock += 30 * 24 * 60 * 60 * 1000;

		session.close({ ...FACTS, sources: ['x'.repeat(5_000)] });
		await settle();

		const [{ body }] = sent();
		expect(body.elapsed_ms).toBe(7 * 24 * 60 * 60 * 1000);
		expect((body.sources as string[])[0]).toHaveLength(1_000);
	});

	it('begins when a wall is made and ends when the last thing drawing it lets go', async () => {
		const wall = showing.ensure();
		const name = wall.session?.id;
		expect(name, 'a wall was made with no session').toBeTruthy();

		showing.drop(false);
		await settle();

		const paths = sent().map((one) => one.path);
		expect(paths).toEqual([`/theater/sessions/${name}`, `/theater/sessions/${name}`]);
		expect(sent()[1].body.ended).toBe(true);
	});
});

describe('a cell reporting what it showed', () => {
	it('names the sitting, the session and the screen, and what was already reported', async () => {
		const sitting = new Cell().sittingWith('file-1');

		await countView(sitting, 4_000, 'an-evening');
		await countView(sitting, 2_000, 'an-evening');

		const [first, second] = sent();
		expect(first.path).toBe('/assets/file-1/view');
		expect(first.body).toMatchObject({
			watch_ms: 4_000,
			already_reported_ms: null,
			sitting: sitting.id,
			screen: 'theater',
			theater_session: 'an-evening',
			position_ms: null
		});
		// The second piece of the same sitting says the first went, so it is one view and one row.
		expect(second.body).toMatchObject({ watch_ms: 2_000, already_reported_ms: 4_000 });
		expect(first.keepalive).toBe(true);
	});

	it('carries what happened inside the piece: the speed, the filled screen, the passes', async () => {
		const sitting = new Cell().sittingWith('file-1');

		await countView(sitting, 4_000, 'an-evening', {
			speeds: { '2': 4_000 },
			fullscreen_ms: 1_000,
			completions: 2
		});

		expect(sent()[0].body).toMatchObject({
			screen: 'theater',
			speeds: { '2': 4_000 },
			fullscreen_ms: 1_000,
			completions: 2
		});
	});

	it('takes back what it claimed when the report did not land', async () => {
		const sitting = new Cell().sittingWith('file-1');
		post.mockRejectedValueOnce(new Error('offline'));

		await countView(sitting, 4_000, null);

		expect(
			sitting.reported,
			'a lost piece would make the next one claim a view it never earned'
		).toBe(null);
	});
});
