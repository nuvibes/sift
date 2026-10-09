import { goto, pushState, replaceState } from '$app/navigation';
import { page } from '$app/state';
import { screenBar } from '$lib/components/shell/screen-bar.svelte';
import type { OpenedFrom, SittingPlace } from '$lib/player/sitting.svelte';
import { run, Walk, type WalkSource } from '$lib/player/run.svelte';
import { api } from '$lib/api/client';
import { planFor, type PlaybackPlan } from '$lib/player/playback';
import type { components } from '$lib/api/schema';
import { noteSearchOpen } from '$lib/shell/visits';
import { jobChanges, libraryChanges } from '$lib/library/changes.svelte';

/*
 * `/asset/{id}` is a real address and ALWAYS a panel: over the grid when a tile is clicked (closing
 * returns exactly there), or over nothing when landed on cold (closing goes to the library).
 */

interface AssetModalState {
	asset: string;
	direct?: boolean;
	/* A moment to open at, in milliseconds, from a face that knows when it was found. */
	at?: number;
	/* Where that moment ENDS, for a saved loop: the player repeats the range. */
	until?: number;
}

/*
 * What was on screen, in order, for Next, Previous and play through. Not in history state: it can
 * be thousands of ids.
 */
let sequence: Neighbour[] = [];

/**
 * How to fetch MORE of the list behind the panel, reading the query independently so the wall
 * underneath never moves. Null where the panel was opened from no paged list.
 */
export interface Continues {
	from: number;
	/** In ALL, so a run knows the end from the end of a page. */
	total: number;
	/** Declared by the list: the wall of Loops has no shuffled order. */
	shuffles: boolean;
	/** One block from a global offset; `order` asks for the server's seeded shuffle. */
	fetch(offset: number, limit: number, order?: RunOrder): Promise<Neighbour[]>;
	/** Where one file sits in this list's own order, for turning Shuffle off on a stranger. */
	locate?(id: string): Promise<number | null>;
}

export interface RunOrder {
	seed: number;
}

let continues: Continues | null = null;

/** About a page of the wall, so crossing a boundary is one request. */
const BLOCK = 60;

/**
 * `runs` is for play through only: a still has no `ended`, so a run steps over it; Next does not.
 */
interface Neighbour {
	id: string;
	runs: boolean;
}

/** Over the current page; `among` is only what has actually been loaded. */
export function openAsset(
	id: string,
	among: Neighbour[] = [],
	at?: number,
	until?: number,
	more: Continues | null = null,
	loop: string | null = null
): void {
	sequence = among;
	continues = more;
	ahead = null;
	planBeside(id);
	run.reset();
	openedLoop = loop === null ? null : { file: id, loop };
	noteSearchOpen(page.route.id, page.url.searchParams, id);
	push(id, at, until);
}

/*
 * WHERE A PANEL WAS OPENED FROM, read off the screen behind it, which shallow routing leaves
 * standing. A test refuses a route on neither this table nor `OPENS_NO_FILE`.
 */
export const OPENED_FROM: Readonly<Record<string, OpenedFrom>> = {
	'/browse': 'library',
	'/favorites': 'favorites',
	'/recent': 'recent',
	'/loops': 'loops',
	'/people/[id]': 'person',
	'/sites/[id]': 'site',
	'/tags/[id]': 'tag',
	'/collections/[id]': 'collection',
	'/photo-sets/[id]': 'photo_set',
	'/songs/[id]': 'song',
	'/organize': 'organize',
	'/organize/[queue]': 'organize',
	'/organize/[queue]/[id]': 'organize',
	'/organize/may-be/[person]/[pile]': 'organize',
	'/downloads': 'downloads',
	'/hidden': 'hidden',
	'/start': 'start',
	'/asset/[id]': 'link',
	'/insights': 'insights',
	'/insights/stats': 'insights',
	'/insights/recaps': 'insights',
	'/insights/recaps/[id]': 'insights'
};

/** The words whose screen is about ONE thing, so a sitting keeps the thing too; a test holds it. */
export const NAMES_ITS_THING: ReadonlySet<OpenedFrom> = new Set([
	'person',
	'site',
	'tag',
	'collection',
	'photo_set',
	'song'
]);

/** Screens with no file of their own; their sittings say `other`. */
export const OPENS_NO_FILE: readonly string[] = [
	'/library',
	'/more',
	'/remote',
	'/collections',
	'/collections/new',
	'/connect',
	'/opening',
	'/design',
	'/design/bar',
	'/design/charts',
	'/design/saved-filters',
	'/design/insights',
	'/library-location',
	'/locked',
	'/login',
	'/people',
	'/people/new',
	'/photo-sets',
	'/photo-sets/new',
	'/settings/[[section]]',
	'/setup',
	'/sites',
	'/sites/new',
	'/songs',
	'/songs/new',
	'/swap',
	'/tags',
	'/tags/new',
	'/theater'
];

