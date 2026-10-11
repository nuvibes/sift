/* A file stored in more than one place is renamed, moved, modified or compressed one copy at a
 * time: these are its copies, as the server lists them. */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** One place a file is stored; `reason` says why it cannot be renamed or moved, null when it can. */
export type FileCopy = components['schemas']['OrganizeCopy'];

/** Every place this file is stored, for an admin; one, or none, for a file that is not asked about. */
export async function copiesOf(assetId: string): Promise<FileCopy[]> {
	const options = await api.get<components['schemas']['OrganizeOptions']>(
		`/assets/${encodeURIComponent(assetId)}/organize`
	);
	return options.copies;
}

/** A copy's file name and the folder it is in, from its whole path in either separator. */
export function partsOf(path: string): { name: string; folder: string } {
	const cut = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
	return { name: path.slice(cut + 1), folder: cut > 0 ? path.slice(0, cut) : path };
}
