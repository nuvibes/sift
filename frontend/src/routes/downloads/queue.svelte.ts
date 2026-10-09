/* What the download queue screen knows, and how it asks the server for it. */

import { counted } from '$lib/entity/entity-counts';
import { api, ApiError, type ApiPath } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { SITE_ORDER, SORT_OPTIONS } from '$lib/grid/sort-state.svelte';
import type { components } from '$lib/api/schema';
import { toasts } from '$lib/shell/toasts.svelte';

/* LIVE: followed by routes/downloads/+page.svelte (refresh on the downloads bell) */

/** How far along a running download is. Absent for anything not running. */
export type DownloadProgress = components['schemas']['DownloadProgress'];

export type DownloadItem = components['schemas']['DownloadItem'];

/** How the queue as a whole is doing. One line above the list rather than fifty rows each
 * ticking. */
export type QueueSummary = components['schemas']['QueueSummary'];

/** What a paste of several links did: how many were queued, and every line that was not a link. */
export type PastedLinks = components['schemas']['PastedLinks'];

/** What the page decides FOR THIS PASTE, sent with it: the page decides for this download,
 * Settings decides the default for every download. */
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

/** What to tell somebody about a paste, or nothing when it all went in. */
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

/** The lines of a paste that could each be an address. */
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

/** The states that mean there is still work to happen. */
const ACTIVE = new Set(['queued', 'running', 'blocked', 'paused']);

/** Which part of the queue is showing. The tabs' ids and the server's `show`, so the tab, the
 *  address and the read cannot drift. */
export type QueueFilter = 'all' | 'active' | 'needs' | 'done' | 'failed';

/** Every value of `QueueFilter`, for reading one out of an address without trusting it. */
export const FILTERS: readonly QueueFilter[] = ['all', 'active', 'needs', 'done', 'failed'];

/* HOW THE LIST IS ORDERED, in the words and under the glyphs every other wall uses. */
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

/* The spellings this screen's address carried before its keys became the shared ones, read as
 * what they meant: a link somebody kept to "by size" still opens by size rather than quietly
 * newest first. */
const FORMER_KEYS: Readonly<Record<string, QueueSort>> = { name: 'name_az', size: 'largest' };

/** The order an address asks for, or the list at rest for anything this screen does not offer. */
export function queueSortFrom(value: string | null): QueueSort {
	if (value === null) return 'newest';
	return SORTS.find((one) => one.value === value)?.value ?? FORMER_KEYS[value] ?? 'newest';
}

/** How many rows a page holds. The server's own default page, so one number is being paged by. */
export const PAGE = 50;

/* WHAT A DOWNLOAD CALLS THE FOLDER IT GOES TO, by where it has got to: one rule, every place. */
type DestinationWord = 'Saving to' | 'Saved to' | 'Save to';

const LANDED = new Set(['done', 'duplicate']);

export function destinationWord(status: string): DestinationWord {
	if (status === 'running') return 'Saving to';
	if (LANDED.has(status)) return 'Saved to';
	return 'Save to';
}

/** A row that is waiting for a person rather than for the machine. */
export function needsYou(item: DownloadItem): boolean {
	return item.status === 'blocked' || item.status === 'failed';
}

/* WHICH VERBS A ROW CAN TAKE, in one place. */

/** Moving up the queue means anything at all only for something that has not started. */
export function canGoFirst(item: DownloadItem): boolean {
	return item.status === 'queued';
}

/** A download can be stopped while it is still on its way. */
export function canCancel(item: DownloadItem): boolean {
	return ACTIVE.has(item.status);
}

/** Holding one where it stands: only something the machine is actually about to do, or doing. */
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

/** What went wrong, in words, whatever kind of failure it was. */
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
	/** Set when the server says this account may not look. */
	refused = $state(false);
	/** Set under the submit box when a pasted link is refused. */
	submitError = $state<string | undefined>(undefined);
	busy = $state(false);

	/** What somebody is looking for. The whole queue, not only the finished part: a failure
	 *  from a site somebody remembers is exactly the row they came here to find. */
	search = $state('');

	/** Which state tab is open. Everything, until somebody filters it. */
	filter = $state<QueueFilter>('all');

	/** Which Sites the list is filtered to, by name: either of them. */
	siteNames = $state<string[]>([]);

	/** The order the list is in. */
	sort = $state<QueueSort>('newest');

	/** Where the page starts, counting from zero. */
	offset = $state(0);

	/** How many rows the filtering keeps: what the pager divides into pages. */
	matched = $state(0);

	/** Each shown state and how many rows wear it, inside the chosen Site. */
	counts = $state<Record<string, number>>({});

	/** The state tabs above the list, each with what it would show. */
	/** Every download inside the chosen Sites, in every state: the All tab's count, and the
	 * heading's. */
	get inSites(): number {
		return this.tabs[0].count;
	}

	get tabs(): { id: QueueFilter; label: string; count: number }[] {
		/* The server's counts when it has answered; the rows on this page only until then. */
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

	/** The rows to draw: the page the server chose for the tab, the Site, the search and the
	 * order. */
	get shown(): DownloadItem[] {
		return this.items;
	}

	/** Filter or re-order, and read the first page of what that leaves. */
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

	/* Which read is the newest one asked. */
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
			// caller is not one.
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

	/** Queue a download from a pasted link. Returns a refusal to show under the box, or
	 * undefined. */
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

	/** Queue several pasted addresses in one go, one download each. */
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

	/** Ask what a playlist or channel address holds, without fetching any of it. */
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

	/** Hold a download where it is. What has been fetched stays on disk, waiting for Resume. */
	async pause(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/pause`);
	}

	/** Let a held download go again, from where it stopped rather than from the beginning. */
	async resume(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/resume`);
	}

	/** Take a settled row off the list. The download is not undone and no file is touched. */
	async remove(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/remove`);
	}

	/** Put a removed row back on the list. Exactly the way out of Remove, and nothing else. */
	async restore(id: string): Promise<void> {
		await this.#act(`/downloads/${id}/restore`);
	}

	/** Take rows off the list and say so, with the one press back. */
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

	/** One row action: ask, then re-read. Deliberately quiet on failure. */
	async #act(path: ApiPath): Promise<void> {
		try {
			await api.post(path);
			await this.refresh();
		} catch {
			// The row is unchanged, and the next read says so.
		}
	}
}
