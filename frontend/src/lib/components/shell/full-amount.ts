// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * THE LEAF AND THE BOLT: eco mode, where background work keeps to a share of this device while
 * somebody is working, a video is playing or other programs are busy, and a press for the full
 * amount until Sift stops or the next press. Nothing is drawn while no task runs, outside eco mode,
 * or for a guest. Read from the queue's first page, which the shell already keeps current.
 */

import { api, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { imports } from '$lib/library/imports.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { siftElsewhere } from './sift-elsewhere.svelte';

/** Why the work steps back: somebody at this device, a video playing, or other programs busy. */
type StepBackCause = NonNullable<components['schemas']['JobsPage']['step_back_for']>;

/** What other programs keep busy: the CPU, the GPU, memory. */
type BusyWith = 'processor' | 'graphics' | 'memory';

/* All this reads of the queue page. A server that does not say the share reads as a quarter, and
   one that does not say why reads as somebody here. */
type StepBackFacts = Partial<
	Pick<
		components['schemas']['JobsPage'],
		| 'stepping_back'
		| 'full_amount'
		| 'counts'
		| 'step_back_share'
		| 'step_back_for'
		| 'step_back_over'
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

const BUSY_WORDS: Record<BusyWith, string> = {
	processor: 'the CPU',
	graphics: 'the GPU',
	memory: 'most of the memory'
};

/** The device eco mode is a share of: in the app's client mode, not the one it's on. */
function device(elsewhere: boolean): string {
	return elsewhere ? 'the device running Sift' : 'this device';
}

/** Why Sift is in eco mode: "you're working", "other programs are using the CPU and the GPU". */
export function ecoWhy(
	cause: StepBackCause | null | undefined,
	over: readonly BusyWith[] | null | undefined = [],
	elsewhere = false
): string {
	if (cause === 'playing') return 'a video is playing';
	if (cause !== 'others') return elsewhere ? "someone's working there" : "you're working";
	const named = (over ?? []).map((one) => BUSY_WORDS[one]).filter(Boolean);
	if (named.length === 0) return 'other programs are busy';
	const last = named.pop();
	return `other programs are using ${named.length > 0 ? `${named.join(', ')} and ` : ''}${last}`;
}

/** What the leaf's tooltip, a phone's row and the Activity line say in eco mode. */
export function usingShare(
	percent: number | null | undefined,
	cause: StepBackCause | null | undefined = 'input',
	over: readonly BusyWith[] | null | undefined = [],
	elsewhere = false
): string {
	return `In eco mode while ${ecoWhy(cause, over, elsewhere)}: using ${shareWords(percent ?? DEFAULT_SHARE)} of ${device(elsewhere)}`;
}

/** Which of the two is drawn, or neither. */
type FullAmountState = 'less' | 'full' | null;

/** The words the leaf and the bolt say, on the rail's tooltip and on a phone's More screen. */
export const FULL_AMOUNT_COPY = {
	/* The control's name, the same in both states; pressed or not is said by `aria-pressed`. */
	name: 'Use the full amount of this device',
	lessPress: 'Press to use the full amount.',
	full: (why: string, of = device(false)) =>
		`Out of eco mode: using the full amount of ${of} although ${why}`,
	fullPress: 'Press to go back to eco mode.',
	/* The press on a phone's row, where there is room for a verb. */
	useFull: 'Use the full amount',
	stepBack: 'Use eco mode',
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

/** The share the step back keeps to, as the queue page the shell keeps says it. */
export function currentShare(): number {
	return imports.page?.step_back_share ?? DEFAULT_SHARE;
}

/** Why Sift is in eco mode now, as the queue page the shell keeps says it. */
function currentCause(): StepBackCause | null {
	return imports.page?.step_back_for ?? null;
}

function currentOver(): BusyWith[] {
	return imports.page?.step_back_over ?? [];
}

/** Whether the leaf is dimmed: eco mode for other programs, not for somebody working. */
export function leafDimmed(
	state: FullAmountState,
	cause: StepBackCause | null = currentCause()
): boolean {
	return state === 'less' && cause === 'others';
}

/** The state's first sentence: what is happening. */
export function fullAmountSays(
	state: Exclude<FullAmountState, null>,
	share: number,
	cause: StepBackCause | null = currentCause(),
	over: readonly BusyWith[] = currentOver(),
	elsewhere: boolean = siftElsewhere.yes
): string {
	return state === 'less'
		? usingShare(share, cause, over, elsewhere)
		: FULL_AMOUNT_COPY.full(ecoWhy(cause, over, elsewhere), device(elsewhere));
}

/** The tooltip's two sentences for a state: what is happening, then what a press does. */
export function fullAmountTip(
	state: Exclude<FullAmountState, null>,
	share: number = currentShare(),
	cause: StepBackCause | null = currentCause(),
	over: readonly BusyWith[] = currentOver(),
	elsewhere: boolean = siftElsewhere.yes
): string {
	const press = state === 'less' ? FULL_AMOUNT_COPY.lessPress : FULL_AMOUNT_COPY.fullPress;
	return `${fullAmountSays(state, share, cause, over, elsewhere)}. ${press}`;
}

const PRESS = '/jobs/full-amount';

/**
 * Press for the full amount (`on`), or go back to eco mode.
 *
 * The press's own answer is drawn at once: a queue read can take seconds on a busy device, and
 * one asked while the queue keeps moving is joined to every read after it.
 */
export async function pressFullAmount(on: boolean): Promise<void> {
	let answer: components['schemas']['StepBack'];
	try {
		answer = await api.post<components['schemas']['StepBack']>(PRESS, { body: { on } });
	} catch (error) {
		toasts.show(error instanceof ApiError ? FULL_AMOUNT_COPY.failed : UNREACHABLE, {
			tone: 'error'
		});
		return;
	}
	if (imports.page !== null) {
		imports.page = {
			...imports.page,
			stepping_back: answer.stepping_back,
			full_amount: answer.full_amount
		};
	}
	void imports.refresh();
}
