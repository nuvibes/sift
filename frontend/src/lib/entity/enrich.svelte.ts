/* Linking one of Sift's own subjects to a stash-box, and reading back what was kept. And,
 * beside it, WHO MADE a row, which is a different fact and is drawn on the same line.
 *
 * ## Why the maker lives here and not in a stash-box module of its own
 *
 * Because it is not a stash-box fact. A box is one of five answers to "who made this", and the
 * other four (Sift and the pass that did it, you, another user, nobody) have nothing to do
 * with this feature. What ties it to this file is the SENTENCE: `madeBySaid` and `madeByGlyph`
 * below are the one place that turns a maker into words and a mark, every screen that draws the
 * line reads them, and a fetch that lived somewhere else would be the answer in one module and the
 * words for it in another.
 *
 * ## One module for all three kinds of subject
 *
 * A person, a site and a tag are linked in exactly the same way: search by name, pick the entry
 * that is really them, and keep what that service says about them. So the kind is an argument
 * rather than three copies of this file, which is also what makes ONE confirm screen possible,
 * and the confirm screen is the part that would otherwise have been written three times.
 *
 * ## Nothing here writes to the subject
 *
 * These calls remember which entry in a stash-box a subject is, and nothing else. What somebody
 * agrees to keep is written through the subject's own edit route (the same one the record form
 * uses), so there is exactly one way a person's birthdate is ever written, whether it was typed
 * or agreed to.
 */

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

/**
 * The maker, in the words a person reads on a page.
 *
 * One function for every screen that draws the line, because the line is one sentence, and built in
 * two places it would be built from two different halves of the answer. A box is named; Sift names
 * the pass that did it; a user is "you" only to the user who asks, and any other is "another
 * user" with no name, which is the same withholding the sharing screens make.
 */
export function madeBySaid(made: Maker): string {
	if (made.kind === 'stash_box') return `Created by ${made.box_name ?? 'a stash-box'}`;
	if (made.kind === 'you') return 'Created by you';
	if (made.kind === 'another_user') return 'Created by another user';
	if (made.kind !== 'sift') return 'Created by somebody';
	if (!made.via) return 'Created by Sift';
	/*
	 * The pass's own row out of the one table that names passes: `facet-labels.ts`, which the
	 * Enriched by column and the glyph beside these words both read. A second list of nine phrases
	 * here would be a list free to disagree with that one, on a page drawing both at once.
	 *
	 * The colon becomes a comma and nothing else moves. "Sift: from a folder name" is a ROW in a
	 * column headed Enriched by, where the colon carries "who: how"; after a preposition it is a
	 * sentence, and a sentence takes a comma ("Created by Sift, from a folder name"). Rendering the
	 * row rather than keeping a second spelling of it is what makes the two agree by construction.
	 *
	 * A file Sift produced says WHICH act made it where the act is stored (`act`): "from a file it
	 * compressed" with the Compress glyph, "from a file it edited" with Modify's, out of `MADE_ACTS`
	 * beside that table.
	 */
	return `Created by ${afterWords(madeLabel(made.via, made.act)).replace(': ', ', ')}`;
}

/** The glyph for the pass that made a row, out of the same table as its words. */
export function madeByGlyph(made: Maker): IconName | undefined {
	if (made.kind !== 'sift' || !made.via) return undefined;
	return madeIcon(made.via, made.act);
}

/**
 * What has already been agreed about this subject, and which box invented it.
 *
 * The one call here a guest can make, because it reaches no network and spends no key: it is
 * Sift's own table. An empty list is the ordinary answer and never an error: most subjects in most
 * libraries have never been linked to anything, and most were made by a person rather than by a box.
 *
 * Both halves in ONE read because they arrive in one answer. A screen that wanted the second would
 * otherwise ask the same route twice. And worse, it could be told two different things by the two
 * replies, which is a page whose own marks disagree.
 */
export async function sourcesOf(subject: LinkSubject, id: string): Promise<StashBoxSources> {
	try {
		const answer = await api.get<components['schemas']['LinkList']>(
			`/stash-boxes/links/${subject}/${id}`
		);
		return { links: answer.links, madeBy: answer.made_by ?? null };
	} catch {
		// A record that cannot be read is a record with nothing in it, never a broken page. The
		// stash-boxes are an optional feature and every screen they touch works without them.
		return { links: [], madeBy: null };
	}
}

/**
 * The links alone, for the screens that draw the records and nothing else.
 *
 * A wrapper rather than a second request: the record panel has no line to put "created by" on, and
 * making it read a field it never draws would be a shape it has to ignore on every call. One
 * caller: if the record panel ever grows that line this wrapper goes with it. A wrapper over one
 * caller is a name for a field access.
 */
export async function linksOf(subject: LinkSubject, id: string): Promise<StashBoxLink[]> {
	return (await sourcesOf(subject, id)).links;
}

