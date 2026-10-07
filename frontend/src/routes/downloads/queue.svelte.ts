/* What the download queue screen knows, and how it asks the server for it.
 *
 * The one connection this application holds says when the queue has changed and this asks then,
 * which covers the half that is never written down as well, because how far along a transfer is,
 * is held in memory and announced from there. So a queue with nothing in it costs nothing, and a
 * running download costs one read a second.
 *
 * The one distinction worth keeping is refused-versus-unreachable. A guest who reaches this
 * address (the nav item is hidden from them, but the address is typeable) gets a 403 from the
 * list, and that is a stop, not a thing to retry. Anything else is the server being briefly
 * unreachable, and the read simply comes back.
 *
 * ## ONE list, with a tab saying which part of it is showing
 *
 * A queue that reorders itself under the pointer is unreadable, and the freeze belongs to the
 * LIST COMPONENT, which holds its order while a pointer or the keyboard is inside it. So there is
 * one list, filtered by a state tab, and the tabs carry their counts: a count is only worth
 * reading next to the other counts, and one list has one empty state, one search, and one way to
 * look at everything that needs somebody.
 *
 * ## Filtered by the SERVER, because the list is paged
 *
 * The tab, the Site, the search and the order are all parameters of the one read; filtering only
 * the rows the first page holds would draw a fraction of "Done" under its count and never find
 * anything older. What this holds is the page the server chose; `matched` is how many there are
 * to page through.
 */

import { counted } from '$lib/entity/entity-counts';
import { api, ApiError, type ApiPath } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { SITE_ORDER, SORT_OPTIONS } from '$lib/grid/sort-state.svelte';
import type { components } from '$lib/api/schema';
import { toasts } from '$lib/shell/toasts.svelte';

/* LIVE: followed by routes/downloads/+page.svelte (refresh on the downloads bell) */

/** How far along a running download is.
 *
 *  Absent for anything not running. A bar left under a finished row is a stale number that reads as
 *  authoritative, and a total nobody knows is absent rather than zero: no total means no bar to
 *  draw, while a zero would mean a bar already full. */
export type DownloadProgress = components['schemas']['DownloadProgress'];

export type DownloadItem = components['schemas']['DownloadItem'];

/** How the queue as a whole is doing.
 *
 *  One line above the list rather than fifty rows each ticking. It is the figure that has to update
 *  fastest, and it is one read on the server, so a queue of five hundred costs what a queue of one
 *  costs. */
export type QueueSummary = components['schemas']['QueueSummary'];

/** What a paste of several links did: how many were queued, and every line that was not a link. */
export type PastedLinks = components['schemas']['PastedLinks'];

/**
 * What the page decides FOR THIS PASTE, sent with it: the page decides for this download,
 * Settings decides the default for every download.
 *
 * `dest` is the folder ('' or absent for the default). `remember` is the switch beside it: it
 * STARTS from the setting of the same name, and a value here is this paste's own answer, written
 * on each row it makes; the stored setting is never touched. Absent or
 * null sends nothing, and the job then follows the setting as it stands when it runs, which is
 * what a drop and the browser extension get.
 */
export interface PasteChoices {
	dest?: string;
	remember?: boolean | null;
}

/** A paste's choices in the words the routes take. One place, so the four routes cannot drift. */
function choiceBody(choices: PasteChoices): Record<string, unknown> {
	return {
		dest_folder_id: choices.dest || null,
		remember: choices.remember ?? null
	};
}

/** What to tell somebody about a paste, or nothing when it all went in.
 *
 *  Everything the paste DID NOT do, in one sentence. A paste of forty that queues thirty-seven is
 *  a success with three facts attached, and leaving any of them out is the screen quietly deciding
 *  somebody did not need to know: the refusals are named one by one because "3 were refused" says
 *  which nothing, the repeats are counted because a repeat is not something to act on, and the
 *  overflow is counted because what is left is still sitting in the box.
 */
