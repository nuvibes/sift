// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A person whose facial fingerprints are thin, said where they are chosen to leave this library:
 * the export's Choose people sheet and a swap's facial fingerprints sheet.
 *
 * Under twenty confirmed faces the person's own page draws its bar in the red, orange or yellow
 * band, and a file or a swap carrying them hands the other Sift the same few faces. The chooser
 * says so on the row, with the band the page draws and the count in words, so nobody sends three
 * faces believing they sent somebody Sift knows well.
 *
 * The band is the server's word (`KnownPerson.verdict`, the page's `Strength.verdict`), never
 * worked out here from the count: a copy of the five, ten and twenty would go on marking the
 * wrong people after the curve moved.
 */

import type { PickChoice } from '$lib/components/common/verbs';

/** The bands under twenty confirmed faces, the ones a chooser marks. */
const THIN = new Set(['weak', 'fair', 'good']);

/** What the row says: how many confirmed faces, and what that means for whoever takes them. */
export function thinSaid(confirmed: number): string {
	const faces = confirmed === 1 ? '1 confirmed face' : `${confirmed} confirmed faces`;
	return `Only ${faces}: Sift recognizes them less surely`;
}

/** A chooser's row for a known person, marked with the page's band where it is under twenty. */
export function thinChoice<T extends PickChoice>(
	choice: T,
	person: { faces: number; verdict?: string | null }
): T {
	const band = person.verdict ?? '';
	if (!THIN.has(band)) return choice;
	return { ...choice, strength: { band, said: thinSaid(person.faces) } };
}

/** How many faces a held entry brought, and how many confirmed faces the file said they had:
 *  "3 confirmed faces" where it gave all it had, "64 faces of 210 confirmed" where it gave the
 *  best of more, and "4 faces" where it said nothing (a folder, or an older file). */
export function heldFaces(faces: number, confirmed?: number | null): string {
	const counted = faces === 1 ? '1 face' : `${faces.toLocaleString()} faces`;
	if (confirmed === null || confirmed === undefined) return counted;
	if (confirmed === faces) {
		return faces === 1 ? '1 confirmed face' : `${faces.toLocaleString()} confirmed faces`;
	}
	return `${counted} of ${confirmed.toLocaleString()} confirmed`;
}
