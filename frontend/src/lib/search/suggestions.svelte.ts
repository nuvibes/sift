/* The folders Sift thinks it can put a name to, and the one action that answers one.
 *
 * **Nothing here decides who may see anything.** Every row arriving from the server has already
 * been resolved against whoever is asking: the count is how many files under that folder this
 * account may open, a folder they may not see never arrives, and no file they cannot see is
 * offered to be left out. Recomputing any of that here would be a second opinion about
 * concealment living in the browser, which is the one place it cannot be read.
 *
 * **Answering yes is one request on purpose.** It attributes every file in the folder, names the
 * face group, teaches Sift the folder's spelling as an also-known-as name, and links a handle
 * folder's account to the person. Splitting it into steps here would put a half-applied answer on
 * screen every time one of them failed.
 */

import { asked, type PageAsk } from '$lib/grid/anchor';
import { api, type ApiPath } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** Why a folder is being asked about. */
export type Evidence = 'face_group' | 'name_only' | 'filenames' | 'username_folder' | 'by_hand';

export type Proposal = components['schemas']['ProposalView'];

type ProposalPage = components['schemas']['ProposalList'];

type Confirmed = components['schemas']['ConfirmedView'];

/** How many rows one page carries. Matches the server's own default so the two agree. */
export const SUGGESTIONS_PER_PAGE = 50;

export async function suggestions(
	page: PageAsk = { limit: SUGGESTIONS_PER_PAGE, offset: 0 }
): Promise<ProposalPage> {
	return api.get<ProposalPage>('/suggestions', { query: asked(page) });
}

/* Yes.
 *
 * `skip` is whatever the row offered to leave out: the odd FILES for an ordinary folder, and the
 * NAMES for a Site one: a Site folder's people come one per file, so there
 * is nothing useful to untick a file by. Either way it is left out of the attribution and out of
 * nothing else.
 */
export async function confirmSuggestion(id: string, skip: string[] = []): Promise<Confirmed> {
	return api.post<Confirmed>(`/suggestions/${encodeURIComponent(id)}/confirm`, { body: { skip } });
}

/** Not a person. Remembered permanently, and by name rather than by folder. */
/** What setting a folder aside did, and the record it can be taken back from. */
export type SetAside = components['schemas']['FolderSetAside'];

export async function rejectSuggestion(id: string): Promise<SetAside> {
	return api.post<SetAside>(`/suggestions/${encodeURIComponent(id)}/reject`, {});
}

/* Who a folder really is, said by a person rather than worked out.
 *
 * The only correction Sift can learn a MISS from. Yes and "not a person" both answer a question it
 * asked, so between them they record every case it got RIGHT and every case it got WRONG about,
 * and nothing at all about the folders it read as somebody else, or never asked about. Those leave
 * no trace unless something writes one down as it happens.
 *
 * It answers exactly as a confirmation does, because on the server it is one.
 */
export async function sayWhoAFolderIs(
	folderId: string,
	name: string,
	kind: 'person' | 'site' = 'person'
): Promise<Confirmed> {
	return api.post<Confirmed>(`/suggestions/folder/${encodeURIComponent(folderId)}`, {
		body: { name, kind }
	});
}

/** One folder a pass filed under somebody without asking. */
export type Filed = components['schemas']['FiledView'];

/** What was filed without anybody being asked.
 *
 * A separate request from the questions, because they are opposite things: one is work outstanding
 * and this is work already done. An empty question list beside a record of what was answered
 * silently is the whole reason this exists: a screen that only ever says "nothing to answer"
 * cannot be told apart from one that is broken.
 */
export async function filedWithoutAsking(): Promise<Filed[]> {
	const answer = await api.get<components['schemas']['FiledList']>('/suggestions/filed');
	return answer.filed;
}

/** One account, and the files a pass filed under it from their own names. */
export type FilenameGroup = components['schemas']['FilenameGroupView'];

/** One of those files, with the decision that takes its filing back. */
export type FiledFromName = components['schemas']['FiledFromNameView'];

/**
 * One page of what the filename pass filed, grouped by the account it filed under.
 *
 * Paged by ACCOUNT rather than by file, because the account is what the pass decided: it read one
 * shape out of a filename and concluded these came off this handle on this site. A page numbered by
 * files would have a boundary in the middle of a group, which is the one place a reader checking a
 * sweep must not be cut off.
 */
export async function filingsFromFilenames(
	query: PageAsk
): Promise<{ groups: FilenameGroup[]; total: number; offset: number }> {
	const answer = await api.get<components['schemas']['FilenameFilingList']>(
		'/suggestions/filenames',
		{ query: asked(query) }
	);
	return { groups: answer.groups, total: answer.total, offset: answer.offset };
}

/** What taking one folder back did, and the record whose Undo puts it back. */
export type FolderTakenBack = components['schemas']['FolderTakenBack'];

/* Take back, on a row of Organize > Folders > Added without asking: the person comes off the files
   the folder pass put them on under that folder, the folder is no longer theirs, and Sift never
   adds it to them again without asking. One decision, whose Undo puts all of it back. */
export async function takeBackFolder(folderId: string, personId: string): Promise<FolderTakenBack> {
	// Cast until the published types are regenerated with this route.
	const path =
		`/suggestions/filed/${encodeURIComponent(folderId)}/people/${encodeURIComponent(personId)}/undo` as ApiPath;
	return api.post<FolderTakenBack>(path, {});
}

/* No on a whole username's row in Organize > Filenames: every file the name filed under it comes
   back as one decision, whose Undo writes each row back as it was. */
export async function takeBackUsername(
	usernameId: string
): Promise<{ files: number; decision_id: string }> {
	return api.post<components['schemas']['UsernameTakenBack']>(
		`/suggestions/filenames/${encodeURIComponent(usernameId)}/undo`,
		{}
	);
}
