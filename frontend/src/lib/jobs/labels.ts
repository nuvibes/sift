/* Small helpers the dashboard uses to render a row.
 *
 * What a job is CALLED is not here: it is declared beside the handler that does the work and
 * arrives on the row, because the screen does not own the work and would drift from it.
 */
import { sayWhen } from '$lib/shell/when';

/** The states a job can be put back from. Not `paused`: it is held, and its way back is Resume. */
export function canRetry(state: string): boolean {
	return state === 'failed' || state === 'canceled';
}

/**
 * The states there is still something to call off.
 *
 * Not `paused`: the kernel's cancel walks the job tree by queued, running and blocked, so a paused
 * job handed to it is not cancelled and nothing says so. A paused download's way out is on the
 * Downloads page. The state words are `Badge`'s, so none is written here.
 */
export function canCancel(state: string): boolean {
	return state === 'queued' || state === 'running' || state === 'blocked';
}

/* Handed on from `$lib/shell/duration` because the Activity rows read it from this module. */
export { sayDuration } from '$lib/shell/duration';

/**
 * Whether a bulk action for these states belongs on screen, given the pile being looked at.
 *
 * Offered regardless of the filter, a bulk action would act on a pile the reader cannot see and
 * read as broken while working. `null` is "everything". The label naming its pile and number is
 * the other half of the answer.
 */
export function offeredWhileViewing(filter: string | null, ...states: string[]): boolean {
	return filter === null || states.includes(filter);
}

/**
 * When a queued job is not waiting for a worker but for a time, said in words.
 *
 * A job whose `run_after` has not come sits in the queue exactly like one next in line, so without
 * this a backup due tomorrow reads as stuck. Both arguments are epoch SECONDS, as the queue stamps.
 * Null when the job may be taken now.
 */
export function startsIn(runAfter: number | null | undefined, now: number): string | null {
	if (runAfter === null || runAfter === undefined) return null;
	if (runAfter - now <= 0) return null;
	return `starts ${sayWhen(runAfter, now)}`;
}
