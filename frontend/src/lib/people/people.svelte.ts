/* People and Sites, and the writes that change them.
 *
 * Two things, kept apart on purpose. A Site is a site; a Person is a human, who may appear in
 * media from several sites and in media posted by people who are not them. One "who is this"
 * field could express neither.
 *
 * A username (one name on one site) is recorded against every downloaded file and is
 * searchable, but it is not a thing anybody manages: what somebody wants to know is WHO, and the
 * download already knew.
 *
 * Counts are the server's and are scoped to whoever is asking, so they are never recomputed here.
 * A count worked out in the client is a count over the rows this session happens to have fetched,
 * which for anybody but an admin is a different number.
 *
 * Resolving a term is a request rather than a filter over the loaded list. The server matches a
 * name and an alias somebody typed; the client holds only the first, so answering here would miss
 * half the question.
 */

import { coverBody, type CoverMore } from '$lib/entity/cover-frame';
import { overChunks, type BulkWriteDone } from '$lib/library/bulk';
import { libraryChanges, recorded } from '$lib/library/changes.svelte';
import { api, type ApiPath } from '$lib/api/client';
import { PICK_PAGE } from '$lib/search/frequent.svelte';
import type { components } from '$lib/api/schema';
import { asked } from '$lib/grid/anchor';
import { fillHeld, type CardPaging } from '$lib/grid/cards.svelte';

/*
 * Every cover field a Site's write reply carries, as the wall's copy of the row takes them.
 *
 * The upload, the moment and the window are one cover, and a reply is applied as one: taking
 * `cover_asset_id` alone would leave a Sites wall drawing the old moment and window after a write
 * that changed them.
 */
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

/**
 * A site, taken from the server's own definition rather than described again here.
 *
 * A copy written out by hand drifts in the quiet direction: `kind`, `site_url`, `notes` and
 * `cover_asset_id` declared as always present where the server sends each of them only when it has
 * one. The tag store does the same: see `Tag`.
 */
export type Site = components['schemas']['SiteView'];

export type Link = components['schemas']['sift__slices__people__models__LinkView'];

type AliasMatch = components['schemas']['AliasMatchView'];

/** One page of People, as the list route answers it. */
type PeoplePage = components['schemas']['PeopleList'];

/* How many People one page of the wall holds.
 *
 * The wall is not the face groups: a person is one row with a count and a cover, not a pile that
 * has to be recounted per viewer, so a page can be larger. It is still a page: every person costs
 * the server a scoped count and a visibility question, and drawing six hundred of them is a page
 * nobody waits for.
 */
export const PEOPLE_PER_PAGE = 60;

/**
 * What a wall of People or Sites offers BEYOND the orders every kind of thing shares.
 *
 * The values are the server's own keys (it refuses one it does not know rather than quietly
 * ordering some other way), and the labels are what the control says. Declared here, once, so the
 * two walls cannot come to offer different orders of the same two.
 *
 * There is no "Most seen" here. The server's ordinary order is by `asset_count`, which has nothing
 * to do with how often anybody was looked at; it is `largest`, in `UNIVERSAL_SORTS`, and every wall
 * in the application offers it under one word rather than one idea under three names.
 *
 * Ordering only. There is no screen of favourited people and no search word for a rating on one:
 * this control changes where a row appears, never whether it appears.
 */
export const ENTITY_SORTS = [
	{ value: 'favorite', label: 'Favorites first' },
	{ value: 'rating', label: 'Highest rated' }
] as const;

/*
 * The order a wall is in, as the server's key.
 *
 * A plain string rather than a union of the three above, and the widening is deliberate. These walls
 * offer the four orders EVERY kind of thing in the library shares (newest, oldest and the two name
 * directions, spelled exactly as the file grid spells them) on top of their own three. A union of
 * the three would make every call site cast to it (`next as EntitySort`) on a value that is one of
 * seven. A cast that is wrong is worse than no type at all, because it reads
 * as having been checked.
 *
 * Nothing is lost by widening it here. The server holds the real list (`ENTITY_SORT_KEYS`) and
 * refuses a key it does not know with a 422 rather than quietly ordering some other way, which is
 * the check that actually matters and the only one that cannot be bypassed by a hand-typed address.
 */
