/* People and Sites, and the writes that change them. */

import { coverBody, type CoverMore } from '$lib/entity/cover-frame';
import { overChunks, type BulkWriteDone } from '$lib/library/bulk';
import { libraryChanges, recorded } from '$lib/library/changes.svelte';
import { api, type ApiPath } from '$lib/api/client';
import { PICK_PAGE } from '$lib/search/frequent.svelte';
import type { components } from '$lib/api/schema';
import { asked } from '$lib/grid/anchor';
import { fillHeld, type CardPaging } from '$lib/grid/cards.svelte';

/* Every cover field a Site's write reply carries, as the wall's copy of the row takes them. */
function coverOf(
	site: Site
): Pick<Site, 'cover_asset_id' | 'cover_upload_id' | 'cover_at_ms' | 'cover_frame'> {
	return {
		cover_asset_id: site.cover_asset_id,
		cover_upload_id: site.cover_upload_id,
		cover_at_ms: site.cover_at_ms,
		cover_frame: site.cover_frame
	};
}

export type Person = components['schemas']['PersonView'];

export type Alias = components['schemas']['AliasView'];

/** A site, taken from the server's own definition rather than described again here. */
export type Site = components['schemas']['SiteView'];

export type Link = components['schemas']['sift__slices__people__models__LinkView'];

type AliasMatch = components['schemas']['AliasMatchView'];

/** One page of People, as the list route answers it. */
type PeoplePage = components['schemas']['PeopleList'];

/* How many People one page of the wall holds. The wall is not the face groups: a person is one
 * row with a count and a cover, not a pile that has to be recounted per viewer, so a page can be
 * larger. */
export const PEOPLE_PER_PAGE = 60;

/* The order a wall is in, as the server's key; the server refuses one it does not know. */
export type EntitySort = string;

/** The people whose name contains what was typed, from the SERVER, for a box somebody is typing a
 * name into. */
export async function peopleNamed(typed: string, limit = PICK_PAGE): Promise<Person[]> {
	const prefix = typed.trim();
	if (!prefix) return [];
	const page = await api.get<PeoplePage>('/people', {
		query: { prefix, anywhere: 'true', limit: String(limit), offset: '0' }
	});
	return page.items;
}

export class People {
	items = $state<Person[]>([]);
	loading = $state(false);
	failed = $state(false);
	loaded = $state(false);
	/** How many this account may see in total, which is what the paginator is drawn from. */
	total = $state(0);
	/** Where the page on screen actually starts, as the server resolved it. See `load`. */
	at = $state(0);

	/* Empty this cache, so the next screen that wants it fetches again. */
	forget(): void {
		this.items = [];
		this.total = 0;
		this.at = 0;
		this.loaded = false;
	}

	/** Rising counter so a slow response cannot overwrite a newer one. */
	#generation = 0;

