// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * THE LEAF AND THE BOLT: background work stepping back while this device is in use, said where
 * somebody can see it, and a press that overrules it for a while.
 *
 * Background work uses a share of this device (a quarter unless somebody chose otherwise) while
 * somebody is at its keyboard or mouse, and all of it once it has been left alone for a minute
 * (Settings > Performance, "Use less system resources while you're working"). The words name that share. That is right most of the time and wrong exactly when the person at the
 * keyboard is waiting on the work: then they want it done, and the setting is the wrong size of
 * answer, since it turns the step back off for good. The press is the moment's answer instead,
 * held by the server until Sift stops or somebody presses again, so every window draws the same
 * state and a press in one is seen in the others.
 *
 * WHEN IT IS DRAWN, and why each other case draws nothing (a control that would do nothing is not
 * offered):
 *   - The leaf: tasks are running on a share of this device because it is in use.
 *     A press runs the full amount.
 *   - The bolt: tasks are running, somebody is at this device, and the full amount was pressed
 *     for. A press steps back again.
 *   - Nothing running: there is nothing to give more of or less of.
 *   - Nobody at this device: the pool already runs its full count by itself, so there is nothing
 *     held back to give. A press made earlier is kept and the bolt comes back with the person.
 *   - The setting off, a pool of one task, or a device whose input cannot be read (anything but
 *     Windows): the step back is never in play, and the server says neither state.
 *   - A guest: the queue is an admin's, and a guest's window never reads it.
 *
 * What is read is the queue's first page, which the shell already keeps current for the rail
 * (`imports`): its two flags and its running count. Nothing here asks the server on a timer.
 */

import { api, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { imports } from '$lib/library/imports.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { UNREACHABLE } from '$lib/shell/unreachable';

/* The queue page's two flags, its counts and the share: all this reads of it, so a test hands in
   only those. A server that does not say the share reads as the default quarter. */
type StepBackFacts = Partial<
	Pick<
		components['schemas']['JobsPage'],
		'stepping_back' | 'full_amount' | 'counts' | 'step_back_share'
	>
>;

/** The share a server says nothing about: the default, a quarter. */
export const DEFAULT_SHARE = 25;

/* The shares that have a word of their own; any other is said as a percent. */
const SHARE_WORDS: Record<number, string> = {
	10: 'a tenth',
	20: 'a fifth',
	25: 'a quarter',
	50: 'half',
	75: 'three quarters',
	100: 'all'
};

/** A share of this device in words: "a quarter", "half", "40%". */
export function shareWords(percent: number): string {
	return SHARE_WORDS[percent] ?? `${percent}%`;
}

/**
 * The sentence the leaf's tooltip, a phone's row and the Activity line all say while the step
 * back holds: how much of this device background work is using, and why.
 */
export function usingShare(percent: number | null | undefined): string {
	return `Using ${shareWords(percent ?? DEFAULT_SHARE)} of this device while it's in use`;
}

/** Which of the two is drawn, or neither. */
type FullAmountState = 'less' | 'full' | null;

/** The words the leaf and the bolt say, on the rail's tooltip and on a phone's More screen. */
export const FULL_AMOUNT_COPY = {
	/* The control's name, the same in both states; pressed or not is said by `aria-pressed`. */
	name: 'Use the full amount of this device',
	lessPress: 'Press to use the full amount.',
	full: "Using the full amount of this device although it's in use",
	fullPress: 'Press to use less system resources again.',
	/* The press on a phone's row, where there is room for a verb. */
	useFull: 'Use the full amount',
	stepBack: 'Use less system resources',
	failed: "Sift couldn't change how much of this device it uses"
} as const;

/** What is drawn for one read of the queue. Public because it is the seam the tests drive. */
export function fullAmountState(page: StepBackFacts | null, isAdmin: boolean): FullAmountState {
	if (!isAdmin || page === null) return null;
	if ((page.counts?.running ?? 0) === 0) return null;
	if (page.full_amount === true) return 'full';
	if (page.stepping_back === true) return 'less';
	return null;
}

/** The current state, from the queue page the shell keeps. */
export function currentFullAmount(isAdmin: boolean): FullAmountState {
	return fullAmountState(imports.page as StepBackFacts | null, isAdmin);
}

/** `usingShare` for a queue page: the share it names, or the default where it names none. */
export function usingShareOf(page: unknown): string {
	return usingShare((page as StepBackFacts | null | undefined)?.step_back_share);
}

/** The share the step back keeps to, as the queue page the shell keeps says it. */
export function currentShare(): number {
	return (imports.page as StepBackFacts | null)?.step_back_share ?? DEFAULT_SHARE;
}

/** The state's first sentence: what is happening. */
export function fullAmountSays(state: Exclude<FullAmountState, null>, share: number): string {
	return state === 'less' ? usingShare(share) : FULL_AMOUNT_COPY.full;
}

/** The tooltip's two sentences for a state: what is happening, then what a press does. */
export function fullAmountTip(
	state: Exclude<FullAmountState, null>,
	share: number = currentShare()
): string {
	const press = state === 'less' ? FULL_AMOUNT_COPY.lessPress : FULL_AMOUNT_COPY.fullPress;
	return `${fullAmountSays(state, share)}. ${press}`;
}

/* The press's own address. Cast because the generated paths do not carry it yet. */
const PRESS = '/jobs/full-amount';

/**
 * Press for the full amount (`on`), or step back again.
 *
 * The queue page is read again at once, so this window answers without waiting for the live
 * connection; the others are told by the server and read it themselves.
 */
export async function pressFullAmount(on: boolean): Promise<void> {
	try {
		await api.post(PRESS, { body: { on } });
	} catch (error) {
		toasts.show(error instanceof ApiError ? FULL_AMOUNT_COPY.failed : UNREACHABLE, {
			tone: 'error'
		});
		return;
	}
	await imports.refresh();
}
