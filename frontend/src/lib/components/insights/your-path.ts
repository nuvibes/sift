/*
 * Get to know Sift on the client: its words, the one read of it, and the small celebration.
 *
 * ## What the server says and what this says
 *
 * Every SENTENCE is the server's: a path's sentence and a goal's help arrive as pieces and are drawn
 * by `HistorySentence`, and a path's and a goal's names arrive whole. What is written here is the
 * furniture around them (how many goals of a path are done).
 *
 * ## The feel
 *
 * Short, warm, one thing at a time, progress you can see, a small celebration when a goal is
 * reached, and no guilt: no streak, no weekly chore, nothing that counts what was missed.
 */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { toasts } from '$lib/shell/toasts.svelte';

export type PathAnswer = components['schemas']['Path'];
export type LearningPath = components['schemas']['LearningPath'];
export type PathGoal = components['schemas']['Goal'];

/** How far along a path is: "2 of 5 done". */
export function pathProgress(path: Pick<LearningPath, 'goals'>): string {
	const done = path.goals.filter((goal) => goal.done).length;
	return `${done.toLocaleString()} of ${path.goals.length.toLocaleString()} done`;
}

/** Read the learning paths. One request; the caller decides what a failure draws. */
export function readPath(): Promise<PathAnswer> {
	return api.get<PathAnswer>('/insights/path');
}

/*
 * --- The small celebration --------------------------------------------------------------------
 *
 * One line in the toast that exists, when a goal is FOUND reached that was not reached the last time
 * this session read the paths. No confetti, no sound, nothing that waits to be dismissed.
 *
 * Remembered for the SESSION and not stored, deliberately: nothing on the server says "celebrated",
 * because a goal is judged from the record and a flag beside the record is the thing the paths
 * refuse to keep. The cost is said rather than hidden: a goal reached while no screen reading the
 * paths was open is found done on the next visit with no line for it: the tick and its date are the
 * whole of the news then. The first read of a session celebrates nothing, or every goal reached a
 * month ago would be announced on every sign-in.
 */
let known: Set<string> | null = null;

/** Note what a fresh answer says is reached, and say one line for anything newly reached since the
 *  last answer this session saw. Returns the lines said, for the tests. */
export function celebrate(answer: Pick<PathAnswer, 'paths'>): string[] {
	const goals = answer.paths.flatMap((path) => path.goals);
	const reached = new Set(goals.filter((goal) => goal.done).map((goal) => goal.id));
	const said: string[] = [];
	if (known !== null) {
		for (const goal of goals) {
			if (goal.done && !known.has(goal.id)) said.push(`Goal reached: ${goal.title}`);
		}
	}
	known = reached;
	for (const line of said) toasts.show(line, { tone: 'success', icon: 'check_circle' });
	return said;
}

/** For the tests only: forget the session's memory of what was celebrated. */
export function forgetPathSession(): void {
	known = null;
}
