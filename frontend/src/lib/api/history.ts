/*
 * Asking the server what happened to one thing, and taking one of those things back.
 *
 * Its own module rather than a call written into whichever screen draws it, because there is more
 * than one: a file, a person, a site, a tag, a shelf, a Photo Set and a song are addresses answering one
 * shape, and a second screen writing an address again is a second place to edit when it moves.
 *
 * It returns the wire shape unchanged. Every line in a history is BUILT by the server as pieces
 * (the actor first, each thing it names placed where it sits) by the one builder per act every
 * History screen uses, and `HistorySentence` draws them. There is nothing here to translate, and no
 * tooltip table either: what a line's mark means is the server's too (`means`).
 *
 * ## The undo lives here too
 *
 * An event that can be taken back carries the KIND of door as well as the id, because there are
 * two: a move goes through the organizer and a workbench decision through the workbench.
 * Several screens draw a history, so the dispatch is here: one place that knows which door each kind of
 * event opens.
 */
import { api } from './client';
import type { HistoryEvent } from '$lib/components/common/history';
import type { components } from './schema';

/** One page of a file's History and how many lines there are in all: the server's `FileHistoryPage`. */
export type FileHistoryPage = components['schemas']['FileHistoryPage'];

/**
 * What happened to a file, oldest first, with the total the tab wears.
 *
 * `limit` is bounded by the server: it refuses anything below one or above five hundred rather
 * than clamping, because a query parameter is something a caller wrote down. Left alone it answers
 * with the most recent fifty, which is why a history that is longer than that loses its beginning
 * rather than its end.
 */
export async function historyOfAsset(assetId: string, limit?: number): Promise<FileHistoryPage> {
	return api.get<FileHistoryPage>(`/assets/${encodeURIComponent(assetId)}/history`, {
		query: { limit }
	});
}

/**
 * What happened to a person, oldest first.
 *
 * The same shape and the same bounds as a file's. What is different is what is IN it: their
 * arrival, the files they were named on (counted per day and per whatever decided it, because
 * somebody on four thousand files is not four thousand lines anybody can read), the faces agreed
 * to and refused as them, and a stash-box link.
 */
export async function historyOfPerson(personId: string, limit?: number): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/people/${encodeURIComponent(personId)}/history`, {
		query: { limit }
	});
}

/**
 * What happened to one site, oldest first.
 *
 * Its arrival where the row recorded one (since v41 of the catalog), the usernames added to it, the
 * files filed under those usernames (counted per day and per task that decided it), a day's
 * downloads as one line, and a stash-box link.
 */
export async function historyOfSite(siteId: string, limit?: number): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/sites/${encodeURIComponent(siteId)}/history`, {
		query: { limit }
	});
}

/**
 * What happened to one tag, oldest first.
 *
 * Its arrival, the files it has been put on (counted per day and per whatever decided it) and a
 * stash-box link.
 */
export async function historyOfTag(tagId: string, limit?: number): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/tags/${encodeURIComponent(tagId)}/history`, {
		query: { limit }
	});
}

/**
 * What happened to one collection, oldest first.
 *
 * When it was created, every file added to it and removed from it (the ledger records both), and,
 * for an admin, who it has been shared with.
 */
export async function historyOfCollection(
	collectionId: string,
	limit?: number
): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/collections/${encodeURIComponent(collectionId)}/history`, {
		query: { limit }
	});
}

/**
 * What happened to one Photo Set, oldest first.
 *
 * What a collection's says, and one thing it has not got: the first line says HOW the set was
 * created: by hand, from a download, from a folder, from an archive.
 */
export async function historyOfPhotoSet(
	photoSetId: string,
	limit?: number
): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/photo-sets/${encodeURIComponent(photoSetId)}/history`, {
		query: { limit }
	});
}

/**
 * What happened to one song, oldest first.
 *
 * Its arrival (by hand, from AcoustID, from a download's page, or from the step that turned the
 * Music fields a library already had into songs), renames and merges, and every file it was named
 * on or taken off.
 */
export async function historyOfSong(songId: string, limit?: number): Promise<HistoryEvent[]> {
	return api.get<HistoryEvent[]>(`/songs/${encodeURIComponent(songId)}/history`, {
		query: { limit }
	});
}

/**
 * Take one workbench decision back, by its own id.
 *
 * Its own exported name because there are two readers now: the dispatch below, which is handed a
 * whole history event, and the feed in Settings, whose events ARE the decisions: a ledger row
 * with a receipt on it is a `workbench_decisions` row and its id is this id. The alternative was
 * the feed writing the address again, which is the second place to edit when it moves.
 */
export async function undoDecision(
	decisionId: string
): Promise<components['schemas']['UndoneView']> {
	return api.post<components['schemas']['UndoneView']>(
		`/workbench/decisions/${encodeURIComponent(decisionId)}/undo`,
		{}
	);
}

/**
 * Take one event back, through whichever door recorded it.
 *
 * The event says which: `move` is the organizer's and `decision` is the workbench's. A caller that
 * held only an id would have to work that out from the event's kind, which is a second mapping to
 * keep in step with the server's in a language that cannot check it.
 *
 * It does nothing for an event with no door (most of them) rather than refusing, so a screen
 * can hand this straight to the list without asking twice. It throws what the request threw: what
 * to SAY when an undo fails is the screen's, and the three that draw a history say it differently.
 */
export async function undoHistoryEvent(event: HistoryEvent): Promise<void> {
	if (event.undo) await undoAt(event.undo);
}

/**
 * Take one act back through its door. The door is the server's (`UndoPoint`): a move goes through
 * the organizer, a workbench decision through the workbench, and a caller holding only an id would
 * have to guess which. Shared by every place an Undo is offered (a History row, and the line
 * under a file's Music field), so there is one way back and not two.
 */
export async function undoAt(door: NonNullable<HistoryEvent['undo']>): Promise<void> {
	if (door.kind === 'move') {
		await api.post(`/moves/${encodeURIComponent(door.id)}/undo`, {});
		return;
	}
	await undoDecision(door.id);
}
