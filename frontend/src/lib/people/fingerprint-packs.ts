// SPDX-License-Identifier: AGPL-3.0-or-later
/* A file of facial fingerprints: taking one in, and building one from People already here. */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

export type PackImported = components['schemas']['PackImported'];

/* Take in a pack of reference faces.
 *
 * Importing the same pack twice adds nothing the second time (every reference is keyed by the
 * identity of its picture), so this is safe to repeat without thinking about it.
 *
 * Nothing here decides who anybody is: the people the file names are held as facial fingerprints,
 * and the pass after it places each one by face, never by the name the file gives.
 */
export async function importPack(file: File): Promise<PackImported> {
	const form = new FormData();
	form.append('file', file);
	return api.post<PackImported>('/faces/packs/import', { body: form });
}

/* Build a pack from People already here and hand back the file to save.
 *
 * Pictures are opt-in. Without them a pack is numbers alone: far smaller, still able to recognize
 * everybody in it, and carrying no photographs of anybody off this machine.
 */
export async function exportPack(
	name: string,
	options: { personIds?: string[]; includePictures?: boolean } = {}
): Promise<Blob> {
	return api.postForFile('/faces/packs/export', {
		name,
		person_ids: options.personIds ?? [],
		include_pictures: options.includePictures ?? false
	});
}
