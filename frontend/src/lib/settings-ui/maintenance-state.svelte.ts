/* Duplicate-finding, as the screens that show it ask the server about it. */

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

/* Which file of a group to keep, chosen automatically. */
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
	/** Files nothing can ever compare, because the decoder refused their frames. */
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

/* The only stable name a group has, and the METHOD is half of it. */
export function keyOf(group: Group): string {
	return `${group.method}:${group.files[0]?.id ?? ''}`;
}

/** One group by the name `keyOf` gives it, for a chain's own screen. */
export function fetchGroup(method: string, first: string): Promise<Group> {
	return api.get<Group>(`/dedup/groups/${encodeURIComponent(method)}/${encodeURIComponent(first)}`);
}

export class Maintenance {
	/** The page of groups on screen. */
	groups = $state<Group[]>([]);
	/** Which file of each group is marked to keep, once somebody has overridden the rule. */
	chosen = $state<Record<string, string>>({});
	redundancies = $state<Redundancy[]>([]);
	/** And which COPY of each file stored more than once is marked to keep, once somebody has
	 * overridden the rule. */
	keptCopy = $state<Record<string, string>>({});
	totalReclaimable = $state(0);
	/** How many assets are stored more than once in the whole library, of which `redundancies` is
	   a page. */
	totalRedundant = $state(0);
	/** How many of the page were left out because the vault conceals them. */
	concealed = $state(0);
	/** Where the page of copies on screen starts, counting from zero, as the server answered it. */
	reclaimOffset = $state(0);
	/** How much of the library has a twin that knows more about it than it does. */
	carry = $state<{ groups: number; files: number }>({ groups: 0, files: 0 });
	/* The numbers that make an empty queue readable. */
	summary = $state<QueueSummary>({ ...NOTHING_KNOWN });
	/** True once a read has *succeeded*. An attempt that failed leaves this false on purpose. */
	loaded = $state(false);
	loading = $state(false);
	problem = $state<string | null>(null);
	busy = $state(false);

	/* Each half on its own, because the two live on different cards. */

	/** One page of the review queue through the panel's paging, and the numbers that go with it. */
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

	/* `loading` only while a request is actually out: a page `fill` answers from the rows held
	   asks nothing, and saying "Looking..." */
	async #asking<T>(ask: () => Promise<T>): Promise<T> {
		this.loading = true;
		return await ask();
	}

	/* A read through the paging: `work` says false when a newer read overtook it, which then
	   owns the flags: an overtaken read must not say the screen has finished loading under it. */
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
			/* Only now. */
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
		/* The overrides go with the page. */
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

	/** Compare the library now. Hands back a refusal, or nothing when the job was queued. */
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

	/** Which file of a group is marked to keep: somebody's override, or the rule's own mark. */
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

	/** Which copy of a file stored more than once is kept: somebody's override, or the largest. */
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

	/** Keep the marked file of each of these groups and delete the rest. */
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

	/** How many files could be told what their twin already knows. */
	async loadCarry(): Promise<void> {
		await this.#reading(async () => {
			const totals = await api.get<components['schemas']['CarryTotals']>('/dedup/carry');
			this.carry = { groups: totals.groups, files: totals.files };
		});
	}

	/** Carry every one of them, once. How many files gained something, or why not. */
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
	/** Let go of a page of copies in one press: the exact-copies queue's own page confirm, as the
	 * near-duplicate queue has one. */
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
			// letting one copy go leaves the rest of the page where it was.
			return undefined;
		} catch (error) {
			return refusal(error);
		} finally {
			this.busy = false;
		}
	}
}

/* --- how the numbers are written ------------------------------------------------------------ */

/* These three are the screen's WORD for a fact it does not have, and nothing else. */

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

/** How alike a group is, in ONE vocabulary, whatever measured it. */
export function describeCloseness(candidate: { method: string; distance: number }): string {
	return HOW_ALIKE[rungOf(candidate)];
}

/** The four rungs, closest first. The only words this screen uses for likeness. */
const HOW_ALIKE = ['Identical', 'Almost identical', 'Very similar', 'Similar'] as const;

/* Where each rung turns over, and it is the CLOSENESS DIAL'S own table. */
const TURNS_OVER = [0, 2, 4] as const;

/** Which rung a figure lands on. Zero is always the top one. */
function rungOf({ distance }: { method: string; distance: number }): number {
	for (const [rung, edge] of TURNS_OVER.entries()) {
		if (distance <= edge) return rung;
	}
	return HOW_ALIKE.length - 1;
}