/** Paired with its file: stepping on is still opened from Loops, but not that Loop. */
let openedLoop: { file: string; loop: string } | null = null;

const AN_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;

type Behind = Pick<SittingPlace, 'opened_from' | 'opened_from_id' | 'searched'>;

function openedFrom(): Behind {
	const route = page.route.id;
	if (route === null) return { opened_from: 'other' };
	if (route === '/browse') {
		// With words typed, the narrowed library is a SEARCH, named by its words.
		const words = (page.url.searchParams.get('q') ?? '').trim();
		if (words) return { opened_from: 'search', searched: words };
		const folder = page.url.searchParams.get('in') ?? '';
		if (AN_ID.test(folder)) return { opened_from: 'folder', opened_from_id: folder };
	}
	const word = OPENED_FROM[route] ?? 'other';
	if (!NAMES_ITS_THING.has(word)) return { opened_from: word };
	const thing = page.params.id ?? '';
	return { opened_from: word, opened_from_id: AN_ID.test(thing) ? thing : null };
}

/** The kept filter is read from the bar's own answer (`appliedKept`). */
export function panelPlace(id: string): SittingPlace {
	return {
		screen: 'panel',
		...openedFrom(),
		loop: openedLoop?.file === id ? openedLoop.loop : null,
		kept_filter: screenBar.keptInForce
	};
}

/** For the corner player handing a clip back, keeping the grid it came from. */
export function reopenAsset(id: string, at?: number): void {
	push(id, at);
}

/**
 * Whether a run has anywhere to go, without a request: true while anything ELSE could play.
 * `pictures` is the account's answer to holding photographs, handed in by `AssetModal`.
 */
export function runGoesOn(id: string, { pictures = false }: { pictures?: boolean } = {}): boolean {
	if (continues && continues.total > sequence.length) return true;
	return sequence.some((each) => (each.runs || pictures) && each.id !== id);
}

function push(id: string, at?: number, until?: number): void {
	// Shallow: the page under it stays mounted. The moment and `until` go in the address too, so a
	// pasted link opens the same stretch.
	const moment = at === undefined ? '' : `?t=${at}${until === undefined ? '' : `&until=${until}`}`;
	takeDismissal();
	pushState(`/asset/${id}${moment}`, { asset: id, at, until } satisfies AssetModalState);
}

/** Within the open modal with `replaceState`, so Back after a run of clips is one press. */
/** A file from elsewhere (Randomize), spliced in after the one it was jumped from. */
export function showStranger(id: string, from: string | null, runs: boolean): void {
	if (indexOf(id) < 0) {
		const after = from === null ? -1 : indexOf(from);
		sequence.splice(after + 1, 0, { id, runs });
	}
	// A shuffled walk takes it as a detour, not as a file the walk drew.
	const walk = run.shuffle ? run.walk : null;
	if (walk && from !== null && walk.holds(from) && !walk.holds(id)) walk.detour = id;
	showAsset(id);
}

/** A deleted file leaves the list the panel walks, or Back would land on a 404. */
export function forgetAsset(id: string): void {
	const at = indexOf(id);
	if (at >= 0) sequence.splice(at, 1);
	run.walk?.forget(id);
	ahead = null;
}

export function showAsset(id: string): void {
	// Carried forward: a panel opened cold is still one after stepping on.
	replaceState(`/asset/${id}`, { asset: id, direct: page.state.direct } satisfies AssetModalState);
	if (ahead?.next !== id || ahead.plan === null) planBeside(id);
}

/**
 * Open the asset this address names, with `replaceState`. `cold` says nothing is behind it, so
 * closing goes to the library; the caller knows (`isFirstScreen`). True by default: it cannot
 * strand anybody.
 */
export function enterAsset(
	id: string,
	at?: number,
	until?: number,
	{ cold = true }: { cold?: boolean } = {}
): void {
	takeDismissal();
	// An address names no Loop, even with a stretch.
	openedLoop = null;
	replaceState('', { asset: id, at, until, direct: cold } satisfies AssetModalState);
}

/**
 * Back normally; to the library when landed on cold. For the close and the corner's hand-over
 * alike.
 */
export function leaveAssetPanel(): void {
	if (page.state.direct) {
		void goto('/browse');
		return;
	}
	history.back();
}

/**
 * A plain left-click on an asset LINK opens the panel over the current screen instead of running
 * the route, which would rebuild the grid behind it. Any modifier is left to the browser.
 */
export function openAssetInstead(event: MouseEvent, id: string, among: Neighbour[] = []): void {
	if (event.defaultPrevented) return;
	if (event.button !== 0) return;
	if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
	event.preventDefault();
	openAsset(id, among);
}

