/* Duplicate-finding, as the screens that show it ask the server about it.
 *
 * Two lists that answer two different questions. The review queue holds GROUPS of files that
 * *might* be the same and need a person to say; reclaim holds files that already *are* the same
 * and only need somebody to choose which copies to keep.
 *
 * It lives under settings-ui though both halves are drawn on the Organize board: Near
 * Duplicates and Exact Duplicates. It is ONE module rather than two because the two halves share
 * how a size is written, how a pair is named and how an asset's facts are fetched and cached;
 * splitting it would be two copies of that drifting apart. Maintenance reads it for the counts
 * beside its settings.
 *
 * Nothing here deletes on its own, and nothing here can. Removing a file is a separate request
 * that only ever goes out because somebody pressed something, and the server refuses it outright
 * rather than destroying it.
 */

import { api, ApiError, type ApiPath } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import * as facts from '$lib/library/facts';
import type { components } from '$lib/api/schema';
import { taskList } from '$lib/jobs/tasks.svelte';
import { asked, type PageAsk } from '$lib/grid/anchor';
import type { CardPaging } from '$lib/grid/cards.svelte';

/** The duplicate sweep, as Tasks names it. */
const DUPLICATES_TASK = 'duplicates';

export type Copy = components['schemas']['CopyView'];

export type Redundancy = components['schemas']['RedundancyView'];

/** One group of files that look alike, as the server clustered them. */
export type Group = components['schemas']['GroupView'];

/** One file of a group, with everything the card prints about it already on it. */
export type GroupFile = components['schemas']['FileView'];

/** One value a dial may be set to, and what it is called. The server's own list, never a copy. */
export type Choice = components['schemas']['ChoiceView'];

/* Which file of a group to keep, chosen automatically.
 *
 * **The rule itself lives on the SERVER.** Decided here, per pair, from facts the browser fetched
 * one file at a time, it could only apply to what had been loaded: "resolve all by a rule"
 * would mean "resolve the first hundred". A rule that decides a whole library has to be applied
 * where the library is, and a rule with a chain of tie-breaks has to be one implementation, not
 * two that drift.
 *
 * What is left here is the NAME of the chosen rule, which is a stored preference like any other.
 */
type KeepStrategy = 'larger' | 'smaller' | 'higher_res' | 'newer' | 'older';

function refusal(error: unknown): string {
	return error instanceof ApiError ? (error.detail ?? error.message) : UNREACHABLE;
}

/** What the server sends with the review queue, so an empty screen can be read. */
export interface QueueSummary {
	/** Groups these settings make, across the whole library. What the page is a part of. */
	total: number;
	/** Where the page on screen starts, counting from zero. */
	offset: number;
	/** Groups the rule could not settle, across the whole library. The ones that cost a decision. */
	needsYou: number;
	/** Pairs waiting that the current settings show, across the whole library. */
	matching: number;
	/** Pairs waiting in all, whatever the settings. Never smaller than `matching`. */
	pendingTotal: number;
	/** Groups left off this page because a file in one is in a vault this session has not opened. */
	concealed: number;
	/** Videos nothing has fingerprinted yet, so they cannot be in the queue at all. */
	awaitingFingerprint: number;
	/**
	 * Files nothing can ever compare, because the decoder refused their frames.
	 *
	 * Separate from `awaitingFingerprint` because they are two different sentences: one is work in
	 * flight and the other is work that will not happen, and folded together an empty queue reads
	 * as one that is still filling.
	 */
	cannotFingerprint: number;
	/** The named closeness the list was read at. */
	level: string;
	/** The length rule it was read at, or null when lengths are ignored. */
	maxDurationGapMs: number | null;
	/** The keeper rule in force, as it is stored. */
	rule: KeepStrategy;
	/** Every keeper rule that may be chosen, with what each is called. */
	rules: Choice[];
	/** Every closeness the dial may be set to, strictest first. */
	levels: Choice[];
	/** The largest length rule the dial accepts, in seconds. */
	maxDurationGapLimit: number;
	/** What the dial says in place of zero, as the setting declares it. Null until read. */
	maxDurationGapWord: string | null;
}

