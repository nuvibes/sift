// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A swap's receipt: what crossed, how each group was filed, what stayed behind and why, and the
 * way to take it back. Kept by each side when the swap ends, so History opens it later.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { counted, filesSaid } from '$lib/entity/entity-counts';
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

/* Under a minute is not worth a sentence of its own. */
const WORTH_SAYING_S = 60;

/**
 * The heavy number's context: "files landed, 7.2 GB, in 7 min". One clock: the time runs from the
 * first byte to the last, less the time cut off, the span the average is over; the size is the
 * files', the same as their rows'.
 */
export function contextWords(receipt: SwapReceipt): string {
	const verb = receipt.role === 'guest' ? 'landed' : 'sent';
	const noun = receipt.files === 1 ? 'file' : 'files';
	const bytes = size(receipt.bytes);
	const length = receipt.moving ?? receipt.seconds;
	const said = length === null ? null : sayDuration(length);
	const took = said ? `, in ${said}` : '';
	return `${noun} ${verb}${bytes ? `, ${bytes}` : ''}${took}`;
}

/** The wait before the first file, said apart: "Joined 3 min before the first file." */
export function waitWords(receipt: Pick<SwapReceipt, 'role' | 'waited'>): string | null {
	if (receipt.waited === null || receipt.waited < WORTH_SAYING_S) return null;
	const how = receipt.role === 'guest' ? 'Joined' : 'Started';
	return `${how} ${sayDuration(receipt.waited)} before the first file.`;
}

function averageWords(rate: number | null): string | null {
	const said = rate ? size(rate) : null;
	return said ? `average ${said}/s` : null;
}

/** The other way of an exchange, with its own average: "And 971 files sent the other way, 8.7 GB,
 * average 16.1 MB/s." */
export function backWords(receipt: SwapReceipt): string | null {
	if (!receipt.two_way) return null;
	const verb = receipt.role === 'guest' ? 'sent' : 'received';
	if (receipt.back_files === 0) return `Nothing ${verb} the other way.`;
	const parts = [`And ${filesSaid(receipt.back_files)} ${verb} the other way`];
	const bytes = size(receipt.back_bytes);
	if (bytes) parts.push(bytes);
	const average = averageWords(receipt.back_rate);
	if (average) parts.push(average);
	return `${parts.join(', ')}.`;
}

/** From whom and how: "From DESK-2F9A through NY-711, average 23.6 MB/s, one cut-off (12 min)". */
export function howWords(receipt: SwapReceipt): string {
	const whence = receipt.role === 'guest' ? 'From' : 'To';
	const parts = [
		`${whence} ${deviceWords(receipt)}${receipt.tunnel ? ` through ${receipt.tunnel}` : ''}`
	];
	const average = averageWords(receipt.rate);
	if (average) parts.push(average);
	const cut =
		receipt.cut_offs === 1
			? 'one cut-off'
			: receipt.cut_offs > 1
				? `${receipt.cut_offs.toLocaleString()} cut-offs`
				: null;
	const cutFor =
		receipt.cut_seconds >= WORTH_SAYING_S ? ` (${sayDuration(receipt.cut_seconds)})` : '';
	if (cut) parts.push(`${cut}${cutFor}`);
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
	if (receipt.not_kept > 0) {
		said.push(
			`${filesSaid(receipt.not_kept)} received and not kept: it ended before ${receipt.not_kept === 1 ? 'it was' : 'they were'} added`
		);
	}
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

/** What the Undo took away, said on the card after it, with what it left and why. */
export function undoneWords(undo: ReceiptUndo): string {
	const disk = undo.from_disk ? ' and deleted from disk' : '. They stay on disk';
	const made = madeWords(undo.people, undo.sites);
	const also = made ? ` Removed with them: ${made} it created.` : '';
	const kept = undo.kept > 0 ? ` ${counted(undo.kept)} kept: already in the library.` : '';
	const inUse = undo.in_use > 0 ? ` ${counted(undo.in_use)} still in use: Undo again later.` : '';
	return `Undone: ${filesSaid(undo.files)} removed from the library${disk}.${kept}${inUse}${also}`;
}

/** The question the Undo asks first, naming the count and where the files go. */
export function undoQuestion(receipt: SwapReceipt): { title: string; consequence: string } {
	const noun = receipt.two_way ? 'exchange' : 'swap';
	// Again, after files were in use: only those are left to take back.
	const count = receipt.undo?.in_use || landedHere(receipt);
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