export function neighboursOf(id: string): { previous: string | null; next: string | null } {
	const at = indexOf(id);
	if (at < 0) return { previous: null, next: null };
	return {
		previous: at > 0 ? sequence[at - 1].id : null,
		next: at < sequence.length - 1 ? sequence[at + 1].id : null
	};
}

/** Stepping over stills, among what is held; `playOnAfter` reads more. */
export function nextPlayableAfter(id: string): string | null {
	const at = indexOf(id);
	if (at < 0) return null;
	for (let index = at + 1; index < sequence.length; index += 1) {
		if (sequence[index].runs) return sequence[index].id;
	}
	return null;
}

/**
 * Where a run goes when a clip finishes: further down what is held, else the next block of the
 * query, else round to the beginning (as a Theater cell wraps). Null when nothing plays.
 */
export async function playOnAfter(id: string): Promise<string | null> {
	const straight = nextPlayableAfter(id);
	if (straight !== null) return straight;

	// Fetch from where the held sequence ends in the WHOLE list.
	while (continues && continues.from + sequence.length < continues.total) {
		const got = await fetchMore();
		if (got === 0) break;
		const next = nextPlayableAfter(id);
		if (next !== null) return next;
	}

	const first = sequence.find((each) => each.runs);
	if (!first) return null;
	// Not the file that just finished, unless it is the only one that plays.
	if (first.id !== id) return first.id;
	const another = sequence.find((each) => each.runs && each.id !== id);
	return another ? another.id : first.id;
}

async function fetchMore(): Promise<number> {
	if (!continues) return 0;
	const at = continues.from + sequence.length;
	let got: Neighbour[] = [];
	try {
		got = await continues.fetch(at, BLOCK);
	} catch {
		return 0;
	}
	// Ids already held are dropped, or a re-ordered wall would hand the same file back forever.
	const held = new Set(sequence.map((each) => each.id));
	const fresh = got.filter((each) => !held.has(each.id));
	sequence = [...sequence, ...fresh];
	// What ARRIVED, not what was fresh, or a block of seen files would stop the run.
	return got.length;
}

/*
 * --- WHERE NEXT AND BACK GO, for both players. With Shuffle on it is the WALK (`run.svelte.ts`):
 * one fixed order, so Back returns to the file just watched.
 */

function walkSource(): WalkSource {
	const more = continues;
	const whole = more === null || (more.from <= 0 && sequence.length >= more.total);
	return {
		held: [...sequence],
		more:
			whole || more === null
				? null
				: {
						total: more.total,
						shuffled: more.shuffles
							? (offset, limit, seed) => more.fetch(offset, limit, { seed })
							: null,
						inOrder: (offset, limit) => more.fetch(offset, limit)
					}
	};
}

/** Never for a question asked while drawing; null with Shuffle off. */
function walkFrom(id: string): Walk | null {
	if (!run.shuffle) return null;
	if (run.walk?.holds(id)) return run.walk;
	const at = indexOf(id);
	if (at < 0) return null;
	run.walk = new Walk(sequence[at], walkSource());
	return run.walk;
}

function walkHolding(id: string): Walk | null {
	const walk = run.shuffle ? run.walk : null;
	return walk?.holds(id) ? walk : null;
}

export function canStepForward(id: string): boolean {
	void run.relisted;
	if (!run.shuffle) return neighboursOf(id).next !== null;
	const walk = walkHolding(id);
	if (walk) return walk.goesOn;
	if (indexOf(id) < 0) return false;
	return sequence.length > 1 || (continues !== null && continues.total > 1);
}

export function canStepBack(id: string): boolean {
	void run.relisted;
	if (!run.shuffle) return neighboursOf(id).previous !== null;
	return walkHolding(id)?.canStepBack(id) ?? false;
}

/** Turning Shuffle off on a file read from beyond the page finds the list's own order around it. */
export function toggleShuffle(onScreen: string | null): void {
	const walk = run.walk;
	run.toggle();
	if (run.shuffle || onScreen === null || indexOf(onScreen) >= 0) return;
	const after = walk === null ? -1 : indexOf(walk.start.id);
	const runs = walk?.runsOf(onScreen) ?? true;
	sequence.splice(after + 1, 0, { id: onScreen, runs });
	run.moved();
	void findInList(onScreen);
}

async function findInList(id: string): Promise<void> {
	const more = continues;
	if (more?.locate === undefined) return;
	let at: number | null = null;
	try {
		at = await more.locate(id);
	} catch {
		at = null;
	}
	if (at === null || continues !== more || run.shuffle) return;
	const from = Math.max(0, at - Math.floor(BLOCK / 2));
	let block: Neighbour[] = [];
	try {
		block = await more.fetch(from, BLOCK);
	} catch {
		block = [];
	}
	if (continues !== more || run.shuffle || !block.some((each) => each.id === id)) return;
	sequence = block;
	continues = { ...more, from };
	run.moved();
}

