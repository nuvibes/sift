// SPDX-License-Identifier: AGPL-3.0-or-later
/* A file of facial fingerprints: taking one in, and building one from People already here. */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

export type PackImported = components['schemas']['PackImported'];

/* Take in a pack of reference faces. */
export async function importPack(file: File): Promise<PackImported> {
	const form = new FormData();
	form.append('file', file);
	return api.post<PackImported>('/faces/packs/import', { body: form });
}

/* Build the file from People here and people waiting for a matching face; neither list is
 * everybody. */
export async function exportPack(
	name: string,
	options: { personIds?: string[]; entryIds?: string[]; includePictures?: boolean } = {}
): Promise<Blob> {
	return api.postForFile('/faces/packs/export', {
		name,
		person_ids: options.personIds ?? [],
		entry_ids: options.entryIds ?? [],
		include_pictures: options.includePictures ?? false
	});
}
