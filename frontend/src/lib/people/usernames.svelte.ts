/* Usernames, and who they belong to.
 *
 * ## What a username is, and what it is not
 *
 * A username is one identity on one site: the name somebody posts under there, the name that
 * site shows beside it, its page there, the Site's own number for it when one is known, and the
 * files posted under it. A Person is a human. One human holds several usernames, and a username
 * can outlive whoever was behind it, which is why collapsing the two would lose something neither
 * could express on its own. One word for it everywhere: two words for one thing read as two
 * things, and "account" is what Sift's own sign-ins are called.
 *
 * ## Stored, and not a place
 *
 * A username has no page of its own (an old `/accounts/<id>` link lands on the Files wall
 * filtered to it: `routes/accounts/[id]/+page.ts`). The row stays (it is what files a
 * download under a Site, what a Site's sharing reaches files through, and what the Site's own
 * number lives on), but it is drawn where somebody already is. A person's Sites tab lists their
 * usernames under each Site, a Site's People tab lists each person's usernames on it, the
 * Organize queue answers "who is this" on the card itself, and "see the files" is the Files wall
 * filtered with `?username=`. Nothing links to a username as if it were a thing to visit, so
 * there is no per-username read or cover write here: an exported call nothing makes is a surface
 * nobody owns.
 *
 * ## Nothing is worked out here
 *
 * Counts are the server's and are scoped to whoever is asking, so they are never recomputed in
 * the client: a count taken over the rows this session happens to hold is a different number for
 * anybody but an admin.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/* LIVE: followed by routes/people/[id]/+page.svelte (UsernamesByCard.read on the library bell; the Site page the same) */

/** One username on one site. */
export type Username = components['schemas']['UsernameView'];

type UsernamePage = components['schemas']['UsernamePageView'];

/** What to ask for. `unattached` left out means "either" (see the route). */
interface UsernameQuery {
	q?: string;
	siteId?: string;
	personId?: string;
	unattached?: boolean;
	limit?: number;
	offset?: number;
	/** The username a page starts at, in place of `offset`. See `PageAsk` in `$lib/grid/anchor`. */
	from?: string;
	/** Where that username was, for when it has left the list since. Read only beside `from`. */
	near?: number | null;
	sort?: string;
}

function params(query: UsernameQuery): Record<string, string> {
	const sent: Record<string, string> = {};
	if (query.q) sent.q = query.q;
	if (query.siteId) sent.site_id = query.siteId;
	if (query.personId) sent.person_id = query.personId;
	/* Sent only when it was asked for. An `unattached=false` where nothing was meant would turn the
	   ordinary wall into the list of usernames already decided, which is the opposite screen. */
	if (query.unattached !== undefined) sent.unattached = String(query.unattached);
	if (query.limit !== undefined) sent.limit = String(query.limit);
	if (query.offset !== undefined) sent.offset = String(query.offset);
	if (query.from !== undefined) sent.from = query.from;
	if (query.from !== undefined && query.near !== undefined && query.near !== null)
		sent.near = String(query.near);
	if (query.sort) sent.sort = query.sort;
	return sent;
}

/** The route's own ceiling on one page (`kernel/paging.py MAX_PAGE_SIZE`). */
const EVERY_PAGE = 200;

/**
 * Whether a username is worth a line under a card: it holds files, or the Site's own number for it
 * is known.
 *
 * The rest are PROFILE LINKS, and on an enriched library they are nearly the whole table: a stash-box
 * record lists a performer's pages on thirty sites and each one becomes a row with nothing under
 * it. Those are the person's Links, drawn in the record already; listed here as usernames they
 * would bury the few that files were actually posted under.
 */
export function holdsSomething(one: Username): boolean {
	return one.asset_count > 0 || Boolean(one.number);
}

/**
 * The usernames that hold something, filed by one of their own columns: the Site for a person's
 * Sites tab, the person for a Site's People tab. A username with no value there is left out: a Site
 * row deleted from under a filing, or a username nobody has said who is behind.
 *
 * And a username with NO NAME is never listed: it is the Site's "poster unknown" row, how a file is
 * filed under a Site alone. Its files stay filed (the Site's Files tab counts them); as a line
 * under a card it would be a blank chip that is nobody's name.
 */
export function usernamesBy(
	all: readonly Username[],
	key: 'site_id' | 'person_id'
): Map<string, Username[]> {
	const filed = new Map<string, Username[]>();
	for (const one of all) {
		const at = one[key];
		if (!at || !holdsSomething(one) || !one.username.trim()) continue;
		const list = filed.get(at);
		if (list) list.push(one);
		else filed.set(at, [one]);
	}
	return filed;
}

