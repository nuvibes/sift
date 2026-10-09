import { sayWindow } from '$lib/shell/when';

/* How long is left, as somebody would say it. */

/* Deliberately vague, and the vagueness is the honest part: the number behind it is
 * work-per-second over the last minute, and the files ahead are not the files behind. */
export function describeWait(seconds: number): string {
	return `${sayWindow(seconds, seconds)} left`;
}
