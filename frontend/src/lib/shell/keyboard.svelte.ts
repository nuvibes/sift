/*
 * The on-screen keyboard, read as a focused field plus a viewport a keyboard's height shorter than
 * its tallest at this width. Then the tab bar stands down and the field is scrolled into view
 * inside its own box. A width change starts the tallest again.
 */

/** More than a browser bar folding away (about 50px); the shortest keyboard is about 200. */
const KEYBOARD_AT_LEAST = 150;

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

export function keyboardShows(height: number, tallest: number): boolean {
	return tallest - height >= KEYBOARD_AT_LEAST;
}

class Keyboard {
	up = $state(false);

	/** Returns the stop, for `onMount`; the keyboard can arrive after the focus or before it. */
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
			const moved = !was || height !== lastHeight;
			// jsdom has no `scrollIntoView`.
			if (this.up && moved && typeof focused?.scrollIntoView === 'function') {
				focused.scrollIntoView({ block: 'nearest', inline: 'nearest' });
			}
			lastHeight = height;
		};
		let lastHeight = heightNow();

		// Read after the focusout and the focusin both, or the bar flashes between two fields.
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
