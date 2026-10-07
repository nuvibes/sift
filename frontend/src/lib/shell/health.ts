/* What the server says about its own responsiveness.
 *
 * `/health` is deliberately outside the API prefix (a container's health probe and an uptime
 * monitor cannot sign in), so it is read here with a plain fetch rather than through the API
 * client, which prefixes every path.
 *
 * Signed out, the answer is a bare status. The detail below rides along only for an admin, so a
 * missing reading is the ordinary answer for everyone else rather than a fault.
 *
 * Five readings, not one, because there are five ways Sift stops being usable and they need
 * different fixes. Work that will not let go stops everything at the same time, pages included.
 * Work waiting its turn for a thread leaves the pages quick while video stutters. Work waiting its
 * turn for a database connection stops every screen together while the first two read perfectly
 * healthy.
 *
 * The fourth is the one all three of those are blind to in the same way: they measure WAITING for
 * something, and a server buried in work that each piece returns promptly waits for nothing. A
 * single request making hundreds of small round trips is slow without ever queueing, and reads
 * healthy on all three.
 *
 * And the fifth is the other half of every one of them: what the time actually went ON. The first
 * four say the server is not short of anything; none says what it is busy with. A piece of work
 * costing four milliseconds that runs two thousand times is eight seconds, and it looks entirely
 * innocent one line at a time.
 */

interface LoopHealth {
	/** The longest single moment the server was unable to do anything, in seconds. */
	worstLagSeconds: number;
	/** How many times it has been held long enough to be worth recording. */
	heldCount: number;
}

interface ThreadHealth {
	/** The longest anything has waited for a free thread, in seconds. */
	worstWaitSeconds: number;
	/** How many times that wait was long enough to be worth recording. */
	fullCount: number;
	/** How long something has been waiting right now. Zero when nothing is. */
	waitingSeconds: number;
}

interface DatabaseHealth {
	/** The longest anything has waited for a database connection, in seconds. */
	worstWaitSeconds: number;
	/** How many times that wait was long enough to be worth recording. */
	fullCount: number;
	/** How long something has been waiting right now. Zero when nothing is. */
	waitingSeconds: number;
	/** How many connections there are to share. Moves with the worker setting above. */
	readers: number;
	/**
	 * Whether a single-row lookup runs straight away or is handed to a helper first.
	 *
	 * Decided by timing this machine at start-up, not by a setting: the same lookup is
	 * microseconds with the database on a local disk and hundreds of times that with it on a
	 * network share, and only the second is worth the handover. Shown because two installs can
	 * legitimately answer differently and an admin comparing them has to be able to see it.
	 */
	pointReadsInline: boolean;
	/** What that lookup measured at start-up, in microseconds. */
	pointReadMicroseconds: number;
	/**
	 * Which whole-library pass is reading right now, or null.
	 *
	 * The reason this reading is worth having rather than just being a number: a wait says
	 * something is queued, and this says what it is queued behind.
	 */
	sweeping: string | null;
}

interface QueueHealth {
	/** The longest the server's own queue of work has taken to clear, in seconds. */
	worstSeconds: number;
	/** How long it is taking right now. The one that moves while somebody watches a screen wait. */
	latestSeconds: number;
	/** How many times it has been slow enough to be worth recording. */
	overCount: number;
}

interface WorkKind {
	/** What the work is, in the server's own name for it. */
	stage: string;
	/** How many times it ran in this window. */
	runs: number;
	/** What it cost altogether, which is the number that matters and the one no single line shows. */
	totalMs: number;
	/** The worst single run. */
	worstMs: number;
}

interface WideRead {
	/** The statement, shortened. It carries no values: every value is bound, never written in. */
	read: string;
	/** The most rows it handed back in one go. The number that decides whether it survives load. */
	widestRows: number;
	/** How many times it ran in this window. */
	runs: number;
	/** Every row it moved altogether. */
	totalRows: number;
}

export interface ServerHealth {
	loop: LoopHealth;
	/** Absent only if the server is older than this reading; present on every current one. */
	threads: ThreadHealth | null;
	/** The same. Absent on a server from before the database was watched at all. */
	database: DatabaseHealth | null;
	/** Absent on a server from before the queue itself was watched. */
	queue: QueueHealth | null;
	/** Empty when nothing has been measured yet, which is the ordinary answer just after a start. */
	work: WorkKind[];
	/** Empty on a server from before reads were measured by width rather than only by duration. */
	widest: WideRead[];
}

function numbers(source: unknown, keys: string[]): number[] | null {
	if (typeof source !== 'object' || source === null) return null;
	const record = source as Record<string, unknown>;
	const found = keys.map((key) => record[key]);
	// Every one has to be a number. A partial answer drawn as zero would read as "nothing has ever
	// gone wrong", which is the one wrong thing this could say.
	if (found.some((value) => typeof value !== 'number')) return null;
	return found as number[];
}

/**
 * Which RUN of the server this is, or null when it will not answer.
 *
 * WHAT IT IS FOR: waiting out a restart honestly. The server answers the ask BEFORE it goes, and
 * goes on answering for a second or so afterwards. So a screen that polled for "does it reply"
 * would decide it had come back before it had left, and then draw the state of the process it had
 * just asked to stop. A value that is different in the new process is the only thing that settles
 * it, and no amount of waiting a bit longer is the same answer.
 *
 * Separate from `fetchHealth`, which answers null to anybody who is not an admin: this one rides
 * with the status line, so it is the same question from the shell, from a browser on the machine,
 * and from a browser on another one.
 */
export async function serverBootId(): Promise<string | null> {
	try {
		const response = await fetch('/health', { credentials: 'same-origin' });
		if (!response.ok) return null;
		const body = (await response.json()) as { boot?: unknown };
		return typeof body.boot === 'string' && body.boot !== '' ? body.boot : null;
	} catch {
		/* Unreachable is the ordinary answer in the middle of a restart, not a fault. */
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
	// The loop reading is what decides whether there is an answer at all: it is the one an admin
	// always gets, so its absence is how "not an admin" is told from "a reading is missing".
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
						// Read on its own because it is the one field that is legitimately absent: no
						// pass is running most of the time, and a missing name is that answer rather
						// than a missing reading.
						sweeping: sweepingIn(answer?.database)
					}
	};
}

function pointReadsInlineIn(source: unknown): boolean {
	// A word rather than a flag on the wire, because the two modes have names and a boolean called
	// something-true reads as a setting somebody chose. Anything else is read as the slower of the
	// two: reporting a server as taking the quicker path when it is not is the wrong way to be wrong.
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
		// Dropped rather than drawn as zero, for the reason the one below is: a read shown as
		// moving nothing is worse than a read that is not listed.
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
		// A row missing any of its numbers is dropped rather than drawn as zero: a kind of work
		// shown as costing nothing is worse than one that is not listed.
		if (measured === null || typeof stage !== 'string' || stage === '') continue;
		out.push({ stage, runs: measured[0], totalMs: measured[1], worstMs: measured[2] });
	}
	return out;
}
