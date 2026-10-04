// SPDX-License-Identifier: AGPL-3.0-or-later
/* Groups that look like somebody held as facial fingerprints: which, their question, the Yes. */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { heldFaces } from '$lib/people/thin-fingerprints';

/* A group's question: where they came from and how many faces. "Them": no pronoun is stored. */
export function fingerprintQuestion(offer: Asked): string {
	if (!offer.source) {
		return `This group looks like ${offer.name}, from a facial fingerprints file. Create a person for them?`;
	}
	const held = offer.faces ? ` (${heldFaces(offer.faces, offer.confirmed)})` : '';
	return `This group looks like ${offer.name}, from ${offer.source}${held}. Create a person for them?`;
}

/** A group that looks like somebody a facial fingerprints file holds, and who that is. */
export type FingerprintOffer = components['schemas']['FingerprintOfferView'];
type Asked = Pick<FingerprintOffer, 'name'> & Partial<FingerprintOffer>;

/* The groups that look like somebody a facial fingerprints file holds, keyed by the group.
 *
 * The server answers only while making people from fingerprints is off, and only to somebody who
 * may make people, so an empty map is the ordinary answer and a refusal reads as one: a card with
 * no offer asks its ordinary question. */
export async function fingerprintOffers(): Promise<Map<string, FingerprintOffer>> {
	try {
		const answer = await api.get<components['schemas']['FingerprintOffers']>(
			'/faces/fingerprints/offers'
		);
		return new Map(answer.items.map((offer) => [offer.pile_id, offer]));
	} catch {
		return new Map();
	}
}

/** Everybody a facial fingerprints file or a folder brought whom no face here matches yet. */
export type WaitingFingerprints = components['schemas']['WaitingFingerprints'];

/* Everybody held and matched by no face yet, newest first. Admin only, as the pane is. */
export async function waitingFingerprints(): Promise<WaitingFingerprints> {
	return api.get<WaitingFingerprints>('/faces/fingerprints/waiting');
}

/* Remove a held entry and the faces it brought: Sift stops trying to recognize them. */
export async function removeWaitingFingerprints(entryId: string): Promise<void> {
	await api.del(`/faces/fingerprints/waiting/${encodeURIComponent(entryId)}`);
}

/* The Yes to a group's question: the person is made with the file's faces, and the library is
   matched again so the faces that look like them are named. Undone from History. */
export async function makePersonFromFingerprints(entryId: string): Promise<string> {
	const made = await api.post<components['schemas']['MadeFromFingerprints']>(
		`/faces/fingerprints/${encodeURIComponent(entryId)}/person`
	);
	return made.person_id;
}
