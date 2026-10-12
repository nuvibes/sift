// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A swap's receipt: what crossed, how each group was filed, what stayed behind and why, and the
 * way to take it back. Kept by each side when the swap ends, so History opens it later.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { filesSaid } from '$lib/entity/entity-counts';
import { size } from '$lib/library/facts';
import { sayDuration } from '$lib/shell/duration';

export type SwapReceipt = components['schemas']['SwapReceipt'];
export type ReceiptGroup = components['schemas']['ReceiptGroup'];
export type ReceiptUndo = components['schemas']['ReceiptUndo'];
export type ReceiptSkipped = components['schemas']['ReceiptSkipped'];

/** What a group is filed under: People, Sites, Collections, Photo Sets, Songs, or other files. */
export type ReceiptKind = ReceiptGroup['kind'];

export function readReceipt(sessionId: string): Promise<SwapReceipt> {
	return api.get<SwapReceipt>(`/swap/receipts/${encodeURIComponent(sessionId)}`);
}

export function undoSwap(sessionId: string): Promise<SwapReceipt> {
	return api.post<SwapReceipt>(`/swap/receipts/${encodeURIComponent(sessionId)}/undo`);
}

/* A group the receipt cannot name: songs and other files have no one name, and a thing the
   reader may not be shown is said by its kind. */
const UNNAMED: Record<ReceiptKind, string> = {
	person: 'A person',
	site: 'A Site',
	collection: 'A Collection',
	photo_set: 'A Photo Set',
	songs: 'Songs',
	loose: 'Other files'
};

export function groupName(group: Pick<ReceiptGroup, 'kind' | 'name'>): string {
	return group.name ?? UNNAMED[group.kind];
}

/** The device by its name where one is known, else its id. */
export function deviceWords(receipt: Pick<SwapReceipt, 'device' | 'device_name'>): string {
	const id = receipt.device ?? 'another device';
	return receipt.device_name && receipt.device
		? `${receipt.device_name} (${receipt.device})`
		: (receipt.device_name ?? id);
}

/** The heavy number's context: "files landed, 30 GB, in 21 min". */
export function contextWords(receipt: SwapReceipt): string {
	const verb = receipt.role === 'guest' ? 'landed' : 'sent';
	const noun = receipt.files === 1 ? 'file' : 'files';
	const bytes = size(receipt.bytes);
	const said = receipt.seconds === null ? null : sayDuration(receipt.seconds);
	const took = said ? `, in ${said}` : '';
	return `${noun} ${verb}${bytes ? `, ${bytes}` : ''}${took}`;
}

/** The other way of an exchange, said once under the number: "and 38 files received, 1.2 GB". */
export function backWords(receipt: SwapReceipt): string | null {
	if (!receipt.two_way) return null;
	const verb = receipt.role === 'guest' ? 'sent' : 'received';
	if (receipt.back_files === 0) return `Nothing ${verb} the other way.`;
	const bytes = size(receipt.back_bytes);
	return `And ${filesSaid(receipt.back_files)} ${verb} the other way${bytes ? `, ${bytes}` : ''}.`;
}

/** From whom and how: "From DESK-2F9A through NY-711, average 23.6 MB/s, one cut-off". */
export function howWords(receipt: SwapReceipt): string {
	const whence = receipt.role === 'guest' ? 'From' : 'To';
	const parts = [
		`${whence} ${deviceWords(receipt)}${receipt.tunnel ? ` through ${receipt.tunnel}` : ''}`
	];
	const rate = receipt.rate ? size(receipt.rate) : null;
	if (rate) parts.push(`average ${rate}/s`);
	if (receipt.cut_offs === 1) parts.push('one cut-off');
	else if (receipt.cut_offs > 1) parts.push(`${receipt.cut_offs} cut-offs`);
	return parts.join(', ');
}

/** How one landed group was filed: "added to your Ava Example", "created, 9 faces learned". */
export function filedWords(group: ReceiptGroup): string {
	const said: string[] = [];
	if (group.way === 'sent') said.push('sent');
	else if (group.filed === 'created') said.push('created');
	else if (group.filed === 'added') said.push(`added to your ${groupName(group)}`);
	if (group.faces > 0)
		said.push(`${group.faces.toLocaleString()} ${group.faces === 1 ? 'face' : 'faces'} learned`);
	if (group.usernames > 0) {
		said.push(
			`${group.usernames.toLocaleString()} ${group.usernames === 1 ? 'username' : 'usernames'} filed`
		);
	}
	return said.join(', ');
}

/** What was not taken, and why, as one line of parts. Empty where everything offered came. */
export function notTakenWords(receipt: SwapReceipt): string[] {
	const said = receipt.skipped.map(
		(one) => `${groupName(one)} (${one.files.toLocaleString()}) skipped`
	);
	if (receipt.refused > 0) said.push(`${receipt.refused.toLocaleString()} refused on import`);
	if (receipt.held > 0) said.push(`${receipt.held.toLocaleString()} already here`);
	return said;
}

/** How many files landed on this side: the guest's, or what an exchange sent back to the host. */
function landedHere(receipt: SwapReceipt): number {
	return receipt.role === 'guest' ? receipt.files : receipt.back_files;
}

function madeWords(people: number, sites: number): string | null {
	const made: string[] = [];
	if (people > 0) made.push(people === 1 ? '1 person' : `${people.toLocaleString()} people`);
	if (sites > 0) made.push(sites === 1 ? '1 Site' : `${sites.toLocaleString()} Sites`);
	return made.length ? made.join(' and ') : null;
}

/** What the Undo took away, said on the card after it. */
export function undoneWords(undo: ReceiptUndo): string {
	const disk = undo.from_disk ? ' and deleted from disk' : '. They stay on disk';
	const made = madeWords(undo.people, undo.sites);
	const also = made ? ` Removed with them: ${made} it created.` : '';
	return `Undone: ${filesSaid(undo.files)} removed from the library${disk}.${also}`;
}

/** The question the Undo asks first, naming the count and where the files go. */
export function undoQuestion(receipt: SwapReceipt): { title: string; consequence: string } {
	const noun = receipt.two_way ? 'exchange' : 'swap';
	const count = landedHere(receipt);
	const one = count === 1;
	const disk = receipt.folder?.own
		? `${one ? 'It is' : 'They are'} deleted from disk in ${receipt.folder.path}.`
		: `${one ? 'It stays' : 'They stay'} on disk, outside the ${noun}'s own folder.`;
	return {
		title: `Undo this ${noun}?`,
		consequence: `${filesSaid(count)} ${one ? 'is' : 'are'} removed from the library, with the People and Sites the ${noun} created that nothing else uses. ${disk} This can't be undone.`
	};
}

/** The card's first word, by how the swap ended. */
export function headlineWords(receipt: Pick<SwapReceipt, 'reason' | 'two_way'>): string {
	const noun = receipt.two_way ? 'exchange' : 'swap';
	switch (receipt.reason) {
		case 'done':
			return 'Done';
		case 'ended by you':
			return `You ended the ${noun}`;
		case 'ended by them':
			return `They ended the ${noun}`;
		case 'lost':
			return 'The connection to them was lost';
		case 'disk full':
			return 'This device ran out of disk space';
		default:
			return `The ${noun} ended`;
	}
}