	/* Any page still in the air is not the answer any more. */
	#settled(): void {
		this.#generation += 1;
	}

	/** The order the wall is showing. Held here rather than on the screen so that a reload
	 *  triggered from anywhere (a share moving, the vault opening) asks for the same order. */
	/* The order the wall opens in: how much of the library each one accounts for, most first. */
	sort = $state<EntitySort>('largest');

	/* WHAT THE WALL IS FILTERED BY, as the named parameters the list route takes. */
	narrowing = $state<Record<string, string[]>>({});

	/** The wall's page, through the wall's own paging rather than at a size handed in. */
	async fill(
		paging: CardPaging,
		sort: EntitySort = this.sort,
		narrowing: Record<string, string[]> = this.narrowing,
		prefix = ''
	): Promise<void> {
		const generation = ++this.#generation;
		this.sort = sort;
		this.narrowing = narrowing;
		await fillHeld(
			this,
			paging,
			JSON.stringify([sort, narrowing, prefix]),
			(ask) =>
				// The filtering first; the paging parameters are this store's and cannot be displaced.
				api.get<PeoplePage>('/people', { query: { ...narrowing, prefix, sort, ...asked(ask) } }),
			(answer) => ({ rows: answer.items, total: answer.total, offset: answer.offset }),
			() => generation === this.#generation
		);
	}

	async load(
		prefix = '',
		offset = 0,
		limit = PEOPLE_PER_PAGE,
		sort: EntitySort = this.sort,
		/* Start at this person instead of at the offset: what the address carries, resolved by
		 * the server against the same scoped, ordered list the page comes out of. */
		from: string | null = null,
		narrowing: Record<string, string[]> = this.narrowing
	): Promise<void> {
		const generation = ++this.#generation;
		this.sort = sort;
		this.narrowing = narrowing;
		this.loading = true;
		this.failed = false;
		try {
			const page = await api.get<PeoplePage>('/people', {
				query: {
					// The filtering FIRST, so a facet that ever came to share a name with one of
					// the paging parameters could not take its place: where the wall starts and how
					// much of it is asked for are this store's to say, never the address's.
					...narrowing,
					prefix,
					limit: String(limit),
					sort,
					// One or the other, never both: a request carrying an anchor AND an offset asks
					// two questions and which one wins becomes a detail of the server.
					...(from === null ? { offset: String(offset) } : { from })
				}
			});
			if (generation !== this.#generation) return;
			this.items = page.items;
			this.total = page.total;
			// Where the server actually started.
			this.at = page.offset;
			this.loaded = true;
		} catch {
			if (generation !== this.#generation) return;
			this.failed = true;
		} finally {
			if (generation === this.#generation) this.loading = false;
		}
	}

	async create(
		name: string,
		vault = false,
		more?: {
			notes: string | null;
			record: Record<string, unknown>;
			aliases: string[];
			links: string[];
		}
	): Promise<Person> {
		/* The rest of the record rides on the create, so a refused address refuses the whole
		   thing and no bare person is left behind; left out when the caller has none. */
		const created = await api.post<Person>('/people', { body: { name, vault, ...(more ?? {}) } });
		// Somebody created straight into the vault never joins the list, for the same reason one
		// moved into it leaves it: the server would not list them, and a store that shows them
		// anyway is showing a row nothing else agrees exists.
		this.#settled();
		if (!created.vault) {
			this.items = [...this.items, created].sort(bySeenThenName);
			this.total += 1;
		}
		return created;
	}

	/** Replaces what is editable. */
	async update(
		id: string,
		name: string,
		vault: boolean,
		notes?: string | null,
		record?: Record<string, unknown>,
		lists?: { aliases: string[]; links: string[] }
	): Promise<Person> {
		/* Each field is left OUT when it was not given, never sent as null. */
		const body: Record<string, unknown> = { name, vault };
		if (notes !== undefined) body.notes = notes;
		if (record !== undefined) body.record = record;
		/* The other names and the addresses, replaced whole, on the same write as the name, so
		   an address the server refuses refuses the whole save and nothing is left half written. */
		if (lists !== undefined) {
			body.aliases = lists.aliases;
			body.links = lists.links;
		}
		const updated = await api.put<Person>(`/people/${id}`, { body });
		// The server does not recount on an edit, so the count this list already holds is kept
		// rather than taking the zero the write replies with.
		const existing = this.items.find((person) => person.id === id);
		const merged = { ...updated, asset_count: existing?.asset_count ?? updated.asset_count };
		if (merged.vault) {
			/* Somebody just put in the vault is re-read rather than dropped. */
			await this.load();
			return merged;
		}
		this.items = this.items
			.map((person) => (person.id === id ? merged : person))
			.sort(bySeenThenName);
		return merged;
	}

	/* The heart and the stars, written optimistically. */
	async setFavorite(id: string, favorite: boolean): Promise<void> {
		await this.#state(id, `/people/${id}/favorite`, { favorite });
	}

	async setRating(id: string, rating: number | null): Promise<void> {
		await this.#state(id, `/people/${id}/rating`, { rating });
	}

	/** Into the vault, or back out. This account's own, exactly as the heart and the stars are. */
	async setVault(id: string, vault: boolean): Promise<void> {
		await api.put<void>(`/people/${id}/vault`, { body: { vault } });
		this.#settled();
		libraryChanges.changed();
	}

	/** The still they are drawn as. Chosen from one of their own files; null clears it back to the
	 *  first the server would pick. The server checks the asset is one this viewer may see. */
	/** Point it at a file, and optionally at WHICH MOMENT of that file. */
	async setCover(
		id: string,
		assetId: string | null,
		atMs: number | null = null,
		more?: CoverMore
	): Promise<Person> {
		const updated = await api.put<Person>(`/people/${id}/cover`, {
			body: coverBody(assetId, atMs, more)
		});
		return this.#tookACover(id, updated);
	}

	/** A cover picture from OUTSIDE the library, chosen from the disk. */
	async uploadCover(id: string, file: File): Promise<Person> {
		const form = new FormData();
		form.set('file', file);
		return this.#tookACover(
			id,
			await api.post<Person>(`/people/${id}/cover-picture`, { body: form })
		);
	}

	/* What both cover writes do with the row they get back. */
	#tookACover(id: string, updated: Person): Person {
		// A cover change does not recount, so the count this list holds is kept over the reply's.
		const existing = this.items.find((person) => person.id === id);
		const merged = { ...updated, asset_count: existing?.asset_count ?? updated.asset_count };
		this.items = this.items.map((person) => (person.id === id ? merged : person));
		return merged;
	}

	async #state(id: string, path: ApiPath, body: Record<string, unknown>): Promise<void> {
		const before = this.items;
		this.#apply(id, body);
		try {
			const held = await api.put<components['schemas']['EntityStateView']>(path, { body });
			// The server's answer rather than the guess, so a value it clamped or refused shows as
			// what was really stored.
			this.#apply(id, held);
		} catch (error) {
			this.items = before;
			throw error;
		}
	}

	#apply(id: string, patch: Record<string, unknown>): void {
		this.items = this.items.map((person) => (person.id === id ? { ...person, ...patch } : person));
	}

	async remove(id: string): Promise<void> {
		await api.del<void>(`/people/${id}`);
		this.#settled();
		const held = this.items.length;
		this.items = this.items.filter((person) => person.id !== id);
		// Only if it was really on this page. A removal from a menu over a row this page is not
		// showing would otherwise take one off a count that never included it.
		if (this.items.length < held) this.total = Math.max(0, this.total - 1);
	}

	/** Attach or detach, for one asset or a whole selection. Moves no file. */
	async assign(assetIds: string[], personIds: string[], add = true): Promise<BulkWriteDone> {
		// The whole answer. See `Collections.#edit` for why the count alone is not enough.
		const done = await overChunks(assetIds, (chunk) =>
			api.post<BulkWriteDone>('/assets/people', {
				body: { asset_ids: chunk, person_ids: personIds, add }
			})
		);
		// A line in each file's history, which the server may tell nobody about (see `recorded`).
		recorded.changed();
		await this.load();
		return done;
	}

	/** Say that these files came from these sites, or that they did not. */
	async filedUnder(assetIds: string[], siteIds: string[], add = true): Promise<BulkWriteDone> {
		const done = await overChunks(assetIds, (chunk) =>
			api.post<BulkWriteDone>('/assets/sites', {
				body: { asset_ids: chunk, site_ids: siteIds, add }
			})
		);
		// A line in each file's history (see `recorded`).
		recorded.changed();
		return done;
	}

	/** Who a typed term names: by name, by an alias, or by the handle of a linked account. */
	async resolve(term: string): Promise<AliasMatch> {
		return api.get<AliasMatch>('/people/resolve', { query: { term } });
	}

	async aliases(personId: string): Promise<Alias[]> {
		return api.get<Alias[]>(`/people/${personId}/aliases`);
	}

	async addAlias(personId: string, alias: string): Promise<Alias> {
		return api.post<Alias>(`/people/${personId}/aliases`, { body: { alias } });
	}

	async links(personId: string): Promise<Link[]> {
		return api.get<Link[]>(`/people/${personId}/links`);
	}

	async addLink(personId: string, url: string): Promise<Link> {
		return api.post<Link>(`/people/${personId}/links`, { body: { url } });
	}

	async removeLink(personId: string, linkId: string): Promise<void> {
		await api.del<void>(`/people/${personId}/links/${linkId}`);
	}

	async removeAlias(personId: string, aliasId: string): Promise<void> {
		await api.del<void>(`/people/${personId}/aliases/${aliasId}`);
	}

	/** One page for a PICKER, alphabetical, filtered by what is being typed. */
	async choices(prefix = '', limit = PICK_PAGE): Promise<{ items: Person[]; total: number }> {
		const page = await api.get<PeoplePage>('/people', {
			query: { prefix, anywhere: 'true', limit, offset: 0, sort: 'name_az' }
		});
		// A locked row (a name the vault keeps back) is no choice: it would be a blank line in an
		// "Add to" list, and picking it would act on something whose name cannot be seen.
		const named = page.items.filter((one) => !one.locked);
		return { items: named, total: page.total - (page.items.length - named.length) };
	}

	/** One person, from the server, by id. A detail page reads this rather than picking its
	 * subject out of `items`. */
	async one(id: string): Promise<Person> {
		return api.get<Person>(`/people/${id}`);
	}

	/** Several people, from the server, by id: every one of them or a refusal. */
	async several(ids: string[]): Promise<Person[]> {
		return Promise.all(ids.map((id) => this.one(id)));
	}

	byId(id: string): Person | undefined {
		return this.items.find((person) => person.id === id);
	}

	/** Names beginning with what has been typed. Filtered here rather than refetched: the list is
	 * already loaded and a request per keystroke buys nothing. */
	matching(prefix: string): Person[] {
		const needle = prefix.trim().toLowerCase();
		if (!needle) return this.items;
		return this.items.filter((person) => person.name.toLowerCase().startsWith(needle));
	}

	/* People whose name contains this term, among those the wall's filters allow, asked of the
	 * server because the cached list is capped and only the server counts files and knows the
	 * covers. */
	async matchingAnywhere(
		term: string,
		narrowing: Record<string, string[]> = {},
		limit = 200
	): Promise<Person[]> {
		const page = await api.get<PeoplePage>('/people', {
			query: { ...narrowing, prefix: term, anywhere: 'true', limit: String(limit) }
		});
		return page.items;
	}
}