/**
 * The kinds of row that record who made them. Six, where a stash-box knows three.
 *
 * A collection and a Photo Set carry the same columns and are made by a person or by one of Sift's
 * own passes, so the maker line is the same line, from the same kernel read, and only the address
 * it is asked at differs. Each of those two is asked at its own entity's route rather than on the
 * stash-box one: a shelf has no stash-box links, and answering about one there would say it might.
 */
type MakerSubject = LinkSubject | 'collection' | 'photo_set' | 'song';

/**
 * WHO MADE one row, for the line under an entity's name.
 *
 * For the three kinds a stash-box can name, the maker rides along with the links: those pages read
 * both halves from one page load, and asking twice would let one reply disagree with the other.
 * The other two have no links to ride with (a shelf has no stash-box record, and answering about
 * one on that route would say it might), so each is asked at its own entity's address.
 *
 * The two addresses are written out rather than built, because the client's path type is generated
 * from the server's own list and only a literal is checked against it.
 *
 * Null for everything that recorded nothing, and null for an answer that could not be read at all:
 * a header missing one line is a page, and a header that throws is not.
 */
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

/**
 * The address of one site's logo out of the pack that ships with Sift (`GET
 * /api/sites/icons/{slug}`). The slug rides on a found record (`RecordFound.icon_slug`), computed
 * by the server, and is null for everything the pack has never heard of.
 */
export function siteIconAddress(slug: string): string {
	return `/api/sites/icons/${slug}`;
}

/**
 * THE PICTURE ONE ROW OF THE STASH-BOX CHOOSER DRAWS, and the one it falls back to.
 *
 * The shipped logo WINS over the box's own picture where there is one, and that is the point
 * rather than a detail. A box's picture is fetched from the box, through Sift's proxy, one trip off
 * the install per row of the list, for a studio mark that is already on the install's own disk and
 * is very often the same logo. The pack answers from the install.
 *
 * The box's picture stays as the SECOND address, so a pack that has the wrong slug for a site, or
 * a file missing out of it, draws what it always drew. And a row with neither falls to the letter
 * `Avatar` draws behind both.
 *
 * Only a SITE entry ever carries a slug (the server decides that, since the pack is a pack of
 * site logos), so a person or a tag in the chooser is untouched.
 */
export function chooserPicture(found: { icon_slug?: string | null; image_url?: string | null }): {
	src: string | null;
	instead: string | null;
} {
	if (found.icon_slug) {
		return { src: siteIconAddress(found.icon_slug), instead: found.image_url ?? null };
	}
	return { src: found.image_url ?? null, instead: null };
}

/**
 * Ask every switched-on box about a name, for the chooser somebody picks an entry out of.
 *
 * `about` is the row the chooser was OPENED ON, and it is the whole of what makes this refusable:
 * without it, the name of a record somebody had kept local would go out to public services the
 * moment the sheet asked its opening question.
 *
 * Optional because the chooser is also a box somebody can type into: a term somebody typed is not a
 * fact about any row in this library, and claiming it was would refuse a search that is about
 * nothing. Every way the sheet opens from a record's own page has an id and passes it.
 */
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
/**
 * Keep a stash-box's picture as the one this subject is shown with.
 *
 * The bytes are NOT sent from here. The server already has a way to fetch that address (through
 * the box's own route, with the box's key), so it re-uses it and keeps what comes back. Posting
 * the picture up from the browser would mean the browser fetching a remote host, which the page's
 * own content policy forbids for exactly the reason this route exists.
 */
/*
 * Keep the linked entry's picture as the one this subject is shown with.
 *
 * No address is sent, deliberately. What this side is holding is Sift's OWN proxy address for the
 * picture (which is how the page can draw it at all), and sending that back would be an address on
 * the wrong host, which the server refuses every time. The entry has been linked by the
 * step before this one, so the server reads the real address out of its own copy of what the box
 * said.
 */
export async function keepPicture(subject: LinkSubject, id: string, boxId: string): Promise<void> {
	await api.post(`/stash-boxes/${boxId}/picture/keep`, {
		body: { subject, local_id: id }
	});
}

export function problemFrom(error: unknown): string {
	return said(error);
}

/**
 * WHERE EACH FIELD CAME FROM, as the words its hover says: field key to "From StashDB, fetched
 * Sep 25, 2026". Read off `gave`, which the server works out from the runs written with each write
 * and holds only while the value is still the one that box gave (see `LinkView.gave`). A field no
 * box gave has no entry, and draws no hover.
 */
export function givenBy(links: readonly StashBoxLink[]): Record<string, string> {
	const said: Record<string, string> = {};
	for (const one of links) {
		for (const key of one.gave ?? []) {
			said[key] ??= `From ${one.box_name}, fetched ${dayOf(one.fetched_at)}`;
		}
	}
	return said;
}
