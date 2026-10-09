/* The Disagreements tab gathered by person: who the rows are about, one person's page of them,
 * and the Yes or No over a run of hers. */
import { ApiError, api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** One person the tab is about: how many of her files, the word for where most came from, and
 * that said in full with the folders it came from (`filed`, its names in `filed_links`), the
 * server's sentence drawn as it comes. */
export type DisagreeingPerson = components['schemas']['DisagreeingPersonView'];

/** One of her files, as the tab draws it: the file's id, and its one face. */
export type Disagreement = components['schemas']['ToCheckCard'];

/** Which of hers an answer is about: the page on screen, the ones picked, or all of hers. */
export type DisagreementScope = components['schemas']['RunScope'];

/** How many of her faces one page holds: a wall of crops, as her own review screen draws them. */
export const DISAGREEMENTS_PER_PAGE = 60;

/* A tab with recognition switched off is a tab with nothing on it, which is what the other faces
   reads answer too; anything else is a fault the screen says. */
async function quietly<T>(work: Promise<T>, fallback: T): Promise<T> {
	try {
		return await work;
	} catch (error) {
		if (error instanceof ApiError && error.status === 409) return fallback;
		throw error;
	}
}

/** Everybody the tab is about, the most files first, and the tab's own total. */
export async function disagreeingPeople(): Promise<{
	people: DisagreeingPerson[];
	total: number;
}> {
	return quietly(api.get<components['schemas']['DisagreeingPeople']>('/faces/disagreements'), {
		people: [],
		total: 0
	});
}

/** One page of one person's files, the faces that look most like her first. */
export async function disagreementsOf(
	personId: string,
	page: { limit: number; offset: number }
): Promise<{ items: Disagreement[]; total: number; offset: number }> {
	return quietly(
		api.get<components['schemas']['ToCheckPage']>(
			`/faces/disagreements/${encodeURIComponent(personId)}`,
			{ query: page }
		),
		{ items: [], total: 0, offset: 0, small_groups: 0 }
	);
}

/** Yes (the face is her) or No (take her off the file) over some of hers. */
export async function answerDisagreements(
	personId: string,
	yes: boolean,
	scope: DisagreementScope,
	assetIds: readonly string[] = []
): Promise<components['schemas']['FacesDecided']> {
	return api.post<components['schemas']['FacesDecided']>(
		`/faces/disagreements/${encodeURIComponent(personId)}`,
		{
			body: scope === 'all' ? { yes, scope } : { yes, scope, asset_ids: [...assetIds] }
		}
	);
}

/** Where the name on her files came from, in words, for the line under her name. */
export function filedFrom(source: string | null | undefined): string {
	if (source === 'folder') return 'Added from a folder name';
	if (source === 'stash_box') return 'Added by a stash-box';
	if (source === 'filename') return 'Added from the filename';
	if (source === 'metadata') return "Added from the file's details";
	if (source === 'watermark') return 'Added from a watermark';
	return 'Added by Sift';
}
