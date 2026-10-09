import { goto, pushState, replaceState } from '$app/navigation';
import { page } from '$app/state';
import { MOBILE_TABS } from '$lib/components/shell/nav';
import { revealSetting } from '$lib/settings-ui/settings-anchor.svelte';
import { session } from '$lib/shell/session.svelte';
import { firstSectionFor, resolveAddress, settingsPath } from '$lib/settings-ui/sections';

/* Opening settings, and the one rule about how. */

/** Where the list of Settings sections is on a phone: the More screen. */
export const SETTINGS_LIST_ON_A_PHONE: { href: string; label: string } = (() => {
	const more = MOBILE_TABS.find((tab) => tab.id === 'more');
	if (!more) throw new Error('settings-view: the phone bar has no More tab');
	return { href: more.href, label: more.label };
})();

/** The width the phone layout begins below, the same one the tab bar and the rail change at. */
const PHONE = '(max-width: 767px)';

function onAPhone(): boolean {
	return typeof matchMedia === 'function' && matchMedia(PHONE).matches;
}

interface SettingsModalState {
	settings: string;
	/** Set when this address was landed on cold. See `App.PageState`. */
	direct?: boolean;
}

/** Open settings over the current page, at one section, or at one SETTING. */
export function openSettings(section: string, key?: string, show?: string): void {
	const to = resolveAddress(section, key, show);
	// Changes the address without running the settings route's load, so the page underneath stays
	// mounted and keeps its scroll.
	pushState(settingsPath(to), {
		settings: to.section
	} satisfies SettingsModalState);
	if (to.key) void revealSetting(to.key, to.section);
}

/** Move to another section within an open panel, or to one SETTING on it. */
export function showSettingsSection(section: string, key?: string, show?: string): void {
	const to = resolveAddress(section, key, show);
	// Carried forward, for the reason `showAsset` carries it: walking between sections does not put
	// a screen behind the panel.
	replaceState(settingsPath(to), {
		settings: to.section,
		direct: page.state.direct
	} satisfies SettingsModalState);
	if (to.key) void revealSetting(to.key, to.section);
}

/** Open the section this address names, with nothing behind it. */
export function enterSettings(section: string, key?: string, show?: string): void {
	/* An address naming no section, on a phone, is the list: the same place the More tab opens. */
	if (onAPhone() && page.url.pathname === '/settings' && key === undefined && show === undefined) {
		void goto(SETTINGS_LIST_ON_A_PHONE.href, { replaceState: true });
		return;
	}
	const to = resolveAddress(section, key, show);
	/* The address the person typed or bookmarked is kept when it is already the right one, and
	   REWRITTEN when it was an old one, so a refreshed or copied address after that is the
	   current one, and a tab the address implies is in it for the screen to read. */
	const moved = to.section !== section || to.key !== key || to.show !== show;
	replaceState(moved ? settingsPath(to) : window.location.href, {
		settings: to.section,
		direct: true
	} satisfies SettingsModalState);
	if (to.key) void revealSetting(to.key, to.section);
}

/** Turn a click on a settings link into the panel, and leave every other kind of click alone. */
export function openSettingsInstead(
	event: MouseEvent,
	section = firstSectionFor(session.isAdmin),
	key?: string,
	show?: string
): void {
	if (event.defaultPrevented) return;
	if (event.button !== 0) return;
	if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
	event.preventDefault();
	openSettings(section, key, show);
}