function said(answer: PastedLinks): string | undefined {
	const notes: string[] = [];
	if (answer.refused.length > 0) {
		notes.push(`These were not links: ${answer.refused.map((one) => one.url).join(', ')}`);
	}
	if (answer.duplicates > 0) {
		notes.push(
			answer.duplicates === 1
				? 'One was already in the list'
				: `${answer.duplicates} were already in the list`
		);
	}
	if (answer.left_over.length > 0) {
		notes.push(`${answer.left_over.length} didn't fit in one paste and are still in the box`);
	}
	if (notes.length === 0) return undefined;
	return `${counted(answer.queued)} queued. ${notes.join('. ')}.`;
}

/**
 * The lines of a paste that could each be an address.
 *
 * A list copied out of a page or a notes file arrives with blank lines and trailing spaces in it,
 * and those are not something to complain at somebody about. Split on newlines only: a space is
 * legal inside a URL once it is encoded, and splitting on whitespace would cut one address into two
 * halves that are each refused.
 *
 * Here rather than on the box, because two callers need the same answer: the box counts them to
 * label its button, and the page decides from the same count whether to send one link or a list.
 * Two copies of a splitting rule is two answers to "how many links is this".
 */
export function lines(text: string): string[] {
	return text
		.split(/[\r\n]+/)
		.map((one) => one.trim())
		.filter((one) => one.length > 0);
}

type DownloadsPage = components['schemas']['DownloadsPage'];

/** What a playlist or channel address turned out to hold. Reported before anything is queued. */
export type BulkPreview = components['schemas']['BulkPreview'];

const NOT_ALLOWED = "You aren't signed in as an admin.";

/** The states that mean there is still work to happen. Everything else is history.
 *
 *  `paused` is one of them, which is the whole of what separates it from `canceled`: the row is
 *  held, not finished, its bytes are still in the staging folder and one press puts it back in the
 *  queue. So it counts under Active, it is not removable from the list, and the summary's own
 *  count of outstanding work includes it: all three from this one line. */
const ACTIVE = new Set(['queued', 'running', 'blocked', 'paused']);

/** Which part of the queue is showing. The tabs' ids and the server's `show`, so the tab, the
 *  address and the read cannot drift. */
export type QueueFilter = 'all' | 'active' | 'needs' | 'done' | 'failed';

/** Every value of `QueueFilter`, for reading one out of an address without trusting it. */
export const FILTERS: readonly QueueFilter[] = ['all', 'active', 'needs', 'done', 'failed'];

/*
 * HOW THE LIST IS ORDERED, in the words and under the glyphs every other wall uses.
 *
 * These are KEYS, taken out of the shared list by name the way the folder view's and the entity
 * walls' are, and the server's `sort` takes the same keys; the glyph is looked up by key
 * (`sortIcon`). A download's size is the bytes of the file it landed, so `Largest file` is the true
 * word here and not the count the entity walls say. The one order no other wall has is the Site's,
 * and its words and glyph are declared beside the rest (`SITE_ORDER`) rather than here.
 */
const QUEUE_SORT_KEYS = [
	'newest',
	'oldest',
	'name_az',
	'name_za',
	'largest',
	'smallest',
	'site'
] as const;

/** How the list is ordered. The server's `sort`. */
export type QueueSort = (typeof QUEUE_SORT_KEYS)[number];

function shared(key: QueueSort): { value: QueueSort; label: string } {
	const found = [...SORT_OPTIONS, SITE_ORDER].find((option) => option.value === key);
	// Fires the moment a key is renamed in the shared list and not here: a hole, never a crash.
	if (!found) throw new Error(`no sort option is spelled ${key}`);
	return { value: key, label: found.label };
}

/** The orders, in the words the menu says them. The first is what the list is at rest. */
export const SORTS: readonly { value: QueueSort; label: string }[] = QUEUE_SORT_KEYS.map(shared);

/*
 * The spellings this screen's address carried before its keys became the shared ones, read as
 * what they meant: a link somebody kept to "by size" still opens by size rather than quietly
 * newest first. Nothing writes them.
 */
const FORMER_KEYS: Readonly<Record<string, QueueSort>> = { name: 'name_az', size: 'largest' };

/** The order an address asks for, or the list at rest for anything this screen does not offer. */
export function queueSortFrom(value: string | null): QueueSort {
	if (value === null) return 'newest';
	return SORTS.find((one) => one.value === value)?.value ?? FORMER_KEYS[value] ?? 'newest';
}

