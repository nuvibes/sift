/* Get to know Sift on the client. Every sentence is the server's (`HistorySentence` draws it);
 * this is the furniture around them, and the small celebration. */
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

// Remembered for the session, not stored: a goal is judged from the record alone. The first
// read celebrates nothing, or old goals would be announced on every sign-in.
let known: Set<string> | null = null;

/** Toast a line per goal newly reached since this session's last answer; returns the lines. */
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
