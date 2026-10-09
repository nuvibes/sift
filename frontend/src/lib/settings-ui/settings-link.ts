/* A link to a settings address, followed as the panel rather than as a page load. */
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
