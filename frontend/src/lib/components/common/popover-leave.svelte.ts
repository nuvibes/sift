/* A hover-opened panel closes when the pointer leaves, even after a pick inside it (the library
 * stops after the first press). Held while its control was pressed, a field in it has the keyboard
 * or the pointer is on it, its control or a list it opened. Mouse only. */

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
	panel: () => Element | null;
	trigger: () => Element | null;
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
