/*
 * The server's own responsiveness, from `/health` outside the API prefix; the detail is an admin's.
 * Five readings, five different faults: the loop held, a thread, a database connection, the queue,
 * and what the time went ON.
 */

interface LoopHealth {
	worstLagSeconds: number;
	heldCount: number;
}

interface ThreadHealth {
	worstWaitSeconds: number;
	fullCount: number;
	waitingSeconds: number;
}

interface DatabaseHealth {
	worstWaitSeconds: number;
	fullCount: number;
	waitingSeconds: number;
	/** Moves with the worker setting. */
	readers: number;
	/** Timed at start-up: a local disk and a network share answer differently. */
	pointReadsInline: boolean;
	pointReadMicroseconds: number;
	/** What a wait is queued behind. */
	sweeping: string | null;
}

interface QueueHealth {
	worstSeconds: number;
	latestSeconds: number;
	overCount: number;
}

interface WorkKind {
	stage: string;
	runs: number;
	/** The number no single line shows. */
	totalMs: number;
	worstMs: number;
}

interface WideRead {
	/** Every value is bound, never written in. */
	read: string;
	widestRows: number;
	runs: number;
	totalRows: number;
}

export interface ServerHealth {
	loop: LoopHealth;
	/** Absent only on an older server. */
	threads: ThreadHealth | null;
	database: DatabaseHealth | null;
	queue: QueueHealth | null;
	work: WorkKind[];
	widest: WideRead[];
}

function numbers(source: unknown, keys: string[]): number[] | null {
	if (typeof source !== 'object' || source === null) return null;
	const record = source as Record<string, unknown>;
	const found = keys.map((key) => record[key]);
	// A partial answer drawn as zero would say nothing ever went wrong.
	if (found.some((value) => typeof value !== 'number')) return null;
	return found as number[];
}

/**
 * Which RUN of the server this is: the server still answers for a moment after it is asked to
 * restart, so only a new value proves it came back. For everyone, unlike `fetchHealth`.
 */
export async function serverBootId(): Promise<string | null> {
	try {
		const response = await fetch('/health', { credentials: 'same-origin' });
		if (!response.ok) return null;
		const body = (await response.json()) as { boot?: unknown };
		return typeof body.boot === 'string' && body.boot !== '' ? body.boot : null;
	} catch {
		return null;
	}
}

export async function fetchHealth(): Promise<ServerHealth | null> {
	let body: unknown;
	try {
		const response = await fetch('/health', { credentials: 'same-origin' });
		if (!response.ok) return null;
		body = await response.json();
	} catch {
		return null;
	}

	const answer = body as {
		loop?: unknown;
		threads?: unknown;
		database?: unknown;
		loop_queue?: unknown;
		slowest_work?: unknown;
		widest_reads?: unknown;
	} | null;
	const loop = numbers(answer?.loop, ['worst_lag_seconds', 'held_count']);
	// The loop reading is an admin's alone, so its absence means "not an admin".
	if (loop === null) return null;

	const threads = numbers(answer?.threads, ['worst_wait_seconds', 'full_count', 'waiting_seconds']);
	const database = numbers(answer?.database, [
		'worst_wait_seconds',
		'full_count',
		'waiting_seconds',
		'readers',
		'point_read_us'
	]);

	const queue = numbers(answer?.loop_queue, ['worst_seconds', 'latest_seconds', 'over_count']);

	return {
		loop: { worstLagSeconds: loop[0], heldCount: loop[1] },
		queue:
			queue === null
				? null
				: { worstSeconds: queue[0], latestSeconds: queue[1], overCount: queue[2] },
		work: workIn(answer?.slowest_work),
		widest: widestIn(answer?.widest_reads),
		threads:
			threads === null
				? null
				: { worstWaitSeconds: threads[0], fullCount: threads[1], waitingSeconds: threads[2] },
		database:
			database === null
				? null
				: {
						worstWaitSeconds: database[0],
						fullCount: database[1],
						waitingSeconds: database[2],
						readers: database[3],
						pointReadMicroseconds: database[4],
						pointReadsInline: pointReadsInlineIn(answer?.database),
						// Legitimately absent: no pass runs most of the time.
						sweeping: sweepingIn(answer?.database)
					}
	};
}

function pointReadsInlineIn(source: unknown): boolean {
	// Anything unknown reads as the slower path.
	if (typeof source !== 'object' || source === null) return false;
	return (source as Record<string, unknown>).point_reads === 'inline';
}

function sweepingIn(source: unknown): string | null {
	if (typeof source !== 'object' || source === null) return null;
	const named = (source as Record<string, unknown>).sweeping;
	return typeof named === 'string' && named !== '' ? named : null;
}

function widestIn(source: unknown): WideRead[] {
	if (!Array.isArray(source)) return [];
	const out: WideRead[] = [];
	for (const row of source) {
		const measured = numbers(row, ['widest_rows', 'runs', 'total_rows']);
		const read = (row as Record<string, unknown> | null)?.read;
		// Dropped rather than drawn as zero.
		if (measured === null || typeof read !== 'string' || read === '') continue;
		out.push({ read, widestRows: measured[0], runs: measured[1], totalRows: measured[2] });
	}
	return out;
}

function workIn(source: unknown): WorkKind[] {
	if (!Array.isArray(source)) return [];
	const out: WorkKind[] = [];
	for (const row of source) {
		const measured = numbers(row, ['runs', 'total_ms', 'worst_ms']);
		const stage = (row as Record<string, unknown> | null)?.stage;
		if (measured === null || typeof stage !== 'string' || stage === '') continue;
		out.push({ stage, runs: measured[0], totalMs: measured[1], worstMs: measured[2] });
	}
	return out;
}
