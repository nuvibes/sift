import { describe, expect, it } from 'vitest';
import { sayDuration as theOneSet } from '$lib/shell/duration';
import { canCancel, canRetry, offeredWhileViewing, sayDuration, startsIn } from './labels';

/* The three small answers every row on the queue screen is drawn from.
 *
 * What a job is CALLED is not here: the name is declared beside the handler that does the work
 * and arrives on the row with the job: a map here would know a few of the types and show the
 * internal word for the rest. What is here is the part that really does belong to the screen.
 *
 * `startsIn` is worth pinning for a reason that is not about formatting: the queue's clock is
 * `time.time()` and its timestamps are SECONDS. Read as milliseconds every job in the library is
 * dated 1970, which looks like a broken column rather than a unit mistake. And its words come
 * from the one ladder in `$lib/shell/when`, so a scheduled job reads like every other moment on screen.
 */

const MINUTE = 60;
const HOUR = 60 * MINUTE;

describe('when a scheduled job starts', () => {
	it('says nothing for a job that may be taken now', () => {
		expect(startsIn(null, 1000)).toBeNull();
		expect(startsIn(undefined, 1000)).toBeNull();
		expect(startsIn(900, 1000)).toBeNull();
	});

	it('reads the one ladder of relative words, so it agrees with every other "in ..." on screen', () => {
		expect(startsIn(1000 + 2 * HOUR, 1000)).toBe('starts in 2 hours');
		expect(startsIn(1000 + 30, 1000)).toBe('starts any moment');
	});
});

describe('what can still be done to a job', () => {
	it('offers a retry only on something that has stopped and could be repeated', () => {
		expect(canRetry('failed')).toBe(true);
		expect(canRetry('canceled')).toBe(true);

		expect(canRetry('queued')).toBe(false);
		expect(canRetry('running')).toBe(false);
		expect(canRetry('blocked')).toBe(false);
		expect(canRetry('succeeded')).toBe(false);
	});

	it('offers a cancel while there is still something to call off', () => {
		expect(canCancel('queued')).toBe(true);
		expect(canCancel('running')).toBe(true);
		// Blocked is work that has not happened YET: it is waiting on something, and waiting is
		// exactly when somebody wants to stop it.
		expect(canCancel('blocked')).toBe(true);

		expect(canCancel('failed')).toBe(false);
		expect(canCancel('canceled')).toBe(false);
		expect(canCancel('succeeded')).toBe(false);
	});

	/* A held job, and the reason this screen offers it nothing at all.
	 *
	 * Not a retry: it has not stopped, it is held, and the way back from a hold is Resume. Not a
	 * cancel either, and that one is a fact about the queue rather than a view about what somebody
	 * ought to be offered: the kernel's cancel walks the tree with its states written into the
	 * statement (queued, running, blocked), so a paused job handed to it is not cancelled and
	 * nothing says so. A button that reports success and does nothing is worse than no button.
	 *
	 * The pair that DOES act on one lives on the Downloads page, which owns both halves: Resume
	 * puts it back in the queue, and Cancel there stops the download and drops its bytes. */
	it('offers a held job neither, because the queue cannot honour either here', () => {
		expect(canRetry('paused')).toBe(false);
		expect(canCancel('paused')).toBe(false);
	});

	it('offers neither on a state it has never heard of', () => {
		// A row left by a later version. Offering an action on it would offer one that cannot work.
		expect(canRetry('something_new')).toBe(false);
		expect(canCancel('something_new')).toBe(false);
	});
});

describe('startsIn', () => {
	const NOW = 1_800_000_000;

	it('says nothing for a job that may be taken now', () => {
		expect(startsIn(null, NOW)).toBeNull();
		expect(startsIn(undefined, NOW)).toBeNull();
	});

	it('says nothing once the time has passed, so it stops being a scheduled job', () => {
		expect(startsIn(NOW - 1, NOW)).toBeNull();
		expect(startsIn(NOW, NOW)).toBeNull();
	});

	it('says how long a nightly job has to wait', () => {
		// The case it is for: a backup queued now and due tomorrow must not read as "queued, 5h
		// ago" beside work that really is stuck.
		expect(startsIn(NOW + 20 * 60 * 60, NOW)).toBe('starts in 20 hours');
		expect(startsIn(NOW + 60 * 60, NOW)).toBe('starts in an hour');
		expect(startsIn(NOW + 90, NOW)).toBe('starts in 2 minutes');
		expect(startsIn(NOW + 5, NOW)).toBe('starts any moment');
		expect(startsIn(NOW + 3 * 24 * 60 * 60, NOW)).toBe('starts in 3 days');
	});
});

describe('which bulk actions belong on screen', () => {
	/* The rule. 100,000 stopped jobs and one failure: filtered to the stopped ones, "Clear them"
	   would mean the failure. */
	it('offers everything while looking at everything', () => {
		expect(offeredWhileViewing(null, 'failed')).toBe(true);
		expect(offeredWhileViewing(null, 'canceled')).toBe(true);
		expect(offeredWhileViewing(null, 'queued', 'running', 'blocked')).toBe(true);
	});

	it('offers a pile its own actions', () => {
		expect(offeredWhileViewing('canceled', 'canceled')).toBe(true);
		expect(offeredWhileViewing('failed', 'failed')).toBe(true);
		// A stop acts on three states at once, and looking at any of them is looking at it.
		expect(offeredWhileViewing('running', 'queued', 'running', 'blocked')).toBe(true);
	});

	it('withholds an action for a pile that is not being looked at', () => {
		expect(offeredWhileViewing('canceled', 'failed')).toBe(false);
		expect(offeredWhileViewing('failed', 'canceled')).toBe(false);
		expect(offeredWhileViewing('done', 'queued', 'running', 'blocked')).toBe(false);
	});
});

describe('sayDuration', () => {
	it('is the one set of duration words, handed on rather than written again', () => {
		/* How long a finished run took, beside it on Activity. The words themselves are
		   `$lib/shell/duration`'s and tested there; this holds the Activity rows to the same function. */
		expect(sayDuration).toBe(theOneSet);
		expect(sayDuration(30)).toBe('under a minute');
		expect(sayDuration(45 * 60)).toBe('45 min');
		expect(sayDuration(3 * 3600)).toBe('3 h');
		expect(sayDuration(3 * 24 * 3600)).toBe('3 d');
		expect(sayDuration(-1)).toBeNull();
	});
});
