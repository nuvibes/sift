/* THE PANES ABOUT THE COMPUTER SIFT RUNS ON, and what a phone's width gets of each. */
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
