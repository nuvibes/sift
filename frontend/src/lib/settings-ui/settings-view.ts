import { goto, pushState, replaceState } from '$app/navigation';
import { page } from '$app/state';
import { MOBILE_TABS } from '$lib/components/shell/nav';
import { revealSetting } from '$lib/settings-ui/settings-anchor.svelte';
import { session } from '$lib/shell/session.svelte';
import { firstSectionFor, resolveAddress, settingsPath } from '$lib/settings-ui/sections';

/* Opening settings, and the one rule about how.
 *
 * `/settings/{section}` is a real address either way. What changes is where you came from:
 *
 *   - reached from inside the app -> it opens as a panel over whatever you were looking at, because
 *     changing a setting is something you do in the middle of something else, and coming back to a
 *     grid you had scrolled halfway down is the whole point
 *   - opened directly, or refreshed with it open -> the same panel, with nothing behind it, so
 *     closing goes to the library rather than stepping out of Sift
 *
 * The same URL and the same frame, both times. There is deliberately no page form.
 *
 * This is the pattern an asset already uses (see `$lib/player/asset-view`), and it lives here, in its
 * own file, for the same reason: so the several places that open settings all do it the one way.
 *
 * ## Every door goes through the one resolver
 *
 * A link, a search result, a bookmark and a refresh all arrive with an address that may be OLD: a
 * section that was folded into another, one that became a tab, a row that moved panes. Each of the
 * three functions below sends what it was given through `resolveAddress` before writing the address
 * or looking for the row, so the address bar says where the person actually is and the row that is
 * rung is the one that answers what they were sent for. See `sections.ts`.
 */

/**
 * Where the list of Settings sections is on a phone: the More screen.
 *
 * A phone has room for the list or a section, never both, so the list is a screen of its own and a
 * section opens over it. A section's way back on a phone names this, so Back lands on the list it
 * was opened from rather than on the first section again.
 */
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

/**
 * Open settings over the current page, at one section, or at one SETTING.
 *
 * Naming a key sends somebody to the row rather than to the pane it is somewhere on, which is the
 * difference between a link and a direction. The key goes in the address as a fragment, so the link
 * can be copied, opened in a tab and refreshed like any other.
 *
 * The reveal is fired from HERE rather than from the panel, and that is deliberate: the panel is
 * three components away, it is mounted by this call, and threading "which row" down through all
 * three would put a prop in two files that have no other reason to know what a setting key is.
 * `revealSetting` waits for the row itself. See that module for why it has to.
 */
export function openSettings(section: string, key?: string, show?: string): void {
	const to = resolveAddress(section, key, show);
	// Changes the address without running the settings route's load, so the page underneath stays
	// mounted and keeps its scroll. Back closes the panel rather than leaving the screen, because
	// this is a history entry like any other.
	pushState(settingsPath(to), {
		settings: to.section
	} satisfies SettingsModalState);
	if (to.key) void revealSetting(to.key, to.section);
}

/**
 * Move to another section within an open panel, or to one SETTING on it.
 *
 * `replaceState`, deliberately. Walking six sections and pressing Back should put somebody back
 * where they were before they opened settings, not six presses later, through every section they
 * glanced at on the way. The panel is one stop in the history; which section it is showing is not.
 *
 * ## Why the key comes in here
 *
 * A search result knows the row as well as the pane. Calling this and then `openSettings` would be
 * a replace followed by a PUSH: the panel would gain a second history entry, and closing it would
 * take two clicks outside it, because the first would go back to the panel it was already showing.
 *
 * So the key comes in here instead. The address carries it as a fragment, the same way
 * `openSettings` writes it, so a copied link still works, and the ring is fired from the same
 * place for the same reason it is fired there: the panel is three components away and threading
 * "which row" down to it would put a prop in two files that have no other reason to know what a
 * setting key is.
 */
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

/**
 * Open the section this address names, with nothing behind it.
 *
 * For the route, which runs only on a cold load or a refresh. There is no page form of settings:
 * one address that renders a panel or a full screen depending on how it was reached is two screens.
 */
export function enterSettings(section: string, key?: string, show?: string): void {
	/* An address naming no section, on a phone, is the list: the same place the More tab opens.
	   On a desktop the list is beside every section, so it opens on the first one. */
	if (onAPhone() && page.url.pathname === '/settings' && key === undefined && show === undefined) {
		void goto(SETTINGS_LIST_ON_A_PHONE.href, { replaceState: true });
		return;
	}
	const to = resolveAddress(section, key, show);
	/* The address the person typed or bookmarked is kept when it is already the right one, and
	   REWRITTEN when it was an old one, so a refreshed or copied address after that is the
	   current one, and a tab the address implies is in it for the screen to read.

	   KEPT WHOLE, FRAGMENT AND ALL. `''` for "this address" is a relative reference: resolved
	   against the page it keeps the path and the query and DROPS THE FRAGMENT, which is the row.
	   So `/settings/performance#performance.keeping_up` opened cold would ring its row and
	   then read `/settings/performance`, and a refresh would ring nothing. The asset route can
	   pass `''` because its address has no fragment to lose; this one does. */
	const moved = to.section !== section || to.key !== key || to.show !== show;
	replaceState(moved ? settingsPath(to) : window.location.href, {
		settings: to.section,
		direct: true
	} satisfies SettingsModalState);
	if (to.key) void revealSetting(to.key, to.section);
}

/**
 * Turn a click on a settings link into the panel, and leave every other kind of click alone.
 *
 * The links stay real anchors. That is the point of doing it this way rather than replacing them
 * with buttons: middle-click still opens a tab, ctrl-click still opens a tab, "copy link address"
 * still copies an address that works, and somebody with JavaScript failing still gets the page. A
 * button has none of that and looks identical.
 *
 * Only a plain left-click is taken, because every modifier means the person asked for something
 * else (a new tab, a new window, a download), and answering any of them with a panel in THIS
 * window is ignoring what they asked for.
 */
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
