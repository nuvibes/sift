// SPDX-License-Identifier: AGPL-3.0-or-later
import { goto } from '$app/navigation';

/** What a press on a card does: an address, or a call handed the press. */
export type CardOpens = string | ((event: MouseEvent) => void);

/* What answers a press of its own, so the card leaves the press to it. */
const ACTIONS =
	'a, button, input, select, textarea, label, summary, [role="button"], [role="menuitem"], [role="checkbox"], [contenteditable], [inert]';

/**
 * ONE RULE FOR A CARD THAT OPENS SOMETHING, as its `onclick`: a press anywhere on it that is not a
 * control or a link does what the card's activation does, as a press on a tile does. Where the
 * card holds a link to that address the press is handed to the link, so whatever the link does on
 * a press (picking, a new tab) the card does too. The keyboard's way in is the card's own link.
 */
export function pressOnCard(event: MouseEvent, opens: CardOpens | undefined): void {
	const card = event.currentTarget as HTMLElement | null;
	const target = event.target as Element | null;
	if (!opens || !card || event.defaultPrevented || event.button !== 0) return;
	if (target?.closest(ACTIONS)) return;
	// A drag across the words selects them; it is not a press.
	if (String(globalThis.getSelection?.() ?? '') !== '') return;
	if (typeof opens !== 'string') return opens(event);
	const link = [...card.querySelectorAll('a[href]')].find(
		(one) => one.getAttribute('href') === opens
	);
	if (link) link.dispatchEvent(new MouseEvent('click', event));
	else if (event.ctrlKey || event.metaKey || event.shiftKey) window.open(opens, '_blank');
	else void goto(opens);
}