/** How many rows a page holds. The server's own default page, so one number is being paged by. */
export const PAGE = 50;

/*
 * WHAT A DOWNLOAD CALLS THE FOLDER IT GOES TO, by where it has got to: one rule, every place.
 *
 * "Saving to" is a claim that bytes are moving into that folder NOW. So the present tense belongs
 * to a running download alone, the past tense to one whose file is there, and everything else
 * (queued, waiting on cookies, paused, failed, cancelled, skipped) names the folder it WOULD use,
 * which is also what the setting that chose it is called.
 *
 * A duplicate counts as landed: the file it would have fetched is already in the library, which is
 * the same reason a re-paste of it is skipped.
 */
type DestinationWord = 'Saving to' | 'Saved to' | 'Save to';

const LANDED = new Set(['done', 'duplicate']);

export function destinationWord(status: string): DestinationWord {
	if (status === 'running') return 'Saving to';
	if (LANDED.has(status)) return 'Saved to';
	return 'Save to';
}

/**
 * A row that is waiting for a person rather than for the machine.
 *
 * Two states and no more. `blocked` is the queue's only way of saying it wants cookies: the
 * service says so in as many words ("a blocked job means the download is waiting", and the row
 * badge reads "Waiting for cookies"), and a failure can be tried again, which is a verb somebody
 * has to press.
 *
 * `canceled` is NOT here: somebody already decided that one, and a list of things needing attention
 * that fills up with decisions already taken is a list nobody reads twice. Nor is `quarantined`,
 * and that one is deliberate against appearances: the same bytes would arrive and be quarantined
 * again, so Try again is the wrong verb for it and the badge that draws it says so.
 */
export function needsYou(item: DownloadItem): boolean {
	return item.status === 'blocked' || item.status === 'failed';
}

/* WHICH VERBS A ROW CAN TAKE, in one place.
 *
 * The row draws them and the selection bar offers them, and the bar's whole rule is that it names
 * only what EVERY picked row can take, so the two have to agree about each one. Written out in
 * both files they would agree until the day one of them learned a new state. */

/** Moving up the queue means anything at all only for something that has not started. */
export function canGoFirst(item: DownloadItem): boolean {
	return item.status === 'queued';
}

/** A download can be stopped while it is still on its way. Once settled there is nothing to stop.
 *
 *  A PAUSED one can be stopped too, and that is not a contradiction: pausing keeps the bytes for
 *  later and cancelling throws them away, so the two are different answers to different questions
 *  and a row held for a week still wants a way out of the list. */
export function canCancel(item: DownloadItem): boolean {
	return ACTIVE.has(item.status);
}

/** Holding one where it stands: only something the machine is actually about to do, or doing.
 *
 *  Not `blocked`, and that is worth saying because it looks like the same thing from the outside.
 *  A blocked row is already stopped (it is waiting for cookies and will not move until somebody
 *  brings some), so pausing it would be a press that changes nothing and takes away the badge
 *  that says what it is waiting for. */
export function canPause(item: DownloadItem): boolean {
	return item.status === 'running' || item.status === 'queued';
}

/** Letting it go again. Only for one that was held: everything else is already on its way or over. */
export function canResume(item: DownloadItem): boolean {
	return item.status === 'paused';
}

/** Trying again only means something for one that did not work. */
export function canRetry(item: DownloadItem): boolean {
	return ['failed', 'canceled', 'quarantined'].includes(item.status);
}

/** Taking a row off the list. Only a settled one: the way to be rid of a live download is Cancel,
 *  and a row that vanished while it was still fetching would leave the fetch running unwatched. */
export function canRemove(item: DownloadItem): boolean {
	return !ACTIVE.has(item.status);
}

/**
 * What went wrong, in words, whatever kind of failure it was.
 *
 * One function because every caller below wants the same sentence: the server's own explanation
 * where there is one, and `UNREACHABLE` where the request never arrived.
 */
function describe(error: unknown): string {
	if (error instanceof ApiError) return error.detail ?? error.message;
	return "Couldn't reach Sift. Check that it's still running.";
}

