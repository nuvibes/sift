import { describe, expect, it, vi, afterEach } from 'vitest';
import { fetchHealth, serverBootId } from './health';

function answers(body: unknown, ok = true): void {
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => ({ ok, json: async () => body }) as unknown as Response)
	);
}

const ADMIN_ANSWER = {
	status: 'ok',
	loop: { worst_lag_seconds: 0.42, held_count: 3 },
	threads: { worst_wait_seconds: 1.5, full_count: 2, waiting_seconds: 0 },
	database: {
		worst_wait_seconds: 0.1,
		full_count: 0,
		waiting_seconds: 0,
		readers: 16,
		point_reads: 'inline',
		point_read_us: 2,
		sweeping: null
	}
};

const READ_DATABASE = {
	worstWaitSeconds: 0.1,
	fullCount: 0,
	waitingSeconds: 0,
	readers: 16,
	pointReadsInline: true,
	pointReadMicroseconds: 2,
	sweeping: null
};

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('reading how well the server has kept up', () => {
	it('reads all three of the readings an admin is given', async () => {
		answers(ADMIN_ANSWER);

		expect(await fetchHealth()).toEqual({
			loop: { worstLagSeconds: 0.42, heldCount: 3 },
			threads: { worstWaitSeconds: 1.5, fullCount: 2, waitingSeconds: 0 },
			database: READ_DATABASE,
			queue: null,
			work: [],
			widest: []
		});
	});

	it('has no answer for anyone who is not an admin', async () => {
		// What the server actually sends a guest or a stranger: the status, and nothing else.
		answers({ status: 'ok' });

		expect(await fetchHealth()).toBeNull();
	});

	it('refuses a partial loop reading rather than reading the missing half as zero', async () => {
		// Zero is the one wrong thing this could say: it reads as "nothing has ever gone wrong".
		answers({ ...ADMIN_ANSWER, loop: { worst_lag_seconds: 0.42 } });

		expect(await fetchHealth()).toBeNull();
	});

	it('refuses a partial thread reading rather than reading the missing part as zero', async () => {
		answers({ ...ADMIN_ANSWER, threads: { worst_wait_seconds: 1.5, full_count: 2 } });

		expect(await fetchHealth()).toEqual({
			loop: { worstLagSeconds: 0.42, heldCount: 3 },
			threads: null,
			database: READ_DATABASE,
			queue: null,
			work: [],
			widest: []
		});
	});

	it('still answers when the thread reading is absent altogether', async () => {
		// A server older than this reading. The loop half is still worth showing on its own, and
		// dropping the whole answer would hide it.
		answers({ status: 'ok', loop: { worst_lag_seconds: 0.42, held_count: 3 } });

		expect(await fetchHealth()).toEqual({
			loop: { worstLagSeconds: 0.42, heldCount: 3 },
			threads: null,
			database: null,
			queue: null,
			work: [],
			widest: []
		});
	});

	/*
	 * The database reading: the third way Sift stops working, and the one the other two are blind
	 * to. Both of them can be green and telling the truth while every screen takes a minute.
	 */

	it('names the pass that is reading the whole library', async () => {
		// The reason this reading is more than a number: a wait says something is queued, and this
		// says what it is queued behind.
		answers({
			...ADMIN_ANSWER,
			database: {
				...ADMIN_ANSWER.database,
				waiting_seconds: 8.2,
				sweeping: 'duplicate fingerprints'
			}
		});

		const health = await fetchHealth();
		expect(health?.database?.sweeping).toBe('duplicate fingerprints');
		expect(health?.database?.waitingSeconds).toBe(8.2);
	});

	it('reads no pass at all as nothing rather than as a name', async () => {
		// Most of the time nothing is sweeping, and that is an answer rather than a missing field.
		answers({ ...ADMIN_ANSWER, database: { ...ADMIN_ANSWER.database, sweeping: '' } });

		expect((await fetchHealth())?.database?.sweeping).toBeNull();
	});

	it('reads which way this server answers a single-row lookup', async () => {
		answers({
			...ADMIN_ANSWER,
			database: { ...ADMIN_ANSWER.database, point_reads: 'threaded', point_read_us: 401 }
		});

		const health = await fetchHealth();
		expect(health?.database?.pointReadsInline).toBe(false);
		expect(health?.database?.pointReadMicroseconds).toBe(401);
	});

	it('reads anything but the quicker mode as the slower one', async () => {
		// A server that has stopped sending this, or sends a word this build does not know, is
		// reported as taking the handover. Claiming the quicker path for a server that may not be
		// on it is the wrong way to be wrong: it is the reading somebody checks when a screen is
		// slow, and it would send them looking somewhere else.
		answers({ ...ADMIN_ANSWER, database: { ...ADMIN_ANSWER.database, point_reads: undefined } });

		expect((await fetchHealth())?.database?.pointReadsInline).toBe(false);
	});

	it('refuses a partial database reading rather than reading the missing part as zero', async () => {
		// Zero here would say "no screen has ever waited for the database", which is the single most
		// misleading thing this pane could tell somebody who is looking at it because it did.
		answers({ ...ADMIN_ANSWER, database: { worst_wait_seconds: 1.5, full_count: 2 } });

		expect((await fetchHealth())?.database).toBeNull();
	});

	it('still answers when the database reading is absent altogether', async () => {
		// A server from before the database was watched at all. The other two are still worth
		// showing, and dropping the whole answer would hide them.
		answers({ status: 'ok', loop: ADMIN_ANSWER.loop, threads: ADMIN_ANSWER.threads });

		expect((await fetchHealth())?.database).toBeNull();
	});

	it('has no answer when the request is refused', async () => {
		answers({ detail: 'no' }, false);

		expect(await fetchHealth()).toBeNull();
	});

	it('has no answer when the request throws', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => {
				throw new Error('offline');
			})
		);

		expect(await fetchHealth()).toBeNull();
	});
});

