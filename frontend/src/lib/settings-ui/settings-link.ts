/*
 * A link to a settings address, followed as the panel rather than as a page load.
 *
 * Followed as an ordinary navigation from a settings address that was opened directly (the panel
 * drawn as a page: `/settings/get-to-know`, a History line's link), it changes only the route's
 * parameters, the panel fades out and the screen stands empty. So a plain click on a link into
 * `/settings/...` opens the panel at that section, row and tab (`openSettingsInstead`); every other
 * link, and any modified click, is left to the browser. One rule for every list of server-written
 * links that may point into Settings: a History line and a Get to know Sift step.
 */
import { openSettingsInstead } from '$lib/settings-ui/settings-view';

export function followSettingsLink(event: MouseEvent, href: string): void {
	const to = new URL(href, window.location.origin);
	const [first, section] = to.pathname.split('/').filter(Boolean);
	if (first !== 'settings' || !section) return;
	openSettingsInstead(
		event,
		section,
		to.hash.slice(1) || undefined,
		to.searchParams.get('show') ?? undefined
	);
}
