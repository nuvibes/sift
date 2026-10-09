/* Linking one of Sift's own subjects to a stash-box, and reading back what was kept. */

import { api, ApiError } from '$lib/api/client';
import { dayOf } from '$lib/shell/when';
import { afterWords, madeIcon, madeLabel } from '$lib/components/shell/facet-labels';
import type { IconName } from '$lib/design/icons';
import type { BoxAnswer, FoundRecord } from '$lib/settings-ui/stash-boxes.svelte';
import type { components } from '$lib/api/schema';

/** The kinds of thing a stash-box can be asked about by name. */
export type LinkSubject = 'person' | 'site' | 'tag';

/** One stash-box's kept record of one subject. */
export type StashBoxLink = components['schemas']['sift__slices__stash_boxes__models__LinkView'];

function said(error: unknown): string {
	return error instanceof ApiError && error.detail ? error.detail : "That didn't work.";
}

/** WHO INVENTED a subject (a box, one of Sift's own passes, or somebody) and how. */
export type Maker = components['schemas']['MadeBy'];

/** Everything Sift's own table says about one subject's stash-boxes: what they said, and who made it. */
type StashBoxSources = { links: StashBoxLink[]; madeBy: Maker | null };

/** The maker, in the words a person reads on a page. */
export function madeBySaid(made: Maker): string {
	if (made.kind === 'stash_box') return `Created by ${made.box_name ?? 'a stash-box'}`;
	if (made.kind === 'you') return 'Created by you';
	if (made.kind === 'another_user') return 'Created by another user';
	if (made.kind !== 'sift') return 'Created by somebody';
	if (!made.via) return 'Created by Sift';
	/* The pass's own row out of the one table that names passes: `facet-labels.ts`, which the
	 * Enriched by column and the glyph beside these words both read. */
	return `Created by ${afterWords(madeLabel(made.via, made.act)).replace(': ', ', ')}`;
}

/** The glyph for the pass that made a row, out of the same table as its words. */
export function madeByGlyph(made: Maker): IconName | undefined {
	if (made.kind !== 'sift' || !made.via) return undefined;
	return madeIcon(made.via, made.act);
}

/** What has already been agreed about this subject, and which box invented it. */
export async function sourcesOf(subject: LinkSubject, id: string): Promise<StashBoxSources> {
	try {
		const answer = await api.get<components['schemas']['LinkList']>(
			`/stash-boxes/links/${subject}/${id}`
		);
		return { links: answer.links, madeBy: answer.made_by ?? null };
	} catch {
		// A record that cannot be read is a record with nothing in it, never a broken page.
		return { links: [], madeBy: null };
	}
}

/** The links alone, for the screens that draw the records and nothing else. */
export async function linksOf(subject: LinkSubject, id: string): Promise<StashBoxLink[]> {
	return (await sourcesOf(subject, id)).links;
}

/** The kinds of row that record who made them. Six, where a stash-box knows three. */
type MakerSubject = LinkSubject | 'collection' | 'photo_set' | 'song';

/** WHO MADE one row, for the line under an entity's name. */
export async function makerOf(subject: MakerSubject, id: string): Promise<Maker | null> {
	if (subject === 'person' || subject === 'site' || subject === 'tag') {
		return (await sourcesOf(subject, id)).madeBy;
	}
	try {
		if (subject === 'collection') return await api.get<Maker | null>(`/collections/${id}/made-by`);
		/* A song is made by hand, by AcoustID's answer, or from the page a file was downloaded
		   from; its maker is read at its own address for the reason the two above are. */
		if (subject === 'song') return await api.get<Maker | null>(`/songs/${id}/made-by`);
		return await api.get<Maker | null>(`/photo-sets/${id}/made-by`);
	} catch {
		return null;
	}
}

/** The address of one site's logo out of the pack that ships with Sift (`GET
 * /api/sites/icons/{slug}`). */
export function siteIconAddress(slug: string): string {
	return `/api/sites/icons/${slug}`;
}

/** THE PICTURE ONE ROW OF THE STASH-BOX CHOOSER DRAWS, and the one it falls back to. */
export function chooserPicture(found: { icon_slug?: string | null; image_url?: string | null }): {
	src: string | null;
	instead: string | null;
} {
	if (found.icon_slug) {
		return { src: siteIconAddress(found.icon_slug), instead: found.image_url ?? null };
	}
	return { src: found.image_url ?? null, instead: null };
}

/** Ask every switched-on box about a name, for the chooser somebody picks an entry out of. */
export async function search(
	subject: LinkSubject,
	term: string,
	about?: string
): Promise<BoxAnswer[]> {
	const answer = await api.get<components['schemas']['LookUpResult']>(
		`/stash-boxes/search/${subject}`,
		{
			query: about ? { term, about } : { term }
		}
	);
	return answer.answers;
}

/** Agree that this box's entry is this subject. The record is re-fetched and kept. */
export async function link(
	subject: LinkSubject,
	id: string,
	boxId: string,
	remoteId: string
): Promise<StashBoxLink> {
	return await api.put<StashBoxLink>(`/stash-boxes/links/${subject}/${id}/${boxId}`, {
		body: { remote_id: remoteId }
	});
}

/** Ask a box again about something already linked. By hand: nothing here runs on a timer. */
export async function refresh(
	subject: LinkSubject,
	id: string,
	boxId: string
): Promise<StashBoxLink> {
	return await api.post<StashBoxLink>(`/stash-boxes/links/${subject}/${id}/${boxId}/refresh`, {});
}

/** Forget that a box knows this subject. What was agreed to stays on the subject. */
export async function forgetLink(subject: LinkSubject, id: string, boxId: string): Promise<void> {
	await api.del(`/stash-boxes/links/${subject}/${id}/${boxId}`);
}

/** The sentence to show when one of these did not work. */
/** Keep a stash-box's picture as the one this subject is shown with. */
/* Keep the linked entry's picture as the one this subject is shown with. */
export async function keepPicture(subject: LinkSubject, id: string, boxId: string): Promise<void> {
	await api.post(`/stash-boxes/${boxId}/picture/keep`, {
		body: { subject, local_id: id }
	});
}

export function problemFrom(error: unknown): string {
	return said(error);
}

/** WHERE EACH FIELD CAME FROM, as the words its hover says: field key to "From StashDB, fetched
 * Sep 25, 2026". */
export function givenBy(links: readonly StashBoxLink[]): Record<string, string> {
	const said: Record<string, string> = {};
	for (const one of links) {
		for (const key of one.gave ?? []) {
			said[key] ??= `From ${one.box_name}, fetched ${dayOf(one.fetched_at)}`;
		}
	}
	return said;
}
