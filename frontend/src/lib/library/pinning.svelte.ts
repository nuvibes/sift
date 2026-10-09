/* Keeping a thing at the top of its wall. Named things AND files: a Person, a Site, a
 * collection, a tag, a photo set, and a file. */

import { api, type ApiPath } from '$lib/api/client';
import { announceRefusal, overChunks, type BulkWriteDone } from '$lib/library/bulk';
import type { components } from '$lib/api/schema';

/** The six walls a pin exists on, spelled as they are in the address. */
export type Pinnable = 'people' | 'sites' | 'collections' | 'tags' | 'photo-sets' | 'songs';

/** Put the new value onto whichever row the wall is holding for this id. */
type SettlePin = (id: string, pinned: boolean) => void;

/** Pin or unpin one thing. Answers what the server ended up holding. */
async function setPinned(kind: Pinnable, id: string, pinned: boolean): Promise<boolean> {
	const held = await api.put<components['schemas']['PinView']>(`/${kind}/${id}/pin` as ApiPath, {
		body: { pinned }
	});
	return held.pinned;
}

/** Pin or unpin one FILE. Answers the whole opinion the server ended up holding. */
export async function setFilePinned(
	id: string,
	pinned: boolean
): Promise<components['schemas']['AssetOpinion']> {
	return await api.put<components['schemas']['AssetOpinion']>(`/assets/${id}/pin` as ApiPath, {
		body: { pinned }
	});
}

/** Pin or unpin a whole SELECTION of files, in one request per five hundred of them. */
export async function setFilesPinned(
	ids: readonly string[],
	pinned: boolean
): Promise<BulkWriteDone> {
	return await overChunks(ids, (chunk) =>
		api.post<BulkWriteDone>('/assets/pin', { body: { asset_ids: chunk, pinned } })
	);
}

/** Pin or unpin a whole selection, putting every row back if any of them fails. */
export async function pinAll(
	kind: Pinnable,
	ids: string[],
	pinned: boolean,
	settle: SettlePin
): Promise<boolean> {
	for (const id of ids) settle(id, pinned);
	try {
		for (const id of ids) settle(id, await setPinned(kind, id, pinned));
		return true;
	} catch (caught) {
		for (const id of ids) settle(id, !pinned);
		announceRefusal(caught);
		return false;
	}
}

/** What the verb should offer over a set of rows: `true` only when every one of them is pinned. */
export function allPinned(pinnedOf: (boolean | undefined)[]): boolean {
	return pinnedOf.length > 0 && pinnedOf.every(Boolean);
}
