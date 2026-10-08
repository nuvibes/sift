/*
 * The year's own cards on the client: which kinds draw a picture of their own, and the two reads
 * a deck makes beside the recap (how its longest session went, and what Keep as Collections would
 * keep).
 */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

export type SessionPath = components['schemas']['SessionPath'];
export type KeepSheet = components['schemas']['KeepSheet'];
export type KeptList = components['schemas']['KeptList'];
type Created = components['schemas']['CollectionSummary'];

/** The pages of the recap's longest session, named for the reader now. */
export function readSession(recapId: string): Promise<SessionPath> {
	return api.get<SessionPath>(`/insights/recaps/${encodeURIComponent(recapId)}/session`);
}

/** The Collections a year's recap can be kept as, and the ones already kept. */
export function readKeep(recapId: string): Promise<KeepSheet> {
	return api.get<KeepSheet>(`/insights/recaps/${encodeURIComponent(recapId)}/keep`);
}

/**
 * Keep the ticked lists as Collections, through the Collections routes themselves: each one
 * created, then filled. A list already kept is never created again. Answers the Collections now
 * kept, by name, each with its id.
 */
export async function keepAsCollections(
	lists: readonly KeptList[]
): Promise<{ id: string; name: string }[]> {
	const kept: { id: string; name: string }[] = [];
	for (const list of lists) {
		if (list.kept) continue;
		const made = await api.post<Created>('/collections', {
			body: { name: list.name }
		});
		await api.post(`/collections/${encodeURIComponent(made.id)}/items`, {
			body: { asset_ids: list.asset_ids, action: 'add' }
		});
		kept.push({ id: made.id, name: made.name });
	}
	return kept;
}
