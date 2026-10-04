// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * What a paste that lands on the downloads screen means.
 *
 * The screen exists to take a link, and somebody arriving with one in their clipboard should not
 * have to find the box first. Three rules decide it, and all three are here rather than in the
 * handler because each of them is a judgement that can be wrong in a way nobody would see:
 *
 *   - **A paste that landed in a box is that box's.** The search field is on this screen, and a
 *     screen that took every paste would empty somebody's search into the queue's paste box.
 *   - **A paste with no address in it is not for this screen.** Somebody copying a sentence to send
 *     to a friend should not find it sitting in the download box.
 *   - **What is already in the box is kept.** A paste that wiped a half-typed address would be the
 *     only destructive act on the screen, and it would happen by accident.
 *
 * Nothing here queues anything. Pasting is not agreeing: a screen that started a download from a
 * paste would fetch whatever was copied last by somebody who only meant to look at the queue.
 */

import { lines } from './queue.svelte';

/** Whether a pasted lump of text holds an address at all. */
export function holdsALink(text: string): boolean {
	return lines(text).some((one) => /^https?:\/\//i.test(one));
}

/**
 * Whether the screen may take a paste that landed here, or whether it belongs to a control.
 *
 * `closest`, not the element itself: a paste into a box lands on the box, but a paste into anything
 * rich enough to have children inside it lands on whichever child the cursor was in.
 */
export function forTheScreen(target: EventTarget | null): boolean {
	if (!(target instanceof HTMLElement)) return true;
	return target.closest('input, textarea') === null && !target.isContentEditable;
}

/** The box's new contents: what is in it, and the pasted link on a line of its own under it. */
export function intoBox(already: string, pasted: string): string {
	const kept = already.trim();
	const link = pasted.trim();
	return kept ? `${kept}\n${link}` : link;
}
