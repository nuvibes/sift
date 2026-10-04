/*
 * The menu's touch path: a hold selects, and never opens the menu.
 *
 * Two ways a touch screen asks for a menu, and both are answered here so `ContextMenu` holds no
 * rule of its own about pointers.
 *
 * - The library's trigger starts a 700 ms timer on any `pointerdown` that is not a mouse's and
 *   opens the menu when it runs out. That handler is handed on for a mouse only.
 * - A phone's browser raises a `contextmenu` event of its own from a long press (Chrome on
 *   Android; Safari on an iPhone does not). The library opens on it exactly as it does for a right
 *   click. It is refused when it came from a finger, or from a pen held down with its tip, and
 *   refusing it (`preventDefault`) is also what keeps the browser's own menu off whatever is being
 *   picked.
 *
 * A pen's barrel button is a right click and is left alone: it raises `contextmenu` with a pen's
 * pointer type too, which is why a pen is judged by the PRESS in progress (the tip is button 0, the
 * barrel is not) rather than by the event's own type. A finger has no right button, so a finger's
 * `contextmenu` is always a hold. The menu key and Shift+F10 raise one with no pointer at all and no
 * press in progress, and are handed on.
 */

/** A handler the library put on its trigger, called only if it is one. */
export function handOn(handler: unknown, event: Event): void {
	if (typeof handler === 'function') handler(event);
}

/**
 * Whether the library may see this `pointerdown`.
 *
 * A mouse only: for anything else its handler's whole job is the hold timer. Handing on the mouse's
 * (which it ignores today) keeps whatever it adds for a mouse later.
 */
export function libraryMaySee(event: PointerEvent): boolean {
	return event.pointerType === 'mouse';
}

/**
 * Keep the focus where it is when a control is pressed with a mouse or a pen: refusing the press on
 * the way down stops focus moving without stopping the click, so no accent ring lights after a
 * press, and Tab still reaches the control and shows its ring there.
 *
 * Never a finger's. A touch lights no ring anyway, and WebKit (Safari, and every browser on an
 * iPhone) takes a touch refused on the way down as a tap that never happened and raises no click
 * for it: a control that refused it could not be pressed at all.
 */
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

/**
 * Watch the presses on one trigger.
 *
 * The press ends on the WINDOW's `pointerup` or `pointercancel`, not the trigger's: a finger that
 * wandered off lets go somewhere else, and a press remembered past its end would refuse the next
 * `contextmenu` from the keyboard.
 */
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

/**
 * Whether this `contextmenu` came from a hold, and so must not open the menu. Refuses it when so.
 *
 * `pointerType` is on the event where the engine makes `contextmenu` a pointer event (Chromium
 * does); where it does not, the press in progress answers.
 */
export function refusesTheHold(event: MouseEvent, pressKind: string | null): boolean {
	const said = 'pointerType' in event ? (event as PointerEvent).pointerType : '';
	const hold =
		said === 'touch' || (pressKind !== null && (pressKind === 'touch' || pressKind === 'pen'));
	if (hold) event.preventDefault();
	return hold;
}

/**
 * A sheet's guard against the tap that opened it.
 *
 * A menu door opens a touch press on the finger coming UP, and the browser then raises the `click`
 * that finishes the tap where the finger was, after the sheet has been drawn there. A dropdown hung
 * under its button is not under the finger; a sheet from the foot of the screen is, often exactly,
 * so the tap that asked for the menu also chose whichever row landed under it: More pressed over
 * picked files could run a sharing verb on all of them.
 *
 * So a sheet ignores a pointer's click until a pointer has come DOWN inside it. A click with no
 * pointer behind it (`detail` 0: Enter and Space on a row, which the library turns into a click) is
 * never held back, so the keyboard is untouched.
 *
 * A mouse or a pen opens the door on the way DOWN instead, so the sheet is under the pointer before
 * it comes up, and the library's row answers a `pointerup` it saw no `pointerdown` for (a drag
 * from the door onto a row) by calling the row's `click()` itself. That click has `detail` 0 and
 * would pass as the keyboard's, so the `pointerup` that ends a press begun outside the sheet is
 * held back too (marked handled, which the row checks first): a narrow desktop window, a pen on a
 * phone, a tablet's trackpad.
 */
interface SheetPresses {
	/** Call whenever the menu opens or closes: nothing has been pressed in the next sheet yet. On
	 *  closing as well as opening, because a door opened through its bound `open` tells nobody. */
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