export class DownloadQueue {
	items = $state<DownloadItem[]>([]);
	total = $state(0);
	summary = $state<QueueSummary | null | undefined>(null);
	/** Kept, not thrown: a read that fails leaves the last good list on screen and says why. */
	problem = $state<string | null>(null);
	/** Set when the server says this account may not look. The nav item is hidden from a guest but
	 *  the address is typeable, and asking again on their behalf would be asking to be refused. */
	refused = $state(false);
	/** Set under the submit box when a pasted link is refused. */
	submitError = $state<string | undefined>(undefined);
	busy = $state(false);

	/** What somebody is looking for. The whole queue, not only the finished part: a failure
	 *  from a site somebody remembers is exactly the row they came here to find. */
	search = $state('');

	/** Which state tab is open. Everything, until somebody filters it. The page keeps it in the
	 *  address and hands it here; see `+page.svelte`. */
	filter = $state<QueueFilter>('all');

	/** Which Sites the list is filtered to, by name: either of them. Empty is every Site. The
	 *  filter panel's Site column writes them into the address, and the page hands them here. */
	siteNames = $state<string[]>([]);

	/** The order the list is in. */
	sort = $state<QueueSort>('newest');

	/** Where the page starts, counting from zero. */
	offset = $state(0);

	/** How many rows the filtering keeps: what the pager divides into pages. */
	matched = $state(0);

	/** Each shown state and how many rows wear it, inside the chosen Site. */
	counts = $state<Record<string, number>>({});

	/**
	 * The state tabs above the list, each with what it would show.
	 *
	 * The counts are the SERVER's, over the whole queue inside the chosen Site, and NOT of the
	 * search. That is the one decision in here worth stating: a tab whose count changed as somebody typed
	 * would be answering "how many failures match this search", and the question a tab answers is
	 * "how many failures are there". The list under it is what the search filters.
	 *
	 * "Needs you" overlaps Active and Failed on purpose. It is not a sixth state: it is the one
	 * question this screen exists to answer, gathered from wherever it lives.
	 */
	/**
	 * Every download inside the chosen Sites, in every state: the All tab's count, and the heading's.
	 * One getter, so a Site left out of the filter leaves the two figures agreeing. `total` stays the
	 * whole queue, which is what decides whether the queue is empty at all.
	 */
	get inSites(): number {
		return this.tabs[0].count;
	}

	get tabs(): { id: QueueFilter; label: string; count: number }[] {
		/* The server's counts when it has answered; the rows on this page only until then.
		   Counts taken from the page would say 50 under a header saying several hundred. */
		const whole = Object.keys(this.counts).length > 0 ? this.counts : this.summary?.by_state;
		const of = (each: (item: DownloadItem) => boolean) => this.items.filter(each).length;
		const many = (...states: string[]) =>
			states.reduce((sum, state) => sum + (whole?.[state] ?? 0), 0);
		if (whole && Object.keys(whole).length > 0) {
			return [
				{ id: 'all', label: 'All', count: many(...Object.keys(whole)) },
				{ id: 'active', label: 'Active', count: many(...ACTIVE) },
				{ id: 'needs', label: 'Needs you', count: many('blocked', 'failed') },
				{ id: 'done', label: 'Done', count: many('done') },
				{ id: 'failed', label: 'Failed', count: many('failed') }
			];
		}
		return [
			{ id: 'all', label: 'All', count: this.items.length },
			{ id: 'active', label: 'Active', count: of((item) => ACTIVE.has(item.status)) },
			{ id: 'needs', label: 'Needs you', count: of(needsYou) },
			{ id: 'done', label: 'Done', count: of((item) => item.status === 'done') },
			{ id: 'failed', label: 'Failed', count: of((item) => item.status === 'failed') }
		];
	}

	/**
	 * The rows to draw: the page the server chose for the tab, the Site, the search and the order.
	 *
	 * Nothing is filtered here. The name a row is drawn under is among what the search reads,
	 * server-side, since somebody searching a download manager is nearly always looking for a file
	 * they remember the name of.
	 */
	get shown(): DownloadItem[] {
		return this.items;
	}