export type EntitySort = string;

/**
 * The people whose name contains what was typed, from the SERVER, for a box somebody is typing a
 * name into.
 *
 * `people.items` is one page of the wall (sixty of several hundred), so filtering it would leave a
 * person past the first page unfindable by typing their name ("ilva" finding nobody, with Ilva
 * Brennan on the wall). This asks the wall's own read for the names, filtered anywhere in the name,
 * a page at a time.
 *
 * The default is the PICKER's page and not a number of its own. A smaller one would be a second
 * ceiling in front of a sheet that already draws one and says how much it is holding back. A
 * search answering eight of forty would make the sheet's own count wrong as well as short. One
 * number for how long a list of choices is, wherever the list came from.
 */
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

	/* Empty this cache, so the next screen that wants it fetches again.
	 *
	 * For the vault: what is concealed never arrives, so a list held from while the vault was open
	 * still holds it after the vault shuts. Clearing the rows as well as the flag matters: a
	 * screen reading `items` directly would otherwise draw the old ones until the fetch lands.
	 */
	forget(): void {
		this.items = [];
		this.total = 0;
		this.at = 0;
		this.loaded = false;
	}

	/** Rising counter so a slow response cannot overwrite a newer one. */
	#generation = 0;

	/* Any page still in the air is not the answer any more. See the tag store, which carries the
	 * same guard and the same reasoning: a write that edits this list in place has to invalidate
	 * a load that was already outstanding, or the older answer lands afterwards and overwrites
	 * the edit.
	 */
	#settled(): void {
		this.#generation += 1;
	}

	/** The order the wall is showing. Held here rather than on the screen so that a reload
	 *  triggered from anywhere (a share moving, the vault opening) asks for the same order. */
	/* The order the wall opens in: how much of the library each one accounts for, most first.
	   The same order the server falls through to, under the name every wall calls it. 'seen' is
	   still the server's own default key and is not offered as a choice: a control cannot show
	   a chosen value that is not one of its options. */
	sort = $state<EntitySort>('largest');

	/*
	 * WHAT THE WALL IS FILTERED BY, as the named parameters the list route takes.
	 *
	 * Held here beside the order, and for the identical reason: a reload triggered from somewhere
	 * that knows nothing about the filter (the order changing, the vault opening, a share moving)
	 * has to ask the question the wall is currently showing the answer to. Left on the screen, every
	 * one of those call sites would have to remember to pass it, and the one that forgot would widen
	 * the wall back to the whole library without saying so.
	 *
	 * Several values under one key are "either of these"; different keys are ANDed by the server.
	 * `facetParams` is what reads them out of the address.
	 */
	narrowing = $state<Record<string, string[]>>({});

	/**
	 * The wall's page, through the wall's own paging rather than at a size handed in.
	 *
	 * A size handed in would ask at the fallback count on a first visit and again, whole, once a
	 * card has been measured. Through `CardPaging.fill` the second ask trims the rows held or asks
	 * for the remainder only, and an anchored arrival lands its offset without asking again. `load`
	 * stays for the other screens that read this list at a size of their own.
	 */
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
		/* Start at this person instead of at the offset: what the address carries, resolved by the
		 * server against the same scoped, ordered list the page comes out of. Only the server knows
		 * that list; working it out from a page already in hand would give a position in the page. */
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
					// The filtering FIRST, so a facet that ever came to share a name with one of the
					// paging parameters could not take its place: where the wall starts and how much
					// of it is asked for are this store's to say, never the address's.
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
			// Where the server actually started. Sent an anchor rather than an offset, the client
			// does not know the answer until it arrives, and the pager has to read the real one or
			// a link would open the right rows under a readout claiming they are the first.
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
		/* The rest of the record rides on the create, so a refused address refuses the whole thing
		   and no bare person is left behind; left out when the caller has none. */
		const created = await api.post<Person>('/people', { body: { name, vault, ...(more ?? {}) } });
		// Somebody created straight into the vault never joins the list, for the same reason one
		// moved into it leaves it: the server would not list them, and a store that shows them
		// anyway is showing a row nothing else agrees exists.
		// The total moves with the rows. The pager is drawn from it, and so is whether the wall thinks
		// it has anything on it at all. So a list that grew by one while the total stayed put reads
		// as an empty screen with a card on it.
		this.#settled();
		if (!created.vault) {
			this.items = [...this.items, created].sort(bySeenThenName);
			this.total += 1;
		}
		return created;
	}

	/** Replaces what is editable. `notes` is the exception and is omitted from the body entirely
	 * when the caller does not pass it: no route hands notes back to a screen, so a caller that
	 * has not been told what they say must not be able to clear them by not knowing. Passing an
	 * explicit null still clears them. */
	async update(
		id: string,
		name: string,
		vault: boolean,
		notes?: string | null,
		record?: Record<string, unknown>,
		lists?: { aliases: string[]; links: string[] }
	): Promise<Person> {
		/* Each field is left OUT when it was not given, never sent as null.
		 *
		 * The server reads absence as "leave this alone" and a value as "make it this", which is the
		 * only way one route can serve both a rename form that knows nothing about a birthdate and a
		 * record form that means to clear one. Sending `record: undefined` through here would be the
		 * same as not sending it; sending `record: {}` clears every column, and that is a real thing
		 * the form says when somebody empties every box.
		 */
		const body: Record<string, unknown> = { name, vault };
		if (notes !== undefined) body.notes = notes;
		if (record !== undefined) body.record = record;
		/* The other names and the addresses, replaced whole, on the same write as the name, so an
		   address the server refuses refuses the whole save and nothing is left half written. */
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
			/*
			 * Somebody just put in the vault is re-read rather than dropped.
			 *
			 * Whether they leave the list is the SERVER's answer and it has two: with the vault shut
			 * they are concealed from every route that names them, and with it open they are still
			 * listed and simply marked. Dropping them here would be right for the first and wrong for
			 * the second: hiding somebody while the vault is open would take them off a screen that
			 * is showing hidden people quite happily, and only a reload would bring them back.
			 */
			await this.load();
			return merged;
		}
		this.items = this.items
			.map((person) => (person.id === id ? merged : person))
			.sort(bySeenThenName);
		return merged;
	}

	/* The heart and the stars, written optimistically.
	 *
	 * The value moves at once and is put back if the server disagrees, exactly as a tile's does:
	 * a control that waits for a round trip before it changes feels broken, and the request almost
	 * always succeeds. Putting it back matters more than it looks: a heart left showing a state the
	 * server never accepted is a lie with nothing on screen to reveal it.
	 */
	async setFavorite(id: string, favorite: boolean): Promise<void> {
		await this.#state(id, `/people/${id}/favorite`, { favorite });
	}

	async setRating(id: string, rating: number | null): Promise<void> {
		await this.#state(id, `/people/${id}/rating`, { rating });
	}

	/**
	 * Into the vault, or back out. This account's own, exactly as the heart and the stars are.
	 *
	 * Its own route, next to the one a Site has. `update` is the wrong route for it: a name and a
	 * record belong to the whole install, so that route is an admin's, and hiding somebody is the
	 * one opinion on a person that belongs to nobody but the viewer. Through `update`, a guest's
	 * Hide would be answered 403.
	 *
	 * No local edit and no re-read here. Whether they leave the wall is the SERVER's answer (with
	 * Hidden open they are still listed and simply marked, and with it shut they are gone from
	 * every route that names them), so the bell is rung and each screen re-reads with the question
	 * IT was asking. `load()` would fetch the first page unfiltered and so throw away whatever page
	 * and facets the wall was on.
	 */
	async setVault(id: string, vault: boolean): Promise<void> {
		await api.put<void>(`/people/${id}/vault`, { body: { vault } });
		this.#settled();
		libraryChanges.changed();
	}

	/** The still they are drawn as. Chosen from one of their own files; null clears it back to the
	 *  first the server would pick. The server checks the asset is one this viewer may see. */
	/**
	 * Point it at a file, and optionally at WHICH MOMENT of that file.
	 *
	 * The moment is null for a photograph and for "the file's own picture", which are one
	 * instruction as far as the server is concerned: no moment stored, so the still it already has
	 * is the one served. Sent with the file every time rather than separately: a moment belongs to
	 * the file it was taken from, and the server writes both columns in one statement so one cannot
	 * outlive the other.
	 */
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

	/**
	 * A cover picture from OUTSIDE the library, chosen from the disk.
	 *
	 * Multipart rather than JSON, because the body is a file. The reply is the same row a pick
	 * answers with, so the screen replaces what it is holding either way, which is what stops
	 * "the cover changed" being two different code paths depending on where the picture came from.
	 */
	async uploadCover(id: string, file: File): Promise<Person> {
		const form = new FormData();
		form.set('file', file);
		return this.#tookACover(
			id,
			await api.post<Person>(`/people/${id}/cover-picture`, { body: form })
		);
	}

	/* What both cover writes do with the row they get back. One method rather than two copies:
	   the two are the same answer arriving from two routes, and a difference between them would be
	   a wall that follows a pick and not an upload. */
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

	/**
	 * Say that these files came from these sites, or that they did not. Returns how many files.
	 *
	 * On the People store because the sites live here: a Site is read, made and renamed through
	 * `Sites` below, and this is the write that points files at one. The list is NOT reloaded
	 * afterwards, unlike the person assignment above: what changes is a count on each site's
	 * card, and the walls follow that through the change bus rather than by being told here.
	 *
	 * `add` has a false side because a picker row's tick has to be clearable. It is the same shape
	 * `assign` above has, and the same route: the server reads the flag and either files or
	 * unfiles. The per-file undo on a file's own record is a different question: WHICH handle
	 * posted it.
	 */
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

	/** Who a typed term names: by name, by an alias, or by the handle of a linked account.
	 *
	 * The server's answer, not a filter over `items`. An empty `people` is what the "is this
	 * another name for someone?" prompt reads to decide whether to offer itself.
	 */
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

	/**
	 * One page for a PICKER, alphabetical, filtered by what is being typed.
	 *
	 * See `Collections.choices`, which is the same method for the same reason: it answers the caller
	 * and keeps nothing, so a keystroke in a flyout cannot move the wall behind it, and it asks for
	 * `name_az` rather than the wall's own order because a picker is read by somebody who already
	 * has a name in mind. `total` comes back so the picker can say how many it is NOT showing.
	 */
	async choices(prefix = '', limit = PICK_PAGE): Promise<{ items: Person[]; total: number }> {
		const page = await api.get<PeoplePage>('/people', {
			query: { prefix, anywhere: 'true', limit, offset: 0, sort: 'name_az' }
		});
		// A locked row (a name the vault keeps back) is no choice: it would be a blank line in an
		// "Add to" list, and picking it would act on something whose name cannot be seen.
		const named = page.items.filter((one) => !one.locked);
		return { items: named, total: page.total - (page.items.length - named.length) };
	}

	/**
	 * One person, from the server, by id.
	 *
	 * A detail page reads this rather than picking its subject out of `items`. `items` is one page
	 * of the wall (a bounded number of rows in a chosen order), so anybody ranked past the cap
	 * is not in it, and anything that replaces the page (the vault opening or shutting, a reload, a
	 * later page being fetched) would take the subject out from under an already-open page: the
	 * page losing its hero, its buttons and its wall at once.
	 */
	async one(id: string): Promise<Person> {
		return api.get<Person>(`/people/${id}`);
	}

	/**
	 * Several people, from the server, by id: every one of them or a refusal.
	 *
	 * For the merge sheet, which is handed a SELECTION and must draw every one of it. Rows picked
	 * out of `items` would not do: `items` is one page of the wall, so somebody found by a search,
	 * or on a later page, would silently fall out. Two people picked would become one on the
	 * sheet, it would ask who to merge THAT one into, and the merge would run the wrong way round.
	 *
	 * All or nothing, because the sheet decides an irreversible act from what arrives: a set with
	 * one quietly missing is the fault above again. One read per id, side by side: a selection
	 * the merge route accepts is at most a hundred, and there is no list-by-id route to ask
	 * instead.
	 */
	async several(ids: string[]): Promise<Person[]> {
		return Promise.all(ids.map((id) => this.one(id)));
	}

	byId(id: string): Person | undefined {
		return this.items.find((person) => person.id === id);
	}

	/** Names beginning with what has been typed. Filtered here rather than refetched: the list is
	 * already loaded and a request per keystroke buys nothing. A term that matches nothing here
	 * still goes to `resolve`, which knows about aliases and handles and this does not. */
	matching(prefix: string): Person[] {
		const needle = prefix.trim().toLowerCase();
		if (!needle) return this.items;
		return this.items.filter((person) => person.name.toLowerCase().startsWith(needle));
	}

	/* People whose name contains this term, asked of the server, without touching the cached list.
	 *
	 * Asked rather than filtered, for two reasons that are both about the answer being wrong
	 * otherwise. The cached list is capped, so filtering it hides anybody past the cap, and with
	 * hundreds of people that is most of them. And the server is what knows how many files somebody
	 * is on and whether this account may be shown their cover picture, so a card built any other
	 * way would read "0 items" over a blank monogram for a person who has five.
	 *
	 * Left out of `items` deliberately: that list is shared with every other screen, and filtering
	 * it here would filter the suggestions on screens that never asked to be filtered.
	 */
	async matchingAnywhere(term: string, limit = 200): Promise<Person[]> {
		const page = await api.get<PeoplePage>('/people', {
			query: { prefix: term, anywhere: 'true', limit: String(limit) }
		});
		return page.items;
	}
}