/*
 * The fourth reading and the fifth. The three above can read healthy (nothing held, no pool full,
 * no connection queued) while no screen will load, because of a queue of work in front of every
 * request, or a single endpoint asking a small question two thousand times. Neither of those is
 * visible in a measurement of WAITING.
 */
describe('the queue in front of the work, and the work itself', () => {
	it('reads the queue when the server sends it', async () => {
		answers({
			status: 'ok',
			loop: { worst_lag_seconds: 0.42, held_count: 3 },
			loop_queue: { worst_seconds: 9.1, latest_seconds: 2.4, over_count: 51 }
		});

		const health = await fetchHealth();

		expect(health?.queue).toEqual({ worstSeconds: 9.1, latestSeconds: 2.4, overCount: 51 });
	});

	it('reads what the time went on, in the order the server ranked it', async () => {
		answers({
			status: 'ok',
			loop: { worst_lag_seconds: 0.42, held_count: 3 },
			slowest_work: [
				{ stage: 'db.read', runs: 2169, total_ms: 8400.5, worst_ms: 9.2 },
				{ stage: 'preview.encode', runs: 12, total_ms: 900, worst_ms: 210 }
			]
		});

		const health = await fetchHealth();

		expect(health?.work).toEqual([
			{ stage: 'db.read', runs: 2169, totalMs: 8400.5, worstMs: 9.2 },
			{ stage: 'preview.encode', runs: 12, totalMs: 900, worstMs: 210 }
		]);
	});

	it('drops a row missing a number rather than drawing it as costing nothing', async () => {
		// A kind of work shown as free is worse than one that is not listed: it is the row somebody
		// would rule out.
		answers({
			status: 'ok',
			loop: { worst_lag_seconds: 0.42, held_count: 3 },
			slowest_work: [
				{ stage: 'db.read', runs: 2169, total_ms: 8400.5, worst_ms: 9.2 },
				{ stage: 'broken', runs: 4, worst_ms: 1 },
				{ runs: 4, total_ms: 8, worst_ms: 1 }
			]
		});

		const health = await fetchHealth();

		expect(health?.work).toEqual([{ stage: 'db.read', runs: 2169, totalMs: 8400.5, worstMs: 9.2 }]);
	});

	it('answers with no work rather than failing when the server sends none', async () => {
		// The ordinary answer moments after a start, and it must not read as a fault.
		answers({ status: 'ok', loop: { worst_lag_seconds: 0.42, held_count: 3 } });

		const health = await fetchHealth();

		expect(health?.work).toEqual([]);
		expect(health?.queue).toBeNull();
	});
});

/* Which run of the server is answering.
 *
 * It exists so that a screen waiting out a restart it asked for can tell "it came back" from "it
 * has not gone yet": the server answers the ask BEFORE it stops, and goes on answering for a
 * second or so afterwards. Anything that polled for a reply would decide it had come back before
 * it had left.
 */
describe('serverBootId', () => {
	it('answers the id the server minted at boot', async () => {
		answers({ status: 'ok', boot: '01HX00000000000000000000AB' });

		expect(await serverBootId()).toBe('01HX00000000000000000000AB');
	});

	/* Unreachable is the ORDINARY answer in the middle of a restart, not a fault, and it must not
	   be mistaken for an id. */
	it('answers nothing when the server cannot be reached', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => {
				throw new Error('ECONNREFUSED');
			})
		);

		expect(await serverBootId()).toBeNull();
	});

	it('answers nothing for a server too old to have one', async () => {
		answers({ status: 'ok' });

		expect(await serverBootId()).toBeNull();
	});

	it('answers nothing for an id that is not a string', async () => {
		answers({ status: 'ok', boot: 12345 });

		expect(await serverBootId()).toBeNull();
	});
});