	/**
	 * Filter or re-order, and read the first page of what that leaves.
	 *
	 * Back to the first page on every change, because the page somebody was on is a position in a
	 * list that no longer exists: page four of "Done" is not a place in "Failed".
	 */
	async narrow(
		changes: Partial<{
			filter: QueueFilter;
			siteNames: readonly string[];
			sort: QueueSort;
			search: string;
		}>
	): Promise<void> {
		if (changes.filter !== undefined) this.filter = changes.filter;
		if (changes.siteNames !== undefined) this.siteNames = [...changes.siteNames];
		if (changes.sort !== undefined) this.sort = changes.sort;
		if (changes.search !== undefined) this.search = changes.search;
		this.offset = 0;
		await this.refresh();
	}

	/** Turn to the page starting at `offset`. */
	async turn(offset: number): Promise<void> {
		this.offset = Math.max(0, offset);
		await this.refresh();
	}

	/* Which read is the newest one asked. The list is read on every move of the queue AND on every
	   filter, so two can be in flight at the same time, and an answer for the tab somebody has just
	   left, landing after the one for the tab they pressed, would draw the wrong rows under the
	   right tab. Only the newest answer is kept. */
	#asked = 0;

	async refresh(): Promise<void> {
		if (this.refused) return;
		const asked = ++this.#asked;
		try {
			const page = await api.get<DownloadsPage>('/downloads', {
				query: {
					limit: PAGE,
					offset: this.offset,
					show: this.filter,
					site: this.siteNames.length > 0 ? this.siteNames : undefined,
					q: this.search.trim() || undefined,
					sort: this.sort
				}
			});
			if (asked !== this.#asked) return;
			this.items = page.downloads;
			this.total = page.total;
			this.summary = page.summary;
			this.matched = page.matched ?? page.total;
			this.counts = page.counts ?? {};
			this.problem = null;
			/* A page past the end, which is what removing the last rows of the last page leaves:
			   step back to the last page there is rather than drawing an empty one under a pager
			   saying there are more. */
			if (this.items.length === 0 && this.offset > 0 && this.matched > 0) {
				this.offset = Math.max(0, Math.floor((this.matched - 1) / PAGE) * PAGE);
				await this.refresh();
			}
		} catch (error) {
			if (asked !== this.#asked) return;
			// A refusal is the one thing not to ask again about: the address is admin-only and the
			// caller is not one. Everything else is the server being briefly unreachable, so the
			// next announcement brings it back and the last good list stays put in the meantime.
			if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
				this.problem = NOT_ALLOWED;
				this.refused = true;
			} else if (error instanceof ApiError) {
				this.problem = error.message;
			} else {
				this.problem = UNREACHABLE;
			}
		}
	}

	/**
	 * Queue a download from a pasted link. Returns a refusal to show under the box, or undefined.
	 *
	 * SET as well as returned: the box below the input draws from `submitError`, so a refusal that
	 * was only returned (an unsupported site, a malformed address) would show nothing at all. A
	 * store method that hands a message back to a caller with nowhere to put it is a message nobody
	 * reads.
	 */
	async submit(url: string, choices: PasteChoices = {}): Promise<string | undefined> {
		this.busy = true;
		this.submitError = undefined;
		try {
			await api.post('/downloads', { body: { url, ...choiceBody(choices) } });
			await this.refresh();
			return undefined;
		} catch (error) {
			this.submitError = describe(error);
			return this.submitError;
		} finally {
			this.busy = false;
		}
	}

	/** Queue several pasted addresses in one go, one download each.
	 *
	 *  Separate from `submit` rather than a loop over it, and the difference is what it can say. A
	 *  paste of forty has to report what it refused as well as what it took, and one bad line must
	 *  never lose the other thirty-nine, which a loop that stopped at the first refusal would do.
	 *
	 *  The refusals come back as a sentence on `submitError` because that is where the box already
	 *  draws one. Naming them is the point: "3 were refused" tells nobody which three. */
	async submitMany(urls: string[], choices: PasteChoices = {}): Promise<PastedLinks | undefined> {
		this.busy = true;
		this.submitError = undefined;
		try {
			const answer = await api.post<PastedLinks>('/downloads/links', {
				body: { urls, ...choiceBody(choices) }
			});
			await this.refresh();
			this.submitError = said(answer);
			return answer;
		} catch (error) {
			this.submitError = describe(error);
			return undefined;
		} finally {
			this.busy = false;
		}
	}

	/** Ask what a playlist or channel address holds, without fetching any of it.
	 *
	 *  Nothing is queued by this. Taking everything a creator has posted is a decision worth making
	 *  with the number in front of you, and the listing costs one request and no media. */
	async preview(url: string, choices: PasteChoices = {}): Promise<BulkPreview | undefined> {
		this.busy = true;
		this.submitError = undefined;
		try {
			return await api.post<BulkPreview>('/downloads/bulk-preview', {
				body: { url, ...choiceBody(choices) }
			});
		} catch (error) {
			this.submitError = describe(error);
			return undefined;
		} finally {
			this.busy = false;
		}
	}

	/** Go ahead with everything behind that address, queued one download per item. */
	async queueAll(url: string, choices: PasteChoices = {}): Promise<string | undefined> {
		this.busy = true;
		this.submitError = undefined;
		try {
			await api.post('/downloads/bulk', { body: { url, ...choiceBody(choices) } });
			await this.refresh();
			return undefined;
		} catch (error) {
			// Set here as well as returned, for the same reason `submit` is: a caller putting it on
			// screen by hand is a thing a later caller forgets to do.
			this.submitError = describe(error);
			return this.submitError;
		} finally {
			this.busy = false;
		}
	}

	/** Cancel a download in flight. The server answers 204 whether or not it was still cancelable, so
	 * a click that races the download finishing is harmless; the next read shows the settled status. */
	async cancel(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/cancel`);
	}

	/** Fetch a link the ledger already had a record of. The row goes back into the queue. */
	async anyway(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/anyway`);
	}