const NOTHING_KNOWN: QueueSummary = {
	total: 0,
	offset: 0,
	needsYou: 0,
	matching: 0,
	pendingTotal: 0,
	concealed: 0,
	awaitingFingerprint: 0,
	cannotFingerprint: 0,
	level: 'medium',
	maxDurationGapMs: null,
	rule: 'higher_res',
	rules: [],
	levels: [],
	maxDurationGapLimit: 3600,
	maxDurationGapWord: null
};

/** What a press over a page of groups actually did. See `Maintenance.confirmMarked`. */
export interface Outcome {
	settled: number;
	removed: number;
	refused: number;
	/** Groups the server would not act on: a dial moved, or another window settled them first. */
	unknown: number;
	problem?: string;
}

/* The only stable name a group has, and the METHOD is half of it.
 *
 * There is no group id and there cannot be one: a group is computed from the pair table at the
 * settings in force, so it exists for as long as the settings do. Its smallest file id is stable
 * across two reads of the same settings, which is exactly as long as an override needs to live.
 *
 * The file alone is NOT enough: a GIF is fingerprinted by `videohash` and by `video_phash`, so
 * the same two files are a group under each, and the two groups have the same smallest file.
 * Keyed on the file alone, an override on one would mark the other, and the `{#each}` in the
 * screen would throw on the duplicate key and draw NOTHING. Groups never chain across methods, so
 * a file is in at most one group per method and this is unique.
 */
export function keyOf(group: Group): string {
	return `${group.method}:${group.files[0]?.id ?? ''}`;
}

/** One group by the name `keyOf` gives it, for a chain's own screen. A 404 is the ordinary
 *  answer for a chain the dial has since broken up, and it is the caller's to read. */
export function fetchGroup(method: string, first: string): Promise<Group> {
	return api.get<Group>(`/dedup/groups/${encodeURIComponent(method)}/${encodeURIComponent(first)}`);
}

export class Maintenance {
	/** The page of groups on screen. */
	groups = $state<Group[]>([]);
	/** Which file of each group is marked to keep, once somebody has overridden the rule.
	 *
	 * Keyed by the group's first file, which is the only stable name a group has: there is no
	 * group id, because a group is computed from the pair table at the settings in force. Held
	 * beside the list rather than on it, so a reload replaces the list without losing an override
	 * somebody has just made, and so the press can see every one of them. */
	chosen = $state<Record<string, string>>({});
	redundancies = $state<Redundancy[]>([]);
	/** And which COPY of each file stored more than once is marked to keep, once somebody has
	 *  overridden the rule. Keyed by the asset, because a row on that queue is one asset in several
	 *  places; held beside the page for the reason `chosen` above is. See `copyKeeperOf`. */
	keptCopy = $state<Record<string, string>>({});
	totalReclaimable = $state(0);
	/** How many assets are stored more than once in the whole library, of which `redundancies` is
	    a page. Held apart because an empty page and an empty library are different sentences. */
	totalRedundant = $state(0);
	/** How many of the page were left out because the vault conceals them. */
	concealed = $state(0);
	/** Where the page of copies on screen starts, counting from zero, as the server answered it. */
	reclaimOffset = $state(0);
	/**
	 * How much of the library has a twin that knows more about it than it does.
	 *
	 * Groups of files that are bit-for-bit identical on the fingerprint, where one of them carries
	 * a person or a site and the others carry nothing. It is NOT the exact-copies queue next door:
	 * identical bytes are one file in several places and already share everything recorded about
	 * them, so there is nothing there to carry. On a typical library it is a small number (tens of
	 * files in a large library), which is why it is a line on this bar rather than a card
	 * of its own.
	 */
	carry = $state<{ groups: number; files: number }>({ groups: 0, files: 0 });
	/* The numbers that make an empty queue readable.
	 *
	 * An empty list has three completely different meanings (there are none, the settings are
	 * hiding them, or half the library has never been fingerprinted), and they draw the same
	 * screen. These are what let it say which. */
	summary = $state<QueueSummary>({ ...NOTHING_KNOWN });
	/** True once a read has *succeeded*. An attempt that failed leaves this false on purpose.
	    See `#reading`. */
	loaded = $state(false);
	loading = $state(false);
	problem = $state<string | null>(null);
	busy = $state(false);

