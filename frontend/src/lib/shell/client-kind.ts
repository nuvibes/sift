/*
 * Which kind of window this page is, sent on every request for Insights. The page decides, never a
 * user agent. Imports only the phone's width: the API client reads it on every request.
 */
import { PHONE_WIDTH } from '../components/common/phone-width.svelte';

/** The server's words (`kernel/client.py`). */
type ClientKind = 'app' | 'computer' | 'phone' | 'tablet' | 'remote';

export const CLIENT_HEADER = 'sift-client';

let kind: ClientKind | null = null;

export function clientKind(): ClientKind {
	return kind ?? firstReading();
}

/* The layout's decision, read off the window until the layout has spoken. */
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

export function setClientKind(next: ClientKind): void {
	kind = next;
}

export interface Window {
	desktop: boolean;
	route: string | null;
	phone: boolean;
	touchOnly: boolean;
}

/** The Remote first, then the app, the phone layout, a tablet; else a computer. */
export function kindOf(window: Window): ClientKind {
	if (window.route === '/remote') return 'remote';
	if (window.desktop) return 'app';
	if (window.phone) return 'phone';
	if (window.touchOnly) return 'tablet';
	return 'computer';
}
