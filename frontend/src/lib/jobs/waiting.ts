import { sayWindow } from '$lib/shell/when';

/*
 * How long is left, as somebody would say it.
 *
 * Its own module rather than one feature's, because two features now run a long pass over the whole
 * library and both have to say the same kind of sentence about it. A second copy of this would
 * drift on the one thing that matters here (how vague to be), and two screens rounding
 * differently is how "about 20 minutes" and "19 minutes" end up beside each other.
 */

/* Deliberately vague, and the vagueness is the honest part: the number behind it is work-per-second
 * over the last minute, and the files ahead are not the files behind. So it is said as the window
 * it stands for ("about 15 to 20 minutes left") by the one estimate formatter, and never counts
 * the last few seconds down.
 */
export function describeWait(seconds: number): string {
	return `${sayWindow(seconds, seconds)} left`;
}