	/* Each half on its own, because the two live on different cards.
	 *
	 * Near Duplicates asks whether two files are the same thing; Exact Duplicates asks which of
	 * several identical copies to keep. Two questions, two cards, and neither should pay a request
	 * for the other's list.
	 *
	 * ## Through the panel's paging, and nowhere else
	 *
	 * Each half is read through the `CardPaging` its panel pages with (the same mechanism every
	 * paged list in Sift has), so the page is asked for by the ROW the panel was left at when the
	 * address names one (`from`, with `near` beside it), a page that a verb has emptied steps back
	 * to where the list now ends, and an answer a newer read has overtaken is never drawn. An offset held
	 * in memory, with a store beside it re-reading "the same page" after every verb by that number,
	 * would open the queue on page one when somebody came back, and a re-read past the end would
	 * draw an empty queue over a count that said otherwise. Nothing here re-reads after a verb; the
	 * panel does, through the same paging. */

	/** One page of the review queue through the panel's paging, and the numbers that go with it.
	 *
	 * `needsYou` narrows it to the groups no rule could settle: a different list, so its rows are
	 * never trimmed or extended to answer the whole queue. */
	async fillGroups(paging: CardPaging, needsYou: boolean): Promise<boolean> {
		return await this.#filling(async () => {
			const page = await paging.fill(
				needsYou ? 'needs-you' : 'every',
				() => this.groups,
				(query) => this.#asking(() => this.#askGroups(query, needsYou)),
				(queue) => ({ rows: queue.groups, total: queue.total ?? 0, offset: queue.offset ?? 0 })
			);
			if (page === null) return false;
			if (page.answer) this.#takeGroups(page.answer);
			// In the same synchronous turn as the rows. See `CardPaging.land`.
			paging.land(page.offset);
			return true;
		});
	}

	/** One page of the files sitting in more than one place, through the panel's paging, and what
	 *  letting one go would free. */
	async fillReclaim(paging: CardPaging): Promise<boolean> {
		return await this.#filling(async () => {
			const page = await paging.fill(
				'copies',
				() => this.redundancies,
				(query) => this.#asking(() => this.#askReclaim(query)),
				(reclaim) => ({
					rows: reclaim.assets,
					total: reclaim.total ?? 0,
					offset: reclaim.offset ?? 0
				})
			);
			if (page === null) return false;
			if (page.answer) this.#takeReclaim(page.answer);
			paging.land(page.offset);
			return true;
		});
	}

	/* `loading` only while a request is actually out: a page `fill` answers from the rows held asks
	   nothing, and saying "Looking..." for it would flash the list away for a frame. */
	async #asking<T>(ask: () => Promise<T>): Promise<T> {
		this.loading = true;
		return await ask();
	}

	/* A read through the paging: `work` says false when a newer read overtook it, which then owns
	   the flags: an overtaken read must not say the screen has finished loading under it. True
	   when a page landed, which is when the panel writes where it is into the address. */
	async #filling(work: () => Promise<boolean>): Promise<boolean> {
		this.problem = null;
		try {
			if (!(await work())) return false;
			this.loaded = true;
			this.loading = false;
			return true;
		} catch (error) {
			this.problem = refusal(error);
			this.loading = false;
			return false;
		}
	}

	async #reading(work: () => Promise<void>): Promise<void> {
		this.loading = true;
		this.problem = null;
		try {
			await work();
			/* Only now. A screen that set this in the failure path too would go on to say "nothing to
			   review" and "no file is stored twice" on the strength of a request that was refused,
			   which is a reassuring thing to tell somebody about a library nobody managed to read. */
			this.loaded = true;
		} catch (error) {
			this.problem = refusal(error);
		} finally {
			this.loading = false;
		}
	}

	async #askGroups(query: PageAsk, needsYou: boolean): Promise<components['schemas']['GroupList']> {
		return await api.get<components['schemas']['GroupList']>('/dedup/groups', {
			query: { ...asked(query), needs_you: String(needsYou) }
		});
	}

	#takeGroups(queue: components['schemas']['GroupList']): void {
		this.groups = queue.groups;
		/* The overrides go with the page. A group's mark belongs to the group in front of somebody,
		   and carrying one across a page turn would put a keeper on a group they have not seen,
		   which the press would then act on. */
		this.chosen = {};
		this.summary = {
			total: queue.total ?? 0,
			offset: queue.offset ?? 0,
			needsYou: queue.needs_you ?? 0,
			matching: queue.matching ?? 0,
			pendingTotal: queue.pending_total ?? 0,
			concealed: queue.concealed ?? 0,
			awaitingFingerprint: queue.awaiting_fingerprint ?? 0,
			cannotFingerprint: queue.cannot_fingerprint ?? 0,
			level: queue.level ?? NOTHING_KNOWN.level,
			maxDurationGapMs: queue.max_duration_gap_ms ?? null,
			rule: (queue.rule ?? NOTHING_KNOWN.rule) as KeepStrategy,
			rules: queue.rules ?? [],
			levels: queue.levels ?? [],
			maxDurationGapLimit: queue.max_duration_gap_limit ?? NOTHING_KNOWN.maxDurationGapLimit,
			maxDurationGapWord: queue.max_duration_gap_word ?? null
		};
	}

	async #askReclaim(query: PageAsk): Promise<components['schemas']['ReclaimView']> {
		return await api.get<components['schemas']['ReclaimView']>('/reclaim', {
			query: asked(query)
		});
	}

	#takeReclaim(reclaim: components['schemas']['ReclaimView']): void {
		this.redundancies = reclaim.assets;
		/* The overrides go with the page, exactly as a group's do: a mark belongs to the row in
		   front of somebody, and carrying one across a page turn would mark a file they have not
		   seen, which the page press would then act on. */
		this.keptCopy = {};
		this.reclaimOffset = reclaim.offset ?? 0;
		this.totalRedundant = reclaim.total ?? 0;
		this.totalReclaimable = reclaim.total_reclaimable_bytes;
		this.concealed = reclaim.concealed ?? 0;
	}

	/**
	 * Compare the library now. Hands back a refusal, or nothing when the job was queued.
	 *
	 * THE DUPLICATES TASK'S OWN RUN NOW, not a request of its own. The Tasks row is the one door to
	 * the sweep, so this presses it, which also means the press is recorded as one, and is never
	 * held by the task's When.
	 *
	 * The queue is not reloaded afterwards and must not be: the scan is a background job that takes
	 * minutes on a large library, so a list read the instant it starts is the list from before it.
	 * The dashboard shows the bar; this screen is re-read when the library changes underneath it.
	 */
	async scanNow(): Promise<string | undefined> {
		this.busy = true;
		try {
			await taskList.run(DUPLICATES_TASK, 'now');
			return undefined;
		} catch (error) {
			return refusal(error);
		} finally {
			this.busy = false;
		}
	}

	/** Which file of a group is marked to keep: somebody's override, or the rule's own mark.
	 *
	 * One reader for both, so the tick on screen and the file the press sends can never be two
	 * different answers. Null means nothing is marked, which is what "needs you" looks like. */
	keeperOf(group: Group): string | null {
		const key = keyOf(group);
		const mine = this.chosen[key];
		if (mine && group.files.some((one) => one.id === mine)) return mine;
		return group.keeper ?? null;
	}

	/** Mark a different file. Writes nothing: the press is what writes. */
	choose(group: Group, fileId: string): void {
		this.chosen = { ...this.chosen, [keyOf(group)]: fileId };
	}

	/**
	 * Which copy of a file stored more than once is kept: somebody's override, or the largest.
	 *
	 * **The rule is not the screen's.** A screen that lights the biggest copy, works the same
	 * figure out again for its page press, and offers nothing that can change which one it is
	 * leaves clicking in a group doing nothing to the choice and Remove as the only control on a
	 * copy, while the sibling tab of the same screen has an override. So the exact-copies queue
	 * has one too, read from here.
	 *
	 * One reader for the mark and the press, for the reason `keeperOf` gives above: the tile that
	 * is lit and the copy a press keeps must not be two answers.
	 *
	 * Null where a row somehow has no copies at all, which no page from the server holds: said
	 * rather than assumed, because the caller has to draw something either way.
	 */
	copyKeeperOf(asset: Redundancy): string | null {
		const mine = this.keptCopy[asset.asset_id];
		if (mine && asset.copies.some((one) => one.location_id === mine)) return mine;
		let kept: Copy | null = null;
		for (const copy of asset.copies) {
			if (!kept || (copy.size_bytes ?? 0) > (kept.size_bytes ?? 0)) kept = copy;
		}
		return kept?.location_id ?? null;
	}

	/** Mark a different copy. Writes nothing: the press is what writes. */
	chooseCopy(asset: Redundancy, locationId: string): void {
		this.keptCopy = { ...this.keptCopy, [asset.asset_id]: locationId };
	}

	/** How many groups on this page are marked, so the press can name a count before it runs. */
	get marked(): Group[] {
		return this.groups.filter((one) => !one.too_big && this.keeperOf(one) !== null);
	}

	/** What confirming this page would delete: every file of every marked group but its keeper. */
	get wouldDelete(): GroupFile[] {
		return this.marked.flatMap((group) => {
			const keep = this.keeperOf(group);
			return group.files.filter((one) => one.id !== keep);
		});
	}

	/** Keep the marked file of each of these groups and delete the rest. Permanent.
	 *
	 * One request for the whole page rather than one per group: a page is one press and one decision,
	 * and twenty-four requests would leave a half-settled page behind if the connection dropped in
	 * the middle. What comes back says what actually happened: a folder Sift was never given write
	 * access to refuses one file and says nothing about the rest.
	 */
	async confirmMarked(groups: Group[]): Promise<Outcome> {
		return this.#pressing('/dedup/groups/confirm', groups);
	}

	/** Say these groups are not duplicates. Deletes nothing, and the answer lasts. */
	async dismissGroups(groups: Group[]): Promise<Outcome> {
		return this.#pressing('/dedup/groups/dismiss', groups);
	}

	async #pressing(where: ApiPath, groups: Group[]): Promise<Outcome> {
		if (groups.length === 0) return { settled: 0, removed: 0, refused: 0, unknown: 0 };
		this.busy = true;
		try {
			const answer = await api.post<components['schemas']['GroupSettleResult']>(where, {
				body: {
					groups: groups.map((group) => ({
						ids: group.files.map((one) => one.id),
						keep: this.keeperOf(group)
					}))
				}
			});
			/* Not read again HERE. Which page to read next is the panel's to say, through its paging:
			   the page press goes back to the front, a single group's answer stays where it was. */
			return {
				settled: answer.settled ?? 0,
				removed: answer.removed ?? 0,
				refused: answer.refused ?? 0,
				unknown: answer.unknown ?? 0
			};
		} catch (error) {
			return { settled: 0, removed: 0, refused: 0, unknown: 0, problem: refusal(error) };
		} finally {
			this.busy = false;
		}
	}

	/** How many files could be told what their twin already knows. Read on its own rather than with
	 *  the page, because it is about the whole library and does not change when a page turns. */
	async loadCarry(): Promise<void> {
		await this.#reading(async () => {
			const totals = await api.get<components['schemas']['CarryTotals']>('/dedup/carry');
			this.carry = { groups: totals.groups, files: totals.files };
		});
	}

	/** Carry every one of them, once. How many files gained something, or why not.
	 *
	 * Every file it writes on gets its own decision in History, so somebody who disagrees
	 * with one of them takes that one back and the rest stand, which is why this can be one press
	 * over a number the line above it has already said out loud.
	 */
	async carryEverywhere(): Promise<{ files: number } | string> {
		this.busy = true;
		try {
			const done = await api.post<components['schemas']['CarryResult']>('/dedup/carry/all');
			await this.loadCarry();
			return { files: done.files };
		} catch (error) {
			return refusal(error) ?? "Those copies couldn't be told.";
		} finally {
			this.busy = false;
		}
	}

	/** Let go of one copy of a file that has several. The file itself survives, in its other places. */
	/**
	 * Let go of a page of copies in one press: the exact-copies queue's own page confirm, as the
	 * near-duplicate queue has one. The same page is re-read afterwards, for
	 * the reason `release` gives.
	 */
	async releaseMany(
		releases: { asset_id: string; location_id: string }[]
	): Promise<{ released: number; refused: number } | string> {
		if (releases.length === 0) return { released: 0, refused: 0 };
		this.busy = true;
		try {
			const done = await api.post<components['schemas']['Released']>('/reclaim/release-many', {
				body: { releases }
			});
			// The page is read again by the panel, through its paging. See `fillReclaim`.
			return done;
		} catch (error) {
			return refusal(error) ?? "Those copies couldn't be deleted.";
		} finally {
			this.busy = false;
		}
	}

	async release(assetId: string, locationId: string): Promise<string | undefined> {
		this.busy = true;
		try {
			await api.post(`/reclaim/${assetId}/release`, { body: { location_id: locationId } });
			// Not read again here: the panel reads the SAME page back through its paging, because
			// letting one copy go leaves the rest of the page where it was. Only its own half: the
			// review queue is a different card. See `fillReclaim`.
			return undefined;
		} catch (error) {
			return refusal(error);
		} finally {
			this.busy = false;
		}
	}
}