	/** Try a failed download again, as itself rather than as a second record of the same link. */
	async retry(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/retry`);
	}

	/** Move a waiting download to the front of the queue. Nothing happens to one already running. */
	async first(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/first`);
	}

	/** Hold a download where it is. What has been fetched stays on disk, waiting for Resume.
	 *
	 *  A running one stops at the next moment its fetcher can be asked to; a waiting one simply
	 *  never starts. Both answer the same address, because from this screen they are one act. */
	async pause(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/pause`);
	}

	/** Let a held download go again, from where it stopped rather than from the beginning. */
	async resume(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/resume`);
	}

	/**
	 * Take a settled row off the list. The download is not undone and no file is touched.
	 *
	 * The server hides the row rather than deleting the record (what was downloaded is history and
	 * history is not editable), so this is the one way a finished row leaves a list that otherwise
	 * keeps everything for ever.
	 */
	async remove(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/remove`);
	}

	/** Put a removed row back on the list. Exactly the way out of Remove, and nothing else. */
	async restore(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/restore`);
	}

	/**
	 * Take rows off the list and say so, with the one press back.
	 *
	 * The row is hidden rather than deleted, and `restore` un-hides it, so the Undo can be
	 * honoured.
	 *
	 * Here rather than on the page, because the row's own verb and the selection bar's both end up
	 * here and the sentence has to be the same one. It is the shape `hiding.ts` already uses for
	 * exactly this pairing: the act, the sentence, and the way back, written together.
	 */
	async removeRows(ids: string[]): Promise<void> {
		for (const id of ids) await this.remove(id);
		const said = ids.length === 1 ? 'Removed from the list' : `${ids.length} removed from the list`;
		toasts.show(said, {
			action: {
				label: 'Undo',
				run: () => {
					void (async () => {
						for (const id of ids) await this.restore(id);
					})();
				}
			}
		});
	}

	/** One row action: ask, then re-read.
	 *
	 *  Deliberately quiet on failure. Every one of these is idempotent on the server and the next
	 *  read keeps the list honest, so a message would be a second, slower answer to a question the
	 *  row itself is about to answer. */
	async #act(path: ApiPath): Promise<void> {
		try {
			await api.post(path);
			await this.refresh();
		} catch {
			// The row is unchanged, and the next read says so.
		}
	}
}
