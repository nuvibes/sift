/* The menu's touch path: a hold selects and never opens the menu. A finger's `contextmenu`
 * is always a hold; a pen's is judged by the press in progress (the tip is button 0), so its
 * barrel button still right-clicks. The menu key and Shift+F10 are handed on. */

/** A handler the library put on its trigger, called only if it is one. */
export function handOn(handler: unknown, event: Event): void {
	if (typeof handler === 'function') handler(event);
}

/** Only a mouse's: for anything else the library's handler is the hold timer. */
export function libraryMaySee(event: PointerEvent): boolean {
	return event.pointerType === 'mouse';
}

/** Keep focus where it is on a mouse or pen press, so no ring lights. Never a finger's:
 * WebKit raises no click for a touch refused on the way down. */
export function keepFocusOnPress(event: PointerEvent): void {
	if (event.pointerType !== 'touch') event.preventDefault();
}

/** The press in progress on one trigger, watched until the pointer lets go anywhere. */
interface TouchPress {
	/** Call from the trigger's `pointerdown`. */
	down(event: PointerEvent): void;
	/** The pointer type of a primary press that has not ended, or null. */
	readonly kind: string | null;
}

/** Watch one trigger's presses; one ends on the window, where a wandering finger lets go. */
export function touchPress(): TouchPress {
	let kind: string | null = null;
	const ended = () => {
		kind = null;
		window.removeEventListener('pointerup', ended, true);
		window.removeEventListener('pointercancel', ended, true);
	};
	return {
		down(event) {
			ended();
			if (event.button !== 0) return;
			kind = event.pointerType || null;
			window.addEventListener('pointerup', ended, true);
			window.addEventListener('pointercancel', ended, true);
		},
		get kind() {
			return kind;
		}
	};
}

/** Whether this `contextmenu` came from a hold, and if so refuse it. */
export function refusesTheHold(event: MouseEvent, pressKind: string | null): boolean {
	const said = 'pointerType' in event ? (event as PointerEvent).pointerType : '';
	const hold =
		said === 'touch' || (pressKind !== null && (pressKind === 'touch' || pressKind === 'pen'));
	if (hold) event.preventDefault();
	return hold;
}

/** A sheet's guard against the tap that opened it, whose click lands on the row drawn under
 * the finger. Pointer clicks and pointerups count only after a press began inside the sheet;
 * the keyboard's clicks (`detail` 0, no press) always pass. */
interface SheetPresses {
	/** Call on every open and close; a door opened through its bound `open` tells nobody. */
	reset(): void;
	/** The sheet's own `pointerdown`, heard on the way down. */
	down(): void;
	/** The sheet's own `pointerup`, heard on the way down, before any row sees it. */
	up(event: PointerEvent): void;
	/** The sheet's own `click`, heard on the way down, before any row sees it. */
	click(event: MouseEvent): void;
}

export function sheetPresses(): SheetPresses {
	let pressedInside = false;
	return {
		reset() {
			pressedInside = false;
		},
		down() {
			pressedInside = true;
		},
		up(event) {
			if (!pressedInside) event.preventDefault();
		},
		click(event) {
			if (pressedInside || event.detail === 0) return;
			event.preventDefault();
			event.stopPropagation();
		}
	};
}
