/*
 * THE PANES ABOUT THE COMPUTER SIFT RUNS ON, and what a phone's width gets of each.
 *
 * Seven things in Settings are about that computer rather than about the library or the person
 * looking at it. Each was decided once, here, for a phone, and the reason is the decision:
 *
 * - Performance (with the graphics card on it): READ ONLY. What the computer is and how Sift is
 *   doing on it is worth reading anywhere; the self-test loads that computer for a minute, its
 *   answers change how hard Sift works it, and installing or removing a graphics card's pack
 *   restarts Sift, which drops the phone's own connection in the middle of the press.
 * - Backup and restore: READ ONLY. A backup saved to a phone puts the library's whole record (the
 *   names, the tags, the faces) in the phone's own storage, where Sift can never erase it, which is
 *   the same reason Sift keeps no offline copy on a phone; a restore replaces the library.
 * - Maintenance: READ ONLY. Every press on it runs for minutes against the library's database, and
 *   some cannot be stopped once started; it is started where the computer can be watched.
 * - Updates: not held here, because a browser can already only read it: the install press is the
 *   desktop application's (`bridge.canApplyUpdate`), and the licence and notices at its foot must
 *   stay reachable everywhere.
 * - Storage folders and Network sharing: already not offered in any browser, a phone included:
 *   both are the desktop application's (`bridge.canMoveStorage`, `bridge.canShareOnNetwork`), and
 *   switching sharing off from a phone would cut the phone off mid-press.
 *
 * READ ONLY means every row still says what it is set to, and nothing on the pane changes it: a
 * row's control is drawn disabled and a row whose control is a press draws no press. The pane says
 * so once, under its title. A link to another pane still goes there.
 */
import { getContext, setContext } from 'svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';

/** The sections a phone's width draws read only, by the section's id. */
export const READ_ONLY_ON_A_PHONE: ReadonlySet<string> = new Set([
	'performance',
	'backup',
	'maintenance'
]);

/** What a read-only pane says under its title. */
export const READ_ONLY_NOTE =
	'Read only on a phone. These are changed in Sift on the computer it runs on.';

const HELD = Symbol('settings-pane-held');

/** Whether a section is drawn read only now: on a phone's width, and one of the sections above. */
export function heldHere(section: string): boolean {
	return phoneWidth.yes && READ_ONLY_ON_A_PHONE.has(section);
}

/** Called by the pane for the section it shows, so every row inside can ask. */
export function holdPane(section: () => string): void {
	setContext(HELD, () => heldHere(section()));
}

/** Whether the pane this row is on is held read only. False outside Settings. */
export function paneHeld(): () => boolean {
	return getContext<(() => boolean) | undefined>(HELD) ?? (() => false);
}
