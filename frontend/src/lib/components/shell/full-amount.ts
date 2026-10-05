// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * THE LEAF AND THE BOLT: background work stepping back to a share of this device, said where
 * somebody can see it, and a press that overrules it until Sift stops or the next press.
 *
 * The work steps back while somebody is at the keyboard or mouse, or while other programs keep the
 * device busy (Settings > Performance). The leaf is drawn while it holds, the bolt while the full
 * amount was pressed for; nothing while no task runs, while neither cause holds, or for a guest.
 *
 * What is read is the queue's first page, which the shell already keeps current for the rail.
 */

import { api, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { imports } from '$lib/library/imports.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { UNREACHABLE } from '$lib/shell/unreachable';

/** Why the work steps back: somebody at this device, or other programs keeping it busy. */
type StepBackCause = NonNullable<components['schemas']['JobsPage']['step_back_for']>;

/* All this reads of the queue page, so a test hands in only those. A server that does not say the
   share reads as the default quarter, and one that does not say why reads as somebody here. */
type StepBackFacts = Partial<
	Pick<
		components['schemas']['JobsPage'],
		'stepping_back' | 'full_amount' | 'counts' | 'step_back_share' | 'step_back_for'
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

/** What the leaf's tooltip, a phone's row and the Activity line say while the step back holds. */
export function usingShare(
	percent: number | null | undefined,
	cause: StepBackCause | null | undefined = 'input'
): string {
	const why = cause === 'others' ? 'other programs are busy' : "it's in use";
	return `Using ${shareWords(percent ?? DEFAULT_SHARE)} of this device while ${why}`;
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
	return fullAmountState(imports.page, isAdmin);
}

/** `usingShare` for a queue page: the share it names, or the default where it names none. */
export function usingShareOf(page: StepBackFacts | null | undefined): string {
	return usingShare(page?.step_back_share, page?.step_back_for);
}

/** The share the step back keeps to, as the queue page the shell keeps says it. */
export function currentShare(): number {
	return imports.page?.step_back_share ?? DEFAULT_SHARE;
}

/** Why the work steps back now, as the queue page the shell keeps says it. */
function currentCause(): StepBackCause | null {
	return imports.page?.step_back_for ?? null;
}

/** The state's first sentence: what is happening. */
export function fullAmountSays(
	state: Exclude<FullAmountState, null>,
	share: number,
	cause: StepBackCause | null = currentCause()
): string {
	return state === 'less' ? usingShare(share, cause) : FULL_AMOUNT_COPY.full;
}

/** The tooltip's two sentences for a state: what is happening, then what a press does. */
export function fullAmountTip(
	state: Exclude<FullAmountState, null>,
	share: number = currentShare(),
	cause: StepBackCause | null = currentCause()
): string {
	const press = state === 'less' ? FULL_AMOUNT_COPY.lessPress : FULL_AMOUNT_COPY.fullPress;
	return `${fullAmountSays(state, share, cause)}. ${press}`;
}

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