export async function stepForward(id: string): Promise<string | null> {
	const walk = walkFrom(id);
	if (walk) return walk.next(id, { pictures: true });
	return run.shuffle ? null : neighboursOf(id).next;
}

export function stepBack(id: string): string | null {
	if (!run.shuffle) return neighboursOf(id).previous;
	return walkHolding(id)?.previous(id) ?? null;
}

/** The SAME walk Next moves; `wraps: false` is Stop at the end under Shuffle. */
export async function playOn(id: string, rule: EndRule = {}): Promise<string | null> {
	const held = ahead;
	if (held !== null && held.key === aheadKey(id, rule) && !run.shuffle) return held.to;
	return findNext(id, rule);
}

async function findNext(
	id: string,
	{ pictures = false, wraps = true }: EndRule
): Promise<string | null> {
	const walk = walkFrom(id);
	if (walk) return walk.next(id, { pictures, wraps });
	return (pictures ? neighboursOf(id).next : null) ?? (await playOnAfter(id));
}

interface EndRule {
	pictures?: boolean;
	wraps?: boolean;
}

/* --- THE NEXT FILE, found while this one plays: an early plan reserves nothing. */
type AssetDetail = components['schemas']['AssetDetail'];

interface Ahead {
	key: string;
	to: Promise<string | null>;
	next: string | null;
	record: AssetDetail | null;
	bells: number;
	plan: PlaybackPlan | null;
}

/** A record asked for before the latest library or jobs bell may be stale. */
export function bellsRung(): number {
	return libraryChanges.generation + jobChanges.generation;
}

interface HeldRecord {
	record: AssetDetail;
	stale: boolean;
}

let ahead: Ahead | null = null;

function aheadKey(id: string, { pictures = false, wraps = true }: EndRule): string {
	return [id, pictures, wraps, run.shuffle, run.relisted].join(' ');
}

export function lookAhead(id: string, rule: EndRule = {}): void {
	const key = aheadKey(id, rule);
	if (ahead?.key === key) return;
	const walk = walkFrom(id);
	const { pictures = false, wraps = true } = rule;
	const where = walk ? walk.peek(id, { pictures, wraps }) : findNext(id, rule);
	const held: Ahead = { key, to: where, next: null, record: null, bells: 0, plan: null };
	ahead = held;
	void held.to
		.then(async (to) => {
			if (to === null || to === id || ahead !== held) return;
			held.next = to;
			held.bells = bellsRung();
			const record = await api.get<AssetDetail>(`/assets/${to}`);
			if (ahead !== held) return;
			held.record = record;
			if (record.media_type === 'video' && !record.concealed) held.plan = await planFor(to);
		})
		.catch(() => {
			// The end asks for itself; a failed early look changes nothing.
		});
}

export function takeRecord(id: string): HeldRecord | null {
	if (ahead === null || ahead.next !== id || ahead.record === null) return null;
	const record = ahead.record;
	ahead.record = null;
	return { record, stale: bellsRung() !== ahead.bells };
}

let beside: { id: string; plan: Promise<PlaybackPlan> } | null = null;

function planBeside(id: string): void {
	beside = null;
	if (!sequence.some((each) => each.id === id && each.runs)) return;
	const plan = planFor(id);
	plan.catch(() => undefined);
	beside = { id, plan };
}

export function takePlan(id: string): PlaybackPlan | Promise<PlaybackPlan> | null {
	if (beside?.id === id) {
		const asked = beside.plan;
		beside = null;
		return asked;
	}
	if (ahead === null || ahead.next !== id || ahead.plan === null) return null;
	const plan = ahead.plan;
	ahead.plan = null;
	return plan;
}

export function dropAhead(): void {
	ahead = null;
}

/** Read before the row is dropped (`gone` in `AssetModal`). */
export async function stepAwayFrom(deleted: string): Promise<string | null> {
	const walk = walkHolding(deleted);
	if (walk) {
		forgetAsset(deleted);
		return (await walk.next(deleted, { pictures: true })) ?? walk.previous(deleted);
	}
	const { previous, next } = neighboursOf(deleted);
	forgetAsset(deleted);
	return next ?? previous;
}

function indexOf(id: string): number {
	return sequence.findIndex((each) => each.id === id);
}

/*
 * --- DISMISSED, OR LEFT FOR ANOTHER PAGE: a clip carries on in the corner only for the second. The
 * navigation cannot tell them apart, so the panel's close says so, read ONCE.
 */
let dismissing = false;

export function dismissingAsset(): void {
	dismissing = true;
}

export function takeDismissal(): boolean {
	const was = dismissing;
	dismissing = false;
	return was;
}
