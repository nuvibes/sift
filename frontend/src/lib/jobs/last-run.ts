/* A long run's last finished run, as a page that starts one says it. */

import type { components } from '$lib/api/schema';
import { exactly, sayAgo } from '$lib/shell/when';

/** A run that ended, as the record keeps it. `outcome` is absent where only a run that finished
 *  its work is ever handed in. */
export type RunOnRecord = Pick<components['schemas']['LastRunView'], 'ended_at'> &
	Partial<Pick<components['schemas']['LastRunView'], 'outcome' | 'said'>>;

/** What a page calls its run ending, each given how long ago it was. */
export interface RunWords {
	ran: (when: string) => string;
	failed: (when: string) => string;
	canceled: (when: string) => string;
}

/** The words for a run named by its subject: "It ran 3 minutes ago." */
export function runWords(subject = 'It'): RunWords {
	return {
		ran: (when) => `${subject} ran ${when}.`,
		failed: (when) => `${subject} stopped with a problem ${when}.`,
		canceled: (when) => `${subject} was stopped ${when}.`
	};
}

/** How the run ended and when, in the page's words. */
export function sayRun(run: RunOnRecord, words: RunWords, now: number): string {
	const when = sayAgo(run.ended_at, now);
	if (run.outcome === 'failed') return words.failed(when);
	if (run.outcome === 'canceled') return words.canceled(when);
	return words.ran(when);
}

/** Whether the run finished its work, so its own sentence says what it did. */
function finishedItsWork(run: RunOnRecord): boolean {
	return !run.outcome || run.outcome === 'done';
}

/** The run's own sentence for the line, or null where it said nothing or did not finish. */
export function lineSaid(run: RunOnRecord): string | null {
	return finishedItsWork(run) && run.said ? run.said : null;
}

/** The hover: the exact moment, and a run that stopped short says why. */
export function hoverSaid(run: RunOnRecord): string {
	const moment = exactly(run.ended_at);
	return !finishedItsWork(run) && run.said ? `${moment}. ${run.said}` : moment;
}
