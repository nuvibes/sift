/* Usernames, and who they belong to. */

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

/** Whether a username is worth a line under a card: it holds files, or the Site's own number for
 * it is known. */
export function holdsSomething(one: Username): boolean {
	return one.asset_count > 0 || Boolean(one.number);
}

/** The usernames that hold something, filed by one of their own columns: the Site for a person's
 * Sites tab, the person for a Site's People tab. */
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

	/** What it is shown as, where its page is, and its ID. */
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

	/** Say who a username belongs to: somebody who already exists, or somebody new. */
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

	/** EVERY username one person holds, or every username on one Site, however many pages that
	 * is. */
	async allOf(query: Pick<UsernameQuery, 'personId' | 'siteId'>): Promise<Username[]> {
		const held: Username[] = [];
		for (;;) {
			const page = await this.list({ ...query, limit: EVERY_PAGE, offset: held.length });
			held.push(...page.items);
			/* An empty page ends it whatever the total says, so a total that moved while this
			   read cannot turn into a loop that never stops asking. */
			if (page.items.length === 0 || held.length >= page.total) return held;
		}
	}
}

export const usernames = new Usernames();

/** The usernames a wall of cards draws under each card, read once for the whole page and filed by
 * the card they belong under: the Site for a person's Sites tab (`site_id`), the person for a
 * Site's People tab (`person_id`). */
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