/** THE PEOPLE WALL'S SEARCH: the answer to a typed name, held apart from the wall's page, and
 * asked again whenever the page is. */
export class PeopleSearch {
	/** The answer on screen, or null when nothing is being searched for. */
	matches = $state<Person[] | null>(null);
	/** The term that answer is for. */
	searched = $state('');
	/** Whether a TYPED search is out. A re-read of the same term is not a new search. */
	searching = $state(false);
	#asked = 0;
	#ask: (term: string) => Promise<Person[]>;

	constructor(ask: (term: string) => Promise<Person[]>) {
		this.#ask = ask;
	}

	/** Search for what was typed; empty clears the search. */
	async run(typed: string): Promise<boolean> {
		const mine = ++this.#asked;
		const wanted = typed.trim();
		if (!wanted) {
			this.matches = null;
			this.searched = '';
			this.searching = false;
			return true;
		}
		this.searching = true;
		try {
			const found = await this.#ask(wanted);
			if (mine !== this.#asked) return false;
			this.matches = found;
			this.searched = wanted;
			return true;
		} catch (error) {
			if (mine !== this.#asked) return false;
			throw error;
		} finally {
			if (mine === this.#asked) this.searching = false;
		}
	}

	/** The search on screen, asked again: what the library bell calls, beside the wall's page. */
	async again(): Promise<void> {
		const term = this.searched;
		if (this.matches === null || !term) return;
		const mine = ++this.#asked;
		try {
			const found = await this.#ask(term);
			if (mine === this.#asked) this.matches = found;
		} catch {
			// Kept, as above.
		}
	}
}

/* How many Sites one page of the wall holds. See `PEOPLE_PER_PAGE`; this is the fallback before
 * the wall has been laid out and a card measured. */
export const SITES_PER_PAGE = 60;

export class Sites {
	items = $state<Site[]>([]);
	loading = $state(false);
	failed = $state(false);
	loaded = $state(false);

	/* Empty this cache, so the next screen that wants it fetches again. */
	forget(): void {
		this.items = [];
		this.total = 0;
		this.loaded = false;
	}

	/** How many this account may see in total, which is what the paginator is drawn from. */
	total = $state(0);

	/* Rising counter so a slow response cannot overwrite a newer one, and so a write that edits
	 * the list in place discards a page that was already in the air. */
	#generation = 0;

	#settled(): void {
		this.#generation += 1;
	}

	/** The order the wall is showing. See the People store above for why it lives here. */
	/* The order the wall opens in: how much of the library each one accounts for, most first. */
	sort = $state<EntitySort>('largest');

	/* One page of the list, with a limit of its own. */
	/** What the wall is filtered by. See `People.narrowing` above for why it lives on the store. */
	narrowing = $state<Record<string, string[]>>({});

	/** One page of the wall. */
	/** Where the server actually started the page it last handed back. */
	at = $state(0);

	/** The wall's page, through the wall's own paging rather than at a size handed in. */
	async fill(
		paging: CardPaging,
		sort: EntitySort = this.sort,
		narrowing: Record<string, string[]> = this.narrowing,
		prefix = ''
	): Promise<void> {
		const generation = ++this.#generation;
		this.sort = sort;
		this.narrowing = narrowing;
		await fillHeld(
			this,
			paging,
			JSON.stringify([sort, narrowing, prefix]),
			(ask) =>
				// The filtering first; the paging parameters are this store's and cannot be displaced.
				api.get<components['schemas']['SiteList']>('/sites', {
					// The box's words match anywhere in a name, as the People wall's do.
					query: { ...narrowing, prefix, anywhere: 'true', sort, ...asked(ask) }
				}),
			(answer) => ({ rows: answer.items, total: answer.total, offset: answer.offset }),
			() => generation === this.#generation
		);
	}

	async load(
		sort: EntitySort = this.sort,
		offset = 0,
		limit = SITES_PER_PAGE,
		narrowing: Record<string, string[]> = this.narrowing,
		prefix = '',
		/* Start at this row instead of at the offset: what the address carries, resolved by the
		 * server against the same scoped, ordered list the page comes out of. */
		from: string | null = null
	): Promise<void> {
		const generation = ++this.#generation;
		this.sort = sort;
		this.narrowing = narrowing;
		this.loading = true;
		this.failed = false;
		try {
			const page = await api.get<components['schemas']['SiteList']>('/sites', {
				// The filtering first; the paging parameters are this store's and cannot be
				// displaced.
				query: { ...narrowing, sort, limit, prefix, ...(from === null ? { offset } : { from }) }
			});
			if (generation !== this.#generation) return;
			this.items = page.items;
			this.total = page.total;
			this.at = page.offset;
			this.loaded = true;
		} catch {
			if (generation !== this.#generation) return;
			this.failed = true;
		} finally {
			if (generation === this.#generation) this.loading = false;
		}
	}

	/** One page for a PICKER, alphabetical, filtered by what is being typed. */
	async choices(prefix = '', limit = PICK_PAGE): Promise<{ items: Site[]; total: number }> {
		const page = await api.get<components['schemas']['SiteList']>('/sites', {
			query: { prefix, limit, offset: 0, sort: 'name_az' }
		});
		const named = page.items.filter((one) => !one.locked);
		return { items: named, total: page.total - (page.items.length - named.length) };
	}

	async create(
		name: string,
		details?: { notes: string | null; aliases: string[]; parent: string | null; links: string[] }
	): Promise<Site> {
		/* The details ride on the create, so a refused address or parent refuses the whole thing
		   and no bare Site is left behind; left out when the caller has none. */
		const created = await api.post<Site>('/sites', { body: { name, ...(details ?? {}) } });
		// The name is unique without regard to case, so this may be a site that already existed.
		this.#settled();
		const held = this.items.length;
		this.items = [...this.items.filter((p) => p.id !== created.id), created].sort(byName);
		// Only when it really is a new row. This replaces by id, so asking for a site that already
		// exists leaves the length where it was and must leave the total there too.
		if (this.items.length > held) this.total += 1;
		return created;
	}

	/** A Site's whole record in one write: its name, and with `record` its notes, other names,
	 * parent and addresses. */
	async save(
		id: string,
		name: string,
		record?: {
			notes: string | null;
			aliases: string[];
			parent: string | null;
			links: string[];
		}
	): Promise<Site> {
		const updated = await api.put<Site>(`/sites/${id}`, { body: { name, ...record } });
		// A rename does not recount, and the reply carries neither number, so the counts this list
		// already holds are kept.
		const existing = this.items.find((one) => one.id === id);
		const merged = {
			...updated,
			asset_count: existing?.asset_count ?? updated.asset_count,
			people_count: existing?.people_count ?? updated.people_count
		};
		this.items = this.items.map((p) => (p.id === id ? merged : p)).sort(byName);
		return merged;
	}

	/** Delete a site. Refused with a 409 while handles are still on it unless `withAccounts`. */
	/* The same optimistic pair the People store has, on the same shape of row. */
	async setFavorite(id: string, favorite: boolean): Promise<void> {
		await this.#state(id, `/sites/${id}/favorite`, { favorite });
	}

	async setRating(id: string, rating: number | null): Promise<void> {
		await this.#state(id, `/sites/${id}/rating`, { rating });
	}

	/** Whatever somebody wrote about the site, and (with the record) its addresses. */
	async setDetails(
		id: string,
		notes: string | null,
		record?: { aliases: string[]; parent: string | null; links?: string[] }
	): Promise<void> {
		/* The record half is left OUT when it was not given, never sent as null. */
		const body: Record<string, unknown> = { notes };
		if (record) {
			body.aliases = record.aliases;
			body.parent = record.parent;
			/* Every address, when the caller had the whole list. */
			if (record.links) body.links = record.links;
		}
		await api.put<void>(`/sites/${id}/details`, { body });
		/* The notes only: which link is FIRST is the server's to say (it puts the site's own
		   home first), and every screen that draws the address reads the row again. */
		this.#apply(id, { notes });
	}

	/** The still the site is drawn as. Chosen from one of its own files; null clears it. */
	/** Point it at a file, and optionally at WHICH MOMENT of that file. */
	async setCover(
		id: string,
		assetId: string | null,
		atMs: number | null = null,
		more?: CoverMore
	): Promise<Site> {
		const updated = await api.put<Site>(`/sites/${id}/cover`, {
			body: coverBody(assetId, atMs, more)
		});
		this.#apply(id, coverOf(updated));
		return updated;
	}

	/** A cover picture from OUTSIDE the library, chosen from the disk. */
	async uploadCover(id: string, file: File): Promise<Site> {
		const form = new FormData();
		form.set('file', file);
		const updated = await api.post<Site>(`/sites/${id}/cover-picture`, { body: form });
		this.#apply(id, coverOf(updated));
		return updated;
	}

	async #state(id: string, path: ApiPath, body: Record<string, unknown>): Promise<void> {
		const before = this.items;
		this.#apply(id, body);
		try {
			this.#apply(id, await api.put<components['schemas']['EntityStateView']>(path, { body }));
		} catch (error) {
			this.items = before;
			throw error;
		}
	}

	#apply(id: string, patch: Record<string, unknown>): void {
		this.items = this.items.map((one) => (one.id === id ? { ...one, ...patch } : one));
	}

	async remove(id: string): Promise<void> {
		await api.del<void>(`/sites/${id}`);
		this.#settled();
		const held = this.items.length;
		this.items = this.items.filter((p) => p.id !== id);
		if (this.items.length < held) this.total = Math.max(0, this.total - 1);
	}

	/** Into the vault, or back out. */
	async setVault(id: string, vault: boolean): Promise<void> {
		await api.put<void>(`/sites/${id}/vault`, { body: { vault } });
		await this.load();
	}

	/** One Site, from the server, by id. See `People.one`. */
	async one(id: string): Promise<Site> {
		return api.get<Site>(`/sites/${id}`);
	}

	/** Several Sites, by id, every one or a refusal. See `People.several` for why. */
	async several(ids: string[]): Promise<Site[]> {
		return Promise.all(ids.map((id) => this.one(id)));
	}

	byId(id: string): Site | undefined {
		return this.items.find((p) => p.id === id);
	}
}

/** The one instance the screens share. */
export const people = new People();
export const sites = new Sites();

function bySeenThenName(a: Person, b: Person): number {
	if (a.asset_count !== b.asset_count) return b.asset_count - a.asset_count;
	return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
}

function byName(a: Site, b: Site): number {
	return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
}