class Usernames {
	/** One page of usernames. */
	async list(query: UsernameQuery = {}): Promise<UsernamePage> {
		return await api.get<UsernamePage>('/usernames', { query: params(query) });
	}

	/**
	 * What it is shown as, where its page is, and its ID. A field left out is left alone, never
	 * blanked.
	 *
	 * `number` fills a blank; `replaceNumber` beside it replaces one the username already has, and
	 * is only sent after the person was asked ("Change the Instagram ID?"). The server answers 409
	 * with a sentence for the person (`ApiError.detail`) where the username already has a different
	 * ID and replacing was not asked, or where ANOTHER username on the Site has this one ("These
	 * may be the same person, renamed").
	 */
	async save(
		id: string,
		changes: Partial<Pick<Username, 'display_name' | 'url' | 'number'>> & {
			replaceNumber?: boolean;
		}
	): Promise<Username> {
		const { replaceNumber, ...body } = changes;
		return await api.put<Username>(`/usernames/${id}`, {
			body: replaceNumber ? { ...body, replace_number: true } : body
		});
	}

	/**
	 * Say who a username belongs to: somebody who already exists, or somebody new.
	 *
	 * Its own call rather than a field on `save`, because one branch creates a person. The server
	 * refuses both and neither, so the branch with the larger consequence has to be asked for.
	 */
	async attach(
		id: string,
		who: { personId: string; asAlias?: boolean } | { newPersonName: string; asAlias?: boolean }
	): Promise<Username> {
		const body =
			'personId' in who
				? { person_id: who.personId, as_alias: who.asAlias ?? true }
				: { new_person_name: who.newPersonName, as_alias: who.asAlias ?? true };
		return await api.post<Username>(`/usernames/${id}/person`, { body });
	}

	/** Take the pointer off. The person stays, and so does any alias that was written. */
	async detach(id: string): Promise<void> {
		await api.del(`/usernames/${id}/person`);
	}

	/**
	 * EVERY username one person holds, or every username on one Site, however many pages that is.
	 *
	 * A person's Sites tab and a Site's People tab each draw usernames beside cards the server
	 * pages on its own terms, so the usernames have to be the whole set rather than the first page
	 * of it: a card whose username sat on page two would read as a person with no username on that
	 * Site. Read in pages of the route's own ceiling until the total is reached: a handful of
	 * requests for a large Site, one for nearly every person.
	 */
	async allOf(query: Pick<UsernameQuery, 'personId' | 'siteId'>): Promise<Username[]> {
		const held: Username[] = [];
		for (;;) {
			const page = await this.list({ ...query, limit: EVERY_PAGE, offset: held.length });
			held.push(...page.items);
			/* An empty page ends it whatever the total says, so a total that moved while this read
			   cannot turn into a loop that never stops asking. */
			if (page.items.length === 0 || held.length >= page.total) return held;
		}
	}
}

export const usernames = new Usernames();

/**
 * The usernames a wall of cards draws under each card, read once for the whole page and filed by
 * the card they belong under: the Site for a person's Sites tab (`site_id`), the person for a
 * Site's People tab (`person_id`).
 *
 * One read of every username rather than one per card: the wall pages its cards on its own terms, and
 * a request per card is a request per card per page. A slower answer for a page somebody has
 * already left cannot land over the one they are on (the rising counter). A failed read leaves
 * nothing under the cards rather than failing the wall: the cards are still the truth, and a line
 * missing is a smaller wrong than a wall replaced by an error.
 */
export class UsernamesByCard {
	#filed = $state<Map<string, Username[]>>(new Map());
	#asked = 0;
	readonly #key: 'site_id' | 'person_id';

	constructor(key: 'site_id' | 'person_id') {
		this.#key = key;
	}

	/** The usernames under one card, by the card's id. */
	of(cardId: string): Username[] {
		return this.#filed.get(cardId) ?? [];
	}

	async read(query: Pick<UsernameQuery, 'personId' | 'siteId'>): Promise<void> {
		const wanted = ++this.#asked;
		try {
			const all = await usernames.allOf(query);
			if (wanted === this.#asked) this.#filed = usernamesBy(all, this.#key);
		} catch {
			if (wanted === this.#asked) this.#filed = new Map();
		}
	}
}
