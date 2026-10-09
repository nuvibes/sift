/*
 * The folders Sift thinks it can name. Nothing here decides who may see anything: the server's rows
 * are already scoped. Yes is one request, so a half-applied answer is never on screen.
 */

import { asked, type PageAsk } from '$lib/grid/anchor';
import { api, type ApiPath } from '$lib/api/client';
import type { components } from '$lib/api/schema';

export type Evidence = 'face_group' | 'name_only' | 'filenames' | 'username_folder' | 'by_hand';

export type Proposal = components['schemas']['ProposalView'];

type ProposalPage = components['schemas']['ProposalList'];

type Confirmed = components['schemas']['ConfirmedView'];

/** The server's own default. */
export const SUGGESTIONS_PER_PAGE = 50;

export async function suggestions(
	page: PageAsk = { limit: SUGGESTIONS_PER_PAGE, offset: 0 }
): Promise<ProposalPage> {
	return api.get<ProposalPage>('/suggestions', { query: asked(page) });
}

/* `skip`: the odd files of a folder, or the names of a Site folder, left out of the attribution. */
export async function confirmSuggestion(id: string, skip: string[] = []): Promise<Confirmed> {
	return api.post<Confirmed>(`/suggestions/${encodeURIComponent(id)}/confirm`, { body: { skip } });
}

export type SetAside = components['schemas']['FolderSetAside'];

export async function rejectSuggestion(id: string): Promise<SetAside> {
	return api.post<SetAside>(`/suggestions/${encodeURIComponent(id)}/reject`, {});
}

/* Who a folder really is, said by a person: the only correction Sift can learn a MISS from. */
export async function sayWhoAFolderIs(
	folderId: string,
	name: string,
	kind: 'person' | 'site' = 'person'
): Promise<Confirmed> {
	return api.post<Confirmed>(`/suggestions/folder/${encodeURIComponent(folderId)}`, {
		body: { name, kind }
	});
}

export type Filed = components['schemas']['FiledView'];

/** Work already done, apart from the questions: an empty question list alone looks broken. */
export async function filedWithoutAsking(): Promise<Filed[]> {
	const answer = await api.get<components['schemas']['FiledList']>('/suggestions/filed');
	return answer.filed;
}

export type FilenameGroup = components['schemas']['FilenameGroupView'];

export type FiledFromName = components['schemas']['FiledFromNameView'];

/** Paged by ACCOUNT, so a page never cuts a group in half. */
export async function filingsFromFilenames(
	query: PageAsk
): Promise<{ groups: FilenameGroup[]; total: number; offset: number }> {
	const answer = await api.get<components['schemas']['FilenameFilingList']>(
		'/suggestions/filenames',
		{ query: asked(query) }
	);
	return { groups: answer.groups, total: answer.total, offset: answer.offset };
}

export type FolderTakenBack = components['schemas']['FolderTakenBack'];

/* Take back a folder filed without asking; one decision, one Undo. */
export async function takeBackFolder(folderId: string, personId: string): Promise<FolderTakenBack> {
	const path =
		`/suggestions/filed/${encodeURIComponent(folderId)}/people/${encodeURIComponent(personId)}/undo` as ApiPath;
	return api.post<FolderTakenBack>(path, {});
}

/* No on a whole username's row: one decision, one Undo. */
export async function takeBackUsername(
	usernameId: string
): Promise<{ files: number; decision_id: string }> {
	return api.post<components['schemas']['UsernameTakenBack']>(
		`/suggestions/filenames/${encodeURIComponent(usernameId)}/undo`,
		{}
	);
}
