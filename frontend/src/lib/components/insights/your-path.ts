/*
 * Get to know Sift on the client: its words, the one read of it, the small celebration, and the rule
 * that a hint is shown once.
 *
 * ## What the server says and what this says
 *
 * Every SENTENCE is the server's: a path's sentence and a goal's help arrive as pieces and are drawn
 * by `HistorySentence`, and a path's and a goal's names arrive whole. What is written here is the
 * furniture around them (how many goals of a path are done) and the three hints, which are the one
 * exception, for the reason given on `HINT_WORDS`.
 *
 * ## The feel
 *
 * Short, warm, one thing at a time, progress you can see, a small celebration when a goal is
 * reached, and no guilt: no streak, no weekly chore, nothing that counts what was missed.
 */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { interfaceState } from '$lib/shell/interface-state.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

export type PathAnswer = components['schemas']['Path'];
export type LearningPath = components['schemas']['LearningPath'];
export type PathGoal = components['schemas']['Goal'];
export type HintName = components['schemas']['Hint']['name'];

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

/**
 * THE THREE HINTS' WORDS, and why they are written here rather than read from the answer.
 *
 * A hint is drawn where somebody is (the empty Organize board, a face group, the first visit to
 * Insights) and none of those screens has any reason to read the learning paths: the answer judges
 * every goal, for one sentence that never changes. What the account holds about a hint is one word,
 * `seen`, in the interface state every screen can already read in one request. So the words are
 * the client's, like every other fixed line on a screen, and the server keeps only the NAMES (the
 * closed list it refuses anything outside of).
 */
export const HINT_WORDS: Record<HintName, string> = {
	organize_empty: "Nothing to decide. Anything Sift can't work out by itself waits here for you.",
	first_pile: 'Name one and Sift matches the rest. You can undo any of it.',
	first_insights: 'These are yours. They stay on the device Sift runs on.'
};

/*
 * --- A hint, once -----------------------------------------------------------------------------
 *
 * Shown ONCE per account, and "shown" is the moment it is drawn, not the moment somebody presses
 * Close: a hint that came back on every visit until it was dismissed would be a nag, which is the
 * one thing this path is built not to be. So the account is told as it appears, and it is also
 * remembered for this session, so a second screen drawing the same hint before the account's answer
 * has been read again does not show it twice.
 *
 * A failed read shows nothing. A hint that may already have been seen is not shown again on a
 * guess; the safe direction for a once-only thing is not at all. A failed WRITE is the one way a
 * hint can come back, on the next visit: said here, and not worth a message on screen.
 */
const shownThisSession = new Set<HintName>();

/** The interface-state key the account keeps a hint's `seen` under. The settings hub's closed list
 *  names each of the three (`settings_hub/service.py` `INTERFACE_KEYS`). */
function hintKey(name: HintName): string {
	return `path.hint.${name}.seen`;
}

/**
 * Claim a hint for drawing: true at most ONCE per session for each name, and only when the account
 * has not seen it. Resolves false on any doubt.
 *
 * The claim is made in the same step as the check, after the read, so two screens mounting the
 * same hint while the one read is in flight cannot both be told yes. A claim whose screen has gone
 * before drawing is simply spent: the hint is not shown this session and the account is not told,
 * so it comes back on the next visit: the direction that loses nothing.
 */
export async function claimHint(name: HintName): Promise<boolean> {
	let state: Record<string, string>;
	try {
		state = await interfaceState();
	} catch {
		return false;
	}
	if (shownThisSession.has(name) || state[hintKey(name)] === 'seen') return false;
	shownThisSession.add(name);
	return true;
}

/**
 * Tell the account a hint has been drawn, so it is never drawn again.
 *
 * Not followed by a fresh read of the interface state: that would forget every other key the
 * session holds (the popout's fold among them) until somebody asked again. The session's own
 * memory above is what stops a second showing before the next sign-in reads the account.
 */
export function hintShown(name: HintName): void {
	void api.post(`/insights/path/hints/${name}/seen`).catch(() => {
		// Not written: the hint may come back on the next visit. See the header.
	});
}

/** For the tests only: forget the session's memory of what was celebrated and shown. */
export function forgetPathSession(): void {
	known = null;
	shownThisSession.clear();
}