/* --- how the numbers are written ------------------------------------------------------------ */

/*
 * These three are the screen's WORD for a fact it does not have, and nothing else. What a size, a
 * length and a shape look like is worked out once, in `$lib/library/facts`, because this screen puts a
 * duplicate pair's size and length side by side with the record on the file's own page, and two
 * places that each divide by their own idea of a kilobyte is how two come to disagree.
 */

/** Bytes, at the scale a person reads them. */
export function formatBytes(bytes: number | null | undefined): string {
	return facts.size(bytes) ?? 'unknown';
}

/** A length. A file written down as zero milliseconds is one nothing could be read from, so it is
 *  left blank rather than drawn as `0:00`, which reads as a real and very short film. */
export function formatDuration(ms: number | null): string {
	return facts.length(ms) ?? '';
}

export function formatDimensions(
	width: number | null | undefined,
	height: number | null | undefined
): string {
	return facts.dimensions(width, height) ?? '';
}

/**
 * How alike a group is, in ONE vocabulary, whatever measured it.
 *
 * ## Why the words are the same for all three
 *
 * Separate vocabularies read as three unrelated scales on one screen: a photograph saying
 * *Identical to look at / Nearly identical / Similar*, a video *Almost certainly the same / Very
 * likely the same / Possibly the same*, a GIF *26 of 30 frames match*: phrases with no
 * order anybody can see between them, some of them a sentence about confidence rather than about
 * likeness.
 *
 * A person reading this screen is asking ONE question (how alike are these), and the answer has
 * to mean the same thing on every card or it cannot be compared with the card above it. So there
 * are four rungs and every method lands on one of them:
 *
 *     Identical  ->  Almost identical  ->  Very similar  ->  Similar
 *
 * The SCALES still differ, and that is the part that has to stay right: 2 apart is nearly nothing
 * for a photograph's 63-bit fingerprint and a fifth of a GIF's thirty frames. What each
 * method does is convert its own figure to the same four rungs, which is exactly the translation
 * the raw number could never do for a reader.
 */