/**
 * THE PEOPLE WALL'S SEARCH: the answer to a typed name, held apart from the wall's page, and
 * asked again whenever the page is.
 *
 * It is held apart because it is a different question (see `matchingAnywhere`): the whole answer to
 * "whose name contains this", unpaged, rather than a page of the library. That is also how it could
 * be left behind: a wall that re-read its PAGE when the library moved and after a merge, but drew
 * the SEARCH whenever one was typed, would keep a person just merged away on a filtered wall until
 * a reload. Anything that re-reads the wall re-reads this too, through `again`, which is the one
 * thing a screen holding a kept answer has to offer the bell.
 *
 * A class rather than three variables on the screen so that "which answer is current" is decided in
 * one place: a typed search and a re-read share one counter, so a re-read that lands after somebody
 * typed something new cannot put the older term's answer back.
 */
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

	/**
	 * Search for what was typed; empty clears the search.
	 *
	 * True when this answer is the one now on screen, false when a newer search overtook it. A
	 * failure of the current search is thrown for the screen to say so; an overtaken one is not.
	 */
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

	/**
	 * The search on screen, asked again: what the library bell calls, beside the wall's page.
	 *
	 * Nothing when no search is on screen. A failure keeps the answer that is there: a re-read that
	 * could not be made is not a reason to empty a list somebody is reading.
	 */
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

	/* Empty this cache, so the next screen that wants it fetches again.
	 *
	 * For the vault: what is concealed never arrives, so a list held from while the vault was open
	 * still holds it after the vault shuts. Clearing the rows as well as the flag matters: a
	 * screen reading `items` directly would otherwise draw the old ones until the fetch lands.
	 */
	forget(): void {
		this.items = [];
		this.total = 0;
		this.loaded = false;
	}

	/** How many this account may see in total, which is what the paginator is drawn from. */
	total = $state(0);

	/* Rising counter so a slow response cannot overwrite a newer one, and so a write that edits the
	 * list in place discards a page that was already in the air. Every list store in the client
	 * carries this: without it, paging twice quickly or changing the order twice could leave the wall
	 * showing whichever answer happened to come back last. */
	#generation = 0;

	#settled(): void {
		this.#generation += 1;
	}

	/** The order the wall is showing. See the People store above for why it lives here. */
	/* The order the wall opens in: how much of the library each one accounts for, most first.
	   The same order the server falls through to, under the name every wall calls it. 'seen' is
	   still the server's own default key and is not offered as a choice: a control cannot show
	   a chosen value that is not one of its options. */
	sort = $state<EntitySort>('largest');

	/*
	 * One page of the list, with a limit of its own.
	 *
	 * A request with no limit takes the route's own (a five-hundred-row SCAN cap meant for a
	 * suggester), and an install with more sites than that would simply end, with nothing on the
	 * page saying so and no way to reach the rest. The order is the server's already, so this wall
	 * needs only the page and not the sort.
	 */
	/** What the wall is filtered by. See `People.narrowing` above for why it lives on the store. */
	narrowing = $state<Record<string, string[]>>({});

	/**
	 * One page of the wall.
	 *
	 * `prefix` is what the search box above the wall has been typed into, and it is SENT rather
	 * than applied to the rows in hand: this is a page of a longer list, so filtering what has
	 * already arrived would quietly hide everything past the page. The pickers ask the same route
	 * the same way.
	 */
	/**
	 * Where the server actually started the page it last handed back.
	 *
	 * Sent an anchor rather than an offset, the client does not know the answer until it arrives,
	 * and the pager has to read the real one, or a link would open the right rows under a readout
	 * claiming they are the first. The People store's own field, for the People wall's own reason.
	 */
	at = $state(0);

	/**
	 * The wall's page, through the wall's own paging rather than at a size handed in.
	 *
	 * A size handed in would ask at the fallback count on a first visit and again, whole, once a
	 * card has been measured. Through `CardPaging.fill` the second ask trims the rows held or asks
	 * for the remainder only, and an anchored arrival lands its offset without asking again. `load`
	 * stays for the other screens that read this list at a size of their own.
	 */
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
		 * server against the same scoped, ordered list the page comes out of. Only the server knows
		 * that list; working it out from a page already in hand would give a position in the page. */
		from: string | null = null
	): Promise<void> {
		const generation = ++this.#generation;
		this.sort = sort;
		this.narrowing = narrowing;
		this.loading = true;
		this.failed = false;
		try {
			const page = await api.get<components['schemas']['SiteList']>('/sites', {
				// The filtering first; the paging parameters are this store's and cannot be displaced.
				// One of `offset` and `from`, never both. See the Tags store.
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

	/**
	 * One page for a PICKER, alphabetical, filtered by what is being typed.
	 *
	 * See `Collections.choices`, which is the same method for the same reason: it answers the caller
	 * and keeps nothing, so a keystroke in a flyout cannot move the wall behind it, and it asks for
	 * `name_az` rather than the wall's own order because a picker is read by somebody who already
	 * has a name in mind. `total` comes back so the picker can say how many it is NOT showing.
	 */
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
		/* The details ride on the create, so a refused address or parent refuses the whole thing and
		   no bare Site is left behind; left out when the caller has none. */
		const created = await api.post<Site>('/sites', { body: { name, ...(details ?? {}) } });
		// The name is unique without regard to case, so this may be a site that already existed.
		// Replacing by id rather than appending keeps one row for one site.
		this.#settled();
		const held = this.items.length;
		this.items = [...this.items.filter((p) => p.id !== created.id), created].sort(byName);
		// Only when it really is a new row. This replaces by id, so asking for a site that already
		// exists leaves the length where it was and must leave the total there too.
		if (this.items.length > held) this.total += 1;
		return created;
	}

	/**
	 * A Site's whole record in one write: its name, and with `record` its notes, other names,
	 * parent and addresses.
	 *
	 * One request, so the server answers every refusal (a taken name, a parent that makes a loop,
	 * an address that is not one) before it writes anything, and a save lands whole or not at all.
	 * The reply carries the record, the parent's id included, which only the server can know.
	 */
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
		// A rename does not recount, and the reply carries neither number, so the counts this
		// list already holds are kept. Taking the reply's would blank both the moment somebody
		// corrected a spelling, which is the shape the write-reply comment on the server warns about.
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
	/* The same optimistic pair the People store has, on the same shape of row. See there for why
	 * the value moves first and is put back on a refusal. */
	async setFavorite(id: string, favorite: boolean): Promise<void> {
		await this.#state(id, `/sites/${id}/favorite`, { favorite });
	}

	async setRating(id: string, rating: number | null): Promise<void> {
		await this.#state(id, `/sites/${id}/rating`, { rating });
	}

	/**
	 * Whatever somebody wrote about the site, and (with the record) its addresses.
	 *
	 * The site's address is the FIRST of `record.links` (the server's `sites.SITE_ADDRESS`); there
	 * is no separate address column to send it to. The wire still names the address `site_url` on the way OUT, which is what every card reads.
	 */
	async setDetails(
		id: string,
		notes: string | null,
		record?: { aliases: string[]; parent: string | null; links?: string[] }
	): Promise<void> {
		/* The record half is left OUT when it was not given, never sent as null.
		 *
		 * The server reads absence as "leave those alone" and a value as "make it this", which is
		 * the only way one route can serve both a screen that edits the notes and one that means
		 * to clear every other name. */
		const body: Record<string, unknown> = { notes };
		if (record) {
			body.aliases = record.aliases;
			body.parent = record.parent;
			/* Every address, when the caller had the whole list. Left out otherwise, so a screen
			   that edits only the first one does not read as "these are all of them now". */
			if (record.links) body.links = record.links;
		}
		await api.put<void>(`/sites/${id}/details`, { body });
		/* The notes only: which link is FIRST is the server's to say (it puts the site's own home
		   first), and every screen that draws the address reads the row again. */
		this.#apply(id, { notes });
	}

	/** The still the site is drawn as. Chosen from one of its own files; null clears it. */
	/**
	 * Point it at a file, and optionally at WHICH MOMENT of that file.
	 *
	 * The moment is null for a photograph and for "the file's own picture", which are one
	 * instruction as far as the server is concerned: no moment stored, so the still it already has
	 * is the one served. Sent with the file every time rather than separately: a moment belongs to
	 * the file it was taken from, and the server writes both columns in one statement so one cannot
	 * outlive the other.
	 */
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

	/**
	 * A cover picture from OUTSIDE the library, chosen from the disk.
	 *
	 * Multipart rather than JSON, because the body is a file. The reply is the same row a pick
	 * answers with, so the screen replaces what it is holding either way, which is what stops
	 * "the cover changed" being two different code paths depending on where the picture came from.
	 */
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

	/**
	 * Into the vault, or back out.
	 *
	 * Re-reads afterwards rather than editing the row: hiding a site takes it off this wall, takes
	 * its usernames off theirs, and conceals every file attributed to it, so there is no local edit
	 * that leaves the screen honest.
	 */
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
