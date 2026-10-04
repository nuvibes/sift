/*
 * The on-screen keyboard, as far as a page can see it.
 *
 * A phone's keyboard takes the lower half of the screen and says nothing about it. What the page
 * can read is the viewport: the visual viewport shrinks under the keyboard (iOS Safari, Chrome on
 * Android since 108), or the whole window does (an Android browser that resizes its content, an
 * embedded view, and Playwright's emulation). Either way the screen a person is typing on has
 * become shorter while a field has the focus, and that is the one reading here: a field is
 * focused, and the viewport is a keyboard's height shorter than the tallest it has been at this
 * width.
 *
 * Two things follow from it:
 *   - The tab bar stands down while the keyboard is up (the layout reads `keyboard.up`). On a
 *     window that shrinks, the bar rises with it and stands on the field being typed in (the
 *     Downloads paste box, under the pager and the tab bar). Every phone's own apps put their tab
 *     bar away while somebody types.
 *   - The focused field is brought into view inside whatever box scrolls it. A browser scrolls the
 *     page for a focused field, but not always the inner box a Sift screen scrolls in, and the
 *     keyboard arriving after the focus is a resize, not a focus, so a field near the foot of a
 *     screen (Profile's PIN) would be left under the keyboard.
 *
 * Width changes (a phone turned sideways) start the tallest again, since a landscape screen is
 * shorter without a keyboard in sight.
 */

/** How much shorter than its tallest the viewport must be to be a keyboard and not a browser's
 *  own bar folding away (Safari's address bar is about 50px; the shortest keyboard is about 200). */
const KEYBOARD_AT_LEAST = 150;

/** Whether this element takes typing: a text field, a text area, or something contenteditable. */
export function takesTyping(element: Element | null): boolean {
	if (!(element instanceof HTMLElement)) return false;
	if (element.isContentEditable) return true;
	if (element instanceof HTMLTextAreaElement) return !element.readOnly;
	if (element instanceof HTMLInputElement) {
		const kind = element.type;
		const typed = ['text', 'search', 'email', 'url', 'tel', 'password', 'number'];
		return typed.includes(kind) && !element.readOnly;
	}
	return false;
}

/** Whether a viewport this tall, at its tallest this width, leaves room for a keyboard's worth. */
export function keyboardShows(height: number, tallest: number): boolean {
	return tallest - height >= KEYBOARD_AT_LEAST;
}

class Keyboard {
	/** A field has the focus and the viewport has lost a keyboard's height. */
	up = $state(false);

	/**
	 * Start watching. Returns the stop, for `onMount`.
	 *
	 * Reads `visualViewport` where the browser has one and the window otherwise, and listens to
	 * both the viewport's resize and focus moving, since a keyboard can come up after the focus
	 * (the browser animates it in) or the focus can move between fields with it already up.
	 */
	watch(): () => void {
		const view = window.visualViewport;
		const heightNow = () => (view ? view.height : window.innerHeight);
		let width = window.innerWidth;
		let tallest = heightNow();

		const read = () => {
			if (window.innerWidth !== width) {
				width = window.innerWidth;
				tallest = heightNow();
			}
			const height = heightNow();
			if (height > tallest) tallest = height;
			const focused = document.activeElement;
			const was = this.up;
			this.up = takesTyping(focused) && keyboardShows(height, tallest);
			// Once, as it comes up, and again whenever the viewport moves under it: the field the
			// person is typing in stays in view inside whatever box scrolls it.
			const moved = !was || height !== lastHeight;
			// `scrollIntoView` asked for rather than assumed: jsdom, which the tests run in, has none.
			if (this.up && moved && typeof focused?.scrollIntoView === 'function') {
				focused.scrollIntoView({ block: 'nearest', inline: 'nearest' });
			}
			lastHeight = height;
		};
		let lastHeight = heightNow();

		// A focus moving from one field to the next is a focusout then a focusin; read after both, or
		// the tab bar would come back for one frame between two fields.
		const soon = () => setTimeout(read, 0);
		const target: EventTarget = view ?? window;
		target.addEventListener('resize', read);
		document.addEventListener('focusin', soon);
		document.addEventListener('focusout', soon);
		return () => {
			target.removeEventListener('resize', read);
			document.removeEventListener('focusin', soon);
			document.removeEventListener('focusout', soon);
			this.up = false;
		};
	}
}

export const keyboard = new Keyboard();