export function describeCloseness(candidate: { method: string; distance: number }): string {
	return HOW_ALIKE[rungOf(candidate)];
}

/** The four rungs, closest first. The only words this screen uses for likeness. */
const HOW_ALIKE = ['Identical', 'Almost identical', 'Very similar', 'Similar'] as const;

/*
 * Where each rung turns over, and it is the CLOSENESS DIAL'S own table.
 *
 * The dial has four positions and the server's `LEVELS` gives each of them a figure per
 * fingerprint: 0, 2, 4 and the widest that method reaches. So the rungs turn over at exactly those
 * figures, for every method, and the dial's own words are these four: *Exact - identical*,
 * *High - almost identical*, *Medium - very similar*, *Low - similar*. A queue read at High shows
 * nothing worse than "Almost identical", and the dial said so before it was moved.
 *
 * That is what makes the words trustworthy rather than decorative: they are not a second opinion
 * about closeness, they are the same four steps the setting is built on. The SCALES still differ
 * underneath: 2 apart is two bits of a photograph's 63 and two frames of a GIF's thirty,
 * which is exactly the translation a raw number could never do for a reader.
 */
const TURNS_OVER = [0, 2, 4] as const;

/** Which rung a figure lands on. Zero is always the top one. */
function rungOf({ distance }: { method: string; distance: number }): number {
	for (const [rung, edge] of TURNS_OVER.entries()) {
		if (distance <= edge) return rung;
	}
	return HOW_ALIKE.length - 1;
}
