import { stepsLeft, type Job } from './family';

/* The words on a row of the queue. */

/* A row about its own file or folder is titled by it (the server's name, so a renamed file reads
   by its name now); any other is titled by what it does. */
export function subjectOf(job: Job): string {
	return job.subject ?? job.name;
}

/** The file a finished row opens: its own. */
export function fileOf(job: Job): string | null {
	return job.subject_id ?? null;
}

/** Why a row failed, in one line with the file it was on: its family's newest failure on a
 *  folded row, its own reason on any other. */
export function whyOf(job: Job): string | null {
	const failure = job.steps?.failure;
	if (failure) {
		const on = failure.subject ? ` on ${failure.subject}` : '';
		const tries = failure.attempts > 1 ? ` (tried ${failure.attempts} times)` : '';
		return `${failure.name} failed${on}${tries}: ${failure.reason}`;
	}
	return job.reason ?? job.error ?? null;
}

/** The second line: what is being done, a running or done job's note, and on a folded row what is
 * under it, a middle dot between. */
export function doingOf(job: Job, steps: string | null): string | null {
	const doing = job.subject ? job.name : (job.steps?.subject ?? null);
	const note = job.state === 'running' || job.state === 'done' ? job.note : null;
	const said = [doing, note, steps].filter((one): one is string => Boolean(one));
	return said.length > 0 ? said.join(' \u00b7 ') : null;
}

/* What a cancel calls off: a folded row's steps as the server counted them. */
export function consequenceFor(job: Job): string {
	if (job.type === 'performance_benchmark')
		return "The tasks it paused start again, and the last benchmark's result stays. You can run it again afterwards.";
	const kids = stepsLeft(job.steps);
	const work = kids === 1 ? '1 task' : `${kids.toLocaleString()} tasks`;
	return kids > 0
		? `This cancels it and the ${work} it started that haven't finished. Work already done is kept, and you can run it again afterwards.`
		: 'Work already done is kept, and you can run it again afterwards.';
}
