/* Small helpers the dashboard uses to render a row. */
import { sayWhen } from '$lib/shell/when';

/** The states a job can be put back from. Not `paused`: it is held, and its way back is Resume. */
export function canRetry(state: string): boolean {
	return state === 'failed' || state === 'canceled';
}

/** The states there is still something to call off. */
export function canCancel(state: string): boolean {
	return state === 'queued' || state === 'running' || state === 'blocked';
}

/* Handed on from `$lib/shell/duration` because the Activity rows read it from this module. */
export { sayDuration } from '$lib/shell/duration';

/** Whether a bulk action for these states belongs on screen, given the pile being looked at. */
export function offeredWhileViewing(filter: string | null, ...states: string[]): boolean {
	return filter === null || states.includes(filter);
}

/** When a queued job is not waiting for a worker but for a time, said in words. */
export function startsIn(runAfter: number | null | undefined, now: number): string | null {
	if (runAfter === null || runAfter === undefined) return null;
	if (runAfter - now <= 0) return null;
	return `starts ${sayWhen(runAfter, now)}`;
}
