/*
 * A panel opened by pointing at its control goes away when the pointer leaves it, after a pick
 * inside it as well as before one.
 *
 * The library closes a hover-opened panel when the pointer leaves the control and the panel, but
 * only until something inside the panel is pressed. From the first press on it treats the panel as
 * one somebody asked for, and only a press outside or Escape puts it away. That is right for a
 * field somebody is typing into, and wrong for a chooser: picking a download folder from Add's
 * list is a press inside the panel, so the panel would stay up over the screen after the pointer
 * had gone, until somebody pressed somewhere to be rid of it.
 *
 * So the panel answers the pointer leaving on its own terms. It is held while:
 *
 * - its control was pressed to open it (a panel somebody asked for stays until they put it away,
 *   which is the library's rule and stays the rule);
 * - a field in it has the keyboard (somebody is typing a link, and the pointer drifting off must
 *   not throw the words away);
 * - the pointer is on the panel, on its control, or on a list opened from inside it (the
 *   download folder list is drawn at the end of the document, outside the panel's box).
 *
 * Otherwise a pointer that has left closes it after `LEAVE_MS`, the delay the library itself
 * closes with, so a pass over the gap between the control and the panel is not a leaving. Only a
 * moving pointer closes it: a pick that leaves the pointer resting outside the panel closes
 * nothing until the hand moves, as before the pick. Mouse only: a touch screen draws the panel as
 * a sheet and there is no hovering to answer.
 */

/** How long a pointer is away from the panel before it closes: the library's own close delay. */
export const LEAVE_MS = 120;

/** A list or menu drawn at the end of the document by a control inside the panel. */
const OPENED_FROM_INSIDE = '[role="listbox"], [role="menu"]';

/** What takes typing. A pressed button, a checkbox or a chooser's trigger does not hold the panel. */
const TYPED_INTO =
	'input:not([type="button"]):not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="file"]):not([type="submit"]), textarea, [contenteditable="true"]';

/** Whether the pointer at `target` is on the panel, on its control or on a list opened from it. */
export function onThePanel(
	target: EventTarget | null,
	panel: Element | null,
	trigger: Element | null
): boolean {
	if (!(target instanceof Node)) return false;
	if (panel?.contains(target) || trigger?.contains(target)) return true;
	return target instanceof Element && target.closest(OPENED_FROM_INSIDE) !== null;
}

/** Whether a field inside the panel has the keyboard. */
export function typingIn(panel: Element | null, active: Element | null): boolean {
	return panel !== null && active !== null && panel.contains(active) && active.matches(TYPED_INTO);
}

interface PointerLeave {
	/** Whether the panel is up. */
	open: () => boolean;
	/** Whether it was opened by pointing, the only way to open it that a leaving undoes. */
	hover: () => boolean;
	/** Whether its control was pressed: a panel somebody asked for. */
	asked: () => boolean;
	/** The panel's own box. */
	panel: () => Element | null;
	/** The control that opens it. */
	trigger: () => Element | null;
	/** Put it away. */
	close: () => void;
}

/** Call once in a component drawing a panel that opens on hover. Listens only while it is up. */
export function closesWhenThePointerLeaves(on: PointerLeave): void {
	$effect(() => {
		if (!on.open() || !on.hover() || on.asked()) return;
		const panel = on.panel();
		const doc = panel?.ownerDocument ?? (typeof document === 'undefined' ? null : document);
		if (!doc) return;
		let timer: ReturnType<typeof setTimeout> | null = null;
		const stop = (): void => {
			if (timer !== null) clearTimeout(timer);
			timer = null;
		};

		const moved = (event: PointerEvent): void => {
			if (event.pointerType !== 'mouse') return;
			const box = on.panel();
			if (onThePanel(event.target, box, on.trigger()) || typingIn(box, doc.activeElement)) {
				stop();
				return;
			}
			if (timer !== null) return;
			timer = setTimeout(() => {
				timer = null;
				if (!typingIn(on.panel(), doc.activeElement)) on.close();
			}, LEAVE_MS);
		};

		doc.addEventListener('pointermove', moved, true);
		return () => {
			doc.removeEventListener('pointermove', moved, true);
			stop();
		};
	});
}
