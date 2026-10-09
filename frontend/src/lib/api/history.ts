/* Asking the server what happened to one thing, and taking one of those things back. */
import { api } from './client';
import type { HistoryEvent } from '$lib/components/common/history';
import type { components } from './schema';

/** One page of a file's History and how many lines there are in all: the server's `FileHistoryPage`. */
export type FileHistoryPage = components['schemas']['FileHistoryPage'];

/** What happened to a file, oldest first, with the total the tab wears. */
export async function historyOfAsset(assetId: string, limit?: number): Promise<FileHistoryPage> {
	return api.get<FileHistoryPage>(`/assets/${encodeURIComponent(assetId)}/history`, {
		query: { limit }
	});
}

/** What happened to a person, oldest first. The same shape and the same bounds as a file's. */
export async function historyOfPerson(personId: string, limit?: number): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/people/${encodeURIComponent(personId)}/history`, {
		query: { limit }
	});
}

/** What happened to one site, oldest first. */
export async function historyOfSite(siteId: string, limit?: number): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/sites/${encodeURIComponent(siteId)}/history`, {
		query: { limit }
	});
}

/** What happened to one tag, oldest first. Its arrival, the files it has been put on (counted per
 * day and per whatever decided it) and a stash-box link. */
export async function historyOfTag(tagId: string, limit?: number): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/tags/${encodeURIComponent(tagId)}/history`, {
		query: { limit }
	});
}

/** What happened to one collection, oldest first. */
export async function historyOfCollection(
	collectionId: string,
	limit?: number
): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/collections/${encodeURIComponent(collectionId)}/history`, {
		query: { limit }
	});
}

/** What happened to one Photo Set, oldest first. */
export async function historyOfPhotoSet(
	photoSetId: string,
	limit?: number
): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/photo-sets/${encodeURIComponent(photoSetId)}/history`, {
		query: { limit }
	});
}

/** What happened to one song, oldest first. */
export async function historyOfSong(songId: string, limit?: number): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/songs/${encodeURIComponent(songId)}/history`, {
		query: { limit }
	});
}

/** Take one workbench decision back, by its own id. */
export async function undoDecision(
	decisionId: string
): Promise<components['schemas']['UndoneView']> {
	return api.post<components['schemas']['UndoneView']>(
		`/workbench/decisions/${encodeURIComponent(decisionId)}/undo`,
		{}
	);
}

/** Take one event back, through whichever door recorded it. */
export async function undoHistoryEvent(event: HistoryEvent): Promise<void> {
	if (event.undo) await undoAt(event.undo);
}

/** Take one act back through its door. */
export async function undoAt(door: NonNullable<HistoryEvent['undo']>): Promise<void> {
	if (door.kind === 'move') {
		await api.post(`/moves/${encodeURIComponent(door.id)}/undo`, {});
		return;
	}
	await undoDecision(door.id);
}
