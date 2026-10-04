/*
 * Which kind of window this page is, said to the server on every request.
 *
 * The server stamps a sitting, a search, a page visit and an act with the kind of window it came
 * from and the device it runs on, so Insights can one day say where a library is used. The device
 * is a cookie the server sets; the kind only the page knows, because it is the page's own decision:
 * the desktop app says it is the app, a phone is a phone because the page is drawn in the phone's
 * layout, and the Remote is a screen the page is showing. A user agent would be a guess that
 * disagrees with the screen somebody is looking at, so none is read.
 *
 * Kept in a module of its own and importing only the phone's width, because the API client reads
 * it on every request and must not pull in the layout's world to do it. The layout says which kind
 * it is (`setClientKind`, from `kindOf`) and the client reads it (`clientKind`).
 *
 * Until the layout has said, the page reads what it can of itself (`firstReading`): the requests a
 * page sends before its layout is drawn, who is signed in above all, would otherwise all be
 * "computer", and that one stamps the session of a browser signed in before devices were kept, so
 * a phone or the app would be written down as a computer for the whole of that session.
 */
import { PHONE_WIDTH } from '../components/common/phone-width.svelte';

/** The words the server knows (`kernel/client.py`). Anything else reads there as unknown. */
type ClientKind = 'app' | 'computer' | 'phone' | 'tablet' | 'remote';

/** The header the word travels in. */
export const CLIENT_HEADER = 'sift-client';

let kind: ClientKind | null = null;

/** The kind of window this page is, as the last thing to say so said it, or as the page reads
 *  itself before anything has. */
export function clientKind(): ClientKind {
	return kind ?? firstReading();
}

/* The same decision as the layout's, from what the window says without the layout: the desktop
   shell's own mark on the window (the bridge's `isDesktop`), the address, and the two media
   queries. Read each time until the layout speaks, because it is cheap and the window can still
   change under a page that has not drawn yet. */
function firstReading(): ClientKind {
	if (typeof window === 'undefined') return 'computer';
	const shell = (window as { sift?: { isDesktop?: boolean } }).sift;
	const query = (media: string) => typeof matchMedia === 'function' && matchMedia(media).matches;
	return kindOf({
		desktop: shell?.isDesktop === true,
		route: window.location.pathname === '/remote' ? '/remote' : null,
		phone: query(PHONE_WIDTH),
		touchOnly: query('(pointer: coarse)') && !query('(any-pointer: fine)')
	});
}

/** Say which kind of window this page is. */
export function setClientKind(next: ClientKind): void {
	kind = next;
}

/** What the page knows about itself, for `kindOf`. */
export interface Window {
	/** The desktop app's own shell is around this page (`bridge.isDesktop()`). */
	desktop: boolean;
	/** The route on screen, as SvelteKit names it. */
	route: string | null;
	/** The page is drawn in the phone's layout (`phoneWidth.yes`). */
	phone: boolean;
	/** The only pointer is a finger: a touch screen with no mouse or trackpad. */
	touchOnly: boolean;
}

/**
 * Which kind of window this is. The Remote first, because it is a screen a phone is being used
 * as; then the app, whatever its width; then the phone layout; then a wide window a finger drives,
 * which is a tablet; anything else is a browser on a computer.
 */
export function kindOf(window: Window): ClientKind {
	if (window.route === '/remote') return 'remote';
	if (window.desktop) return 'app';
	if (window.phone) return 'phone';
	if (window.touchOnly) return 'tablet';
	return 'computer';
}
