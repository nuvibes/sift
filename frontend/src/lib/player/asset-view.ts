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

/* Opening an asset, and the one rule about how.
 *
 * `/asset/{id}` is a real address, and it is ALWAYS a panel. What changes is only what is behind it:
 *
 *   - clicked a tile in the grid -> it opens over the grid you were looking at, so closing puts you
 *     back exactly where you were, scrolled to the same place, without refetching anything
 *   - opened the link directly, or refreshed with it open -> there is nothing behind it, so closing
 *     goes to the library rather than stepping out of Sift
 *
 * The same URL and the same frame, both times. There is deliberately no page form: one address that
 * renders two different screens depending on how you reached it is two screens.
 *
 * This lives here, and is established before any screen uses it, because the alternative is each
 * screen that opens an asset deciding for itself and none of them agreeing.
 */

interface AssetModalState {
	asset: string;
	/** Set when this address was landed on cold. See `App.PageState`. */
	direct?: boolean;
	/* A moment to open at, in milliseconds, when the asset was opened from something that knows one:
	 * a face in a group, where the alternative is landing at the start of a long video and
	 * hunting for the second the face was found at. Absent for an ordinary tile. */
	at?: number;
	/* Where that moment ENDS, for something opened from a saved stretch rather than from a point: a
	 * loop. With both, the player repeats the range instead of running on to the end of the file. */
	until?: number;
}

/*
 * What was on screen when an asset was opened, in the order it was in.
 *
 * "Play through" and the next and previous buttons all mean "the next one in the grid", and the
 * modal has no idea what grid it came from. It is handed an id and nothing else, deliberately, so
 * that it works the same over Browse, Favorites, a search and a collection.
 *
 * Kept in the module rather than in the history entry. It is a list that can run to thousands of
 * ids and history state is serialised into the browser's session storage on every navigation; the
 * modal's own address stays a single id, which is what makes it linkable. The cost is that a
 * refresh loses the sequence, and that is the honest answer anyway, because after a refresh there
 * is no grid behind the page to move through.
 */
let sequence: Neighbour[] = [];

/**
 * How to fetch MORE of the list the panel was opened from, when the run reaches the end of what is
 * on screen.
 *
 * ## Why the sequence alone is not enough
 *
 * The wall is PAGED. It draws one page of a query (sixty-five files out of eight thousand), and
 * `sequence` is a snapshot of that page taken when the panel opened, so "Play through" would stop
 * at the bottom of the page you happened to be looking at. The note above still stands about what
 * `sequence` is; this is the other half, which lets a run keep going past it.
 *
 * ## Why a reader and not the grid
 *
 * The run must not page the wall underneath it. Somebody who opens a file and lets it play should
 * come back to the screen they left, scrolled where they left it, and a run that drove the grid
 * would have walked it to the far end of the library by then. So this reads the same query
 * independently, and the screen behind the panel never moves.
 *
 * Null when the panel was opened from something that is not a paged list at all (a face in a
 * pile, a link in a file's own facts), and then the run is what was handed in.
 */
export interface Continues {
	/** Where `sequence[0]` sits in the whole list, so a walked offset can be turned into a global one. */
	from: number;
	/** How many there are in ALL, which is what tells a run it has reached the end rather than a page. */
	total: number;
	/**
	 * Whether this list's server can hand it back in a seeded shuffle: whether `fetch` honours an
	 * `order`. Declared by the list rather than assumed, for the reason `RowSource.sorts` is: the
	 * wall of Loops has no shuffled order, and asking for one would be refused.
	 */
	shuffles: boolean;
	/**
	 * One block of the same list, from a global offset. Empty at the end.
	 *
	 * `order` asks for the block in the server's seeded shuffle instead of the list's own order:
	 * the same set, the same filters, one arrangement per seed. It is how a shuffled run walks past
	 * what is held (see `Walk`). Only asked of a list that says it `shuffles`.
	 */
	fetch(offset: number, limit: number, order?: RunOrder): Promise<Neighbour[]>;
	/**
	 * Where one file sits in this list, in its own order, or null when the list does not hold it.
	 *
	 * Asked when Shuffle is turned off on a file the shuffled walk read from beyond the page: that
	 * file is in no block the panel holds, so its neighbours in the list's own order are known only
	 * to the server. Optional, and a list without it leaves the file where `showStranger` would.
	 */
	locate?(id: string): Promise<number | null>;
}

/** Which shuffled arrangement of a list to read: the server's permutation for this seed. */
export interface RunOrder {
	seed: number;
}

let continues: Continues | null = null;

/** How many more to ask for at a time. A page of the wall, near enough, so a run crossing a
 *  boundary costs one request rather than one per file. */
const BLOCK = 60;

/**
 * One thing in the list behind an open asset, and whether it runs.
 *
 * The second field exists for play-through and nothing else. "Play through" means keep playing, and
 * a photograph has nothing to play: the video element that would fire `ended` and move on is not
 * even built for a still, so a run of clips with a photo in the middle stops dead at the photo and
 * waits for somebody to notice. Skipping it is what the setting says it does.
 *
 * It is only ever consulted when moving on BY ITSELF. The next and previous buttons step onto
 * everything, because a person pressing Next means the next one. If they wanted to skip the
 * photos they would not be pressing a button per item.
 */
interface Neighbour {
	id: string;
	/** Whether it plays on its own: a video or a GIF, rather than a still. */
	runs: boolean;
}

/** Open an asset over the current page. Use from a grid or a list, never from a bare link.
 *
 * `among` is what is on screen, in order, so the player can move to the next one. Only what has
 * actually been loaded: a grid that has fetched two pages can move through two pages, and the end
 * of the list is the end of the list rather than a request nobody asked for.
 */
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
	// A new list, so whatever shuffled order was being walked was an order of another one.
	run.reset();
	openedLoop = loop === null ? null : { file: id, loop };
	// Opened from the wall a typed search narrowed: which result of which search (`visits.ts`).
	noteSearchOpen(page.route.id, page.url.searchParams, id);
	push(id, at, until);
}

/*
 * WHERE A PANEL WAS OPENED FROM, for the sittings it reports.
 *
 * A sitting keeps which screen it happened on and what it was opened from, because none of that can
 * be worked out afterwards (see `SittingPlace`). The panel is one frame over every screen, so it
 * cannot know which from anything it was handed; what it can read is the screen BEHIND it, which
 * shallow routing leaves standing: `pushState` changes the address bar and leaves `page.route` and
 * `page.url` on the screen underneath, so both still describe what the panel was opened over for as
 * long as it is open. A cold arrival has nothing behind it and its route is the file's own.
 *
 * One table from a route to the word the server keeps, and a test that refuses a route that is on
 * neither this table nor `OPENS_NO_FILE`, so a new screen that can open a file is named when it
 * is added, rather than read as `other` for ever.
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
	/* A file named on Insights (a list of the files watched most, a recap's) opens over it. */
	'/insights': 'insights',
	'/insights/stats': 'insights',
	'/insights/recaps': 'insights',
	'/insights/recaps/[id]': 'insights'
};

/**
 * The words whose screen is about ONE thing, and so name it: the id in the screen's address. A
 * sitting keeps the thing as well as the kind of screen (`SittingPlace.opened_from_id`), because
 * "opened from a person" with no person cannot be added up into anybody's figures later.
 *
 * Every word on `OPENED_FROM` that a screen about one thing says is here, and a test holds it so:
 * a new screen about a thing, given its word and not this, would keep every sitting from it
 * without the thing.
 */
export const NAMES_ITS_THING: ReadonlySet<OpenedFrom> = new Set([
	'person',
	'site',
	'tag',
	'collection',
	'photo_set',
	'song'
]);

/**
 * The screens that draw no file of their own, and so can only be behind a panel the corner player
 * handed back while somebody was there. Their sittings say `other`, which is the truth about them.
 */
export const OPENS_NO_FILE: readonly string[] = [
	// The phone's own screens: a list of places, a list of settings, and the remote's controls.
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
	/* A swap lists people, counts and titles between two libraries and opens none of them. */
	'/swap',
	'/tags',
	'/tags/new',
	'/theater'
];

/**
 * The saved Loop the panel was opened on, and the file it is a stretch of.
 *
 * Paired with its file rather than kept alone, because the panel walks on: stepping to the next
 * file in the wall of Loops is still a sitting opened from Loops, and is not that Loop any more.
 */
let openedLoop: { file: string; loop: string } | null = null;

/** An id as the server mints them, and nothing an address could carry instead. */
const AN_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;

/** What the screen behind the panel was opened from: the word, the one thing, and a search's words. */
type Behind = Pick<SittingPlace, 'opened_from' | 'opened_from_id' | 'searched'>;

/** The screen behind the panel, as a sitting keeps it. See `OPENED_FROM` and `NAMES_ITS_THING`. */
function openedFrom(): Behind {
	const route = page.route.id;
	if (route === null) return { opened_from: 'other' };
	if (route === '/browse') {
		// The library narrowed to one folder is that folder's wall; with words typed into it, it is
		// a SEARCH, which is a different way of arriving at a file from scrolling to it. The search
		// is named by its words, and the server keeps the record they were last searched under.
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

/**
 * Where a sitting in the panel is happening, for the file it is showing.
 *
 * The kept filter comes from the bar, which is the one place that works out which one the screen
 * is (see `appliedKept` there). This reads its answer rather than working it out a second time.
 */
export function panelPlace(id: string): SittingPlace {
	return {
		screen: 'panel',
		...openedFrom(),
		loop: openedLoop?.file === id ? openedLoop.loop : null,
		kept_filter: screenBar.keptInForce
	};
}

/**
 * Open a file again without touching what it was opened from.
 *
 * For the small panel handing a clip back to full size. It knows the id and the moment and nothing
 * else, so passing an empty list, as opening does, would throw away the grid the file came out of
 * and the next and previous buttons would disappear for the rest of the sitting.
 */
export function reopenAsset(id: string, at?: number): void {
	push(id, at);
}

/**
 * Whether a run has anywhere to go from here, answered WITHOUT a request.
 *
 * It decides whether the advance handler is passed to the player at all, so it has to be
 * synchronous, and it must not be answered from the loaded page: answered there, "play through"
 * would be handed no handler once the page in hand ran out, and stop at the bottom of the screen
 * on a library of thousands.
 *
 * True while anything ELSE in the list could play. `loop_all` wraps at the far end, so on a list
 * with two playable files in it there is always somewhere to go; on one with a single video and
 * nine hundred photographs there is not, and the run correctly stops rather than repeating.
 *
 * `pictures` IS THE ACCOUNT'S ANSWER TO "hold photographs too", and without it this would tell a
 * run of photographs that it had nowhere to go, on an install that had asked for exactly that. A
 * still carries `runs: false` because it has no end to reach, which is a fact about the FILE;
 * whether a run steps over it or waits on it is a preference, and one flag cannot answer both.
 * Handed in rather than read here: this module knows about a list of files and nothing about
 * settings, and `AssetModal` is already the place that reads the preference.
 */
export function runGoesOn(id: string, { pictures = false }: { pictures?: boolean } = {}): boolean {
	if (continues && continues.total > sequence.length) return true;
	return sequence.some((each) => (each.runs || pictures) && each.id !== id);
}

function push(id: string, at?: number, until?: number): void {
	// Changes the address without running the asset route's load: the page under it stays mounted and
	// keeps its scroll. Back closes the modal rather than leaving the grid, because this is a history
	// entry like any other.
	//
	// The moment goes in the address as well as the state, so the two halves of this address agree:
	// opened over a grid it is read from the state, and the same link pasted or refreshed is read
	// from `?t=` by the page. One address, the same place, either way.
	//
	// `until` rides along the same way, so a saved loop is a LINK: sent to somebody else, or
	// pasted back in tomorrow, it still opens the stretch rather than the file it came from.
	const moment = at === undefined ? '' : `?t=${at}${until === undefined ? '' : `&until=${until}`}`;
	// A close nobody read must not still be standing when this panel is left. See `dismissingAsset`.
	takeDismissal();
	pushState(`/asset/${id}${moment}`, { asset: id, at, until } satisfies AssetModalState);
}

/** Move to another asset within the open modal, without adding a history entry for it.
 *
 * `replaceState`, so closing after playing through eleven clips is one press of Back rather than
 * eleven. Watching a run of videos is one visit to the modal; which one it is showing is not a
 * place you navigated to.
 */
/**
 * Show a file that is not in the list, and give it a place in it.
 *
 * Randomize opens a file from anywhere in the library, and the list the panel walks is one page of
 * whatever was on screen, so the new file would have no neighbours and the bar's outer pair would
 * go blank. It is spliced in just after the file it was jumped from: Previous is where you came
 * from, and Next carries the list on from there. A file already in the list simply shows; nothing
 * moves.
 */
export function showStranger(id: string, from: string | null, runs: boolean): void {
	if (indexOf(id) < 0) {
		const after = from === null ? -1 : indexOf(from);
		sequence.splice(after + 1, 0, { id, runs });
	}
	// A shuffled walk takes it as a DETOUR: Back returns to where the walk stood, Next carries the
	// walk on from there. Splicing it into the order would make it a file the walk had drawn.
	const walk = run.shuffle ? run.walk : null;
	if (walk && from !== null && walk.holds(from) && !walk.holds(id)) walk.detour = id;
	showAsset(id);
}

/**
 * Take a file OUT of the list the panel is walking, because it does not exist any more.
 *
 * Deleting the file that is open is the one act that can make the list wrong while somebody is
 * standing in it. Without this the panel steps on, and stepping BACK lands on the file that was
 * just deleted, which answers 404 and draws "Not found" inside a dialog that was working a moment
 * ago. The row is the client's copy of what was on screen, so it is the client's to correct.
 *
 * Only the list. What the WALL behind the panel does about the same file is the wall's, and it is
 * already told: the delete rings `libraryChanges`.
 */
export function forgetAsset(id: string): void {
	const at = indexOf(id);
	if (at >= 0) sequence.splice(at, 1);
	run.walk?.forget(id);
	ahead = null;
}

export function showAsset(id: string): void {
	// Carried forward. Stepping to the next clip does not put a screen behind the panel, so a panel
	// that was opened cold is still one after moving through five of them, and closing it still
	// has to go to the library rather than out of Sift.
	replaceState(`/asset/${id}`, { asset: id, direct: page.state.direct } satisfies AssetModalState);
	if (ahead?.next !== id || ahead.plan === null) planBeside(id);
}

/**
 * Open the asset this address names, for the route that runs when the address was navigated TO.
 *
 * `replaceState` rather than a push: the address is already right, and this entry becomes the panel
 * rather than gaining one in front of it, so a single Back leaves the way it arrived.
 *
 * ## `cold` is what `direct` means
 *
 * `direct` decides where closing goes: with nothing behind the panel there is nowhere to step back
 * to, so it leaves for Browse; otherwise it steps back to whatever it was opened over. This route
 * runs for two quite different arrivals: a cold load or a refresh, where Browse is right, and a
 * plain `<a href="/asset/...">` followed inside the app, where there is a perfectly good screen
 * behind it in history.
 *
 * The caller answers it, because the caller is the only thing that can: `isFirstScreen` in
 * `navigation.svelte.ts` says whether this document showed any other screen before it. (Not
 * `afterNavigate`'s `from`: the route that asks is drawn after the session check, too late to hear
 * the first navigation's callback. See the route.)
 *
 * `cold` defaults to true, which is the answer that cannot strand anybody: leaving for Browse from
 * somewhere that had a history entry is a smaller cost than a wrong Back would be.
 */
export function enterAsset(
	id: string,
	at?: number,
	until?: number,
	{ cold = true }: { cold?: boolean } = {}
): void {
	takeDismissal();
	// An address arrived at names no Loop, even when it carries a stretch: the link is the Loop's
	// shape and not its identity, and a sitting claiming one it cannot name would be a guess.
	openedLoop = null;
	replaceState('', { asset: id, at, until, direct: cold } satisfies AssetModalState);
}

/**
 * Leave the panel the way its own close does.
 *
 * Back, normally: the panel is a history entry over the screen somebody was already on, so leaving
 * it puts that screen back exactly as they left it. A panel landed on cold has nothing behind it,
 * and a step back from the first entry leaves Sift entirely. So that one goes to the library
 * instead. `direct` is set by the routes that only run on a cold load (`enterAsset` above).
 *
 * ONE function because two places make this exit: the shell's close, and the corner player's
 * hand-over. A hand-over that read "is there a panel" (`page.state.asset`, true on a cold load too)
 * where the question is "is there anything BEHIND it" would step back out of Sift from a file page
 * opened by its own address.
 */
export function leaveAssetPanel(): void {
	if (page.state.direct) {
		void goto('/browse');
		return;
	}
	history.back();
}

/**
 * Take a plain left-click on an asset LINK and open the panel where it stands.
 *
 * ## The invariant this exists to hold
 *
 * `/asset/{id}` is a real address and always a panel. But WHAT IS BEHIND IT depends entirely on
 * how it was reached, and the route's own note says so: clicking a tile never runs the route; it
 * runs only when there is nothing behind the address yet.
 *
 * A bare `<a href="/asset/...">` runs the route. SvelteKit takes the click, tears down the screen
 * that was there, mounts the asset route, and `enterAsset` marks the panel `direct`. So the grid
 * behind it goes blank and rebuilds, and closing afterwards lands on the library instead of back
 * where you were.
 *
 * `AssetLink` is what asset links use, and this is the rule it applies: here, in the module that
 * already owns every other way an asset opens, rather than in the component, exactly as
 * `openSettingsInstead` sits beside `openSettings`.
 *
 * ## Why it stays a real anchor
 *
 * Everything a link can do is worth keeping: middle-click and ctrl-click open a tab, "copy link
 * address" copies an address that genuinely works, and the address is the one a person would share.
 * So only a plain left-click is taken. Every modifier means they asked for something else, and
 * answering that with a panel in THIS window ignores what they asked for.
 *
 * `among` is what to move through afterwards, for a caller that has a list. Empty is honest for the
 * callers that do not (a link inside a file's own facts, a row on Jobs), and leaves the panel
 * with no next and previous rather than inventing a run out of the library.
 */
export function openAssetInstead(event: MouseEvent, id: string, among: Neighbour[] = []): void {
	if (event.defaultPrevented) return;
	if (event.button !== 0) return;
	if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
	event.preventDefault();
	openAsset(id, among);
}

/** The ids either side of this one in whatever it was opened from. Null where there is no more. */
export function neighboursOf(id: string): { previous: string | null; next: string | null } {
	const at = indexOf(id);
	if (at < 0) return { previous: null, next: null };
	return {
		previous: at > 0 ? sequence[at - 1].id : null,
		next: at < sequence.length - 1 ? sequence[at + 1].id : null
	};
}

/** The next held file that plays, stepping over stills; null when none is held. `playOnAfter`
 *  is the one that reads more. */
export function nextPlayableAfter(id: string): string | null {
	const at = indexOf(id);
	if (at < 0) return null;
	for (let index = at + 1; index < sequence.length; index += 1) {
		if (sequence[index].runs) return sequence[index].id;
	}
	return null;
}

/**
 * Where a run goes when a clip finishes by itself: END TO END, across the whole list.
 *
 * `sequence` is one PAGE of a paged wall, so walking it alone would stop at the bottom of the files
 * on screen. The three answers, in order:
 *
 * **Something further down what is already held.** Free, and covers every step but the last one of
 * a page.
 *
 * **The next block of the same query.** Fetched here, appended to `sequence`, and asked again, so
 * the run crosses a page boundary without the wall underneath moving. It keeps asking while the
 * blocks come back with nothing that plays in them, because a run of a hundred photographs is an
 * ordinary library, and one that gave up on the first such block would stop in the middle of the
 * list.
 *
 * **The beginning.** At the true end of the list (not the end of a page) a run WRAPS. That is
 * what "play through" means in a Theater cell (`loop_all` rewinds its source and starts again), and
 * a player and a wall disagreeing about what the end means is worse than either answer on its own.
 * Null only when nothing in the whole list plays, which stops.
 */
export async function playOnAfter(id: string): Promise<string | null> {
	const straight = nextPlayableAfter(id);
	if (straight !== null) return straight;

	// Everything held has been walked. Go and get the rest, a block at a time, from wherever the
	// held sequence ends in the WHOLE list rather than from where it ends in itself.
	while (continues && continues.from + sequence.length < continues.total) {
		const got = await fetchMore();
		if (got === 0) break;
		const next = nextPlayableAfter(id);
		if (next !== null) return next;
	}

	// The end of the list. Round to the front, skipping the stills exactly as a step does.
	const first = sequence.find((each) => each.runs);
	if (!first) return null;
	// Wrapping onto the file that just finished is repeating it, which is `loop_one`'s answer and
	// not this one, unless it is the only thing in the list that plays, and then repeating it is
	// the honest answer rather than stopping dead.
	if (first.id !== id) return first.id;
	const another = sequence.find((each) => each.runs && each.id !== id);
	return another ? another.id : first.id;
}

/** One more block onto the end of what is held. How many arrived, so a caller can stop. */
async function fetchMore(): Promise<number> {
	if (!continues) return 0;
	const at = continues.from + sequence.length;
	let got: Neighbour[] = [];
	try {
		got = await continues.fetch(at, BLOCK);
	} catch {
		// A run is not worth an error message. It stops where it is.
		return 0;
	}
	// Ids already held are dropped rather than appended. A wall re-ordered by an import underneath
	// a run would otherwise hand the same file back and the run would sit on it for ever.
	const held = new Set(sequence.map((each) => each.id));
	const fresh = got.filter((each) => !held.has(each.id));
	sequence = [...sequence, ...fresh];
	// What ARRIVED, not what was fresh: a whole block of files the run has already seen still means
	// the list moved on, and answering zero there would stop a run that could keep going.
	return got.length;
}

/*
 * --- WHERE NEXT AND BACK GO: the one answer both players ask ------------------------------------
 *
 * The full-size panel and the corner player are two ways of looking at one run, so they ask the
 * same functions and neither decides for itself. In order, the answer is the list's own neighbour.
 * With Shuffle on it is the WALK: one fixed shuffled order of the list (see `run.svelte.ts`), so
 * Back returns to the file just watched, nothing comes round twice until everything has played, and
 * the corner player shuffles exactly as the panel does. A fresh random pick per press would have no
 * memory of what had played, and no Back.
 */

/** What the walk reads: the list held, and the rest of it through `continues` where there is more. */
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

/**
 * The walk this file is part of, started from it when there is none. Never for a question asked
 * while drawing. Null with Shuffle off, and for a file in no list.
 */
function walkFrom(id: string): Walk | null {
	if (!run.shuffle) return null;
	if (run.walk?.holds(id)) return run.walk;
	const at = indexOf(id);
	if (at < 0) return null;
	run.walk = new Walk(sequence[at], walkSource());
	return run.walk;
}

/** The walk that already holds this file, without starting one. */
function walkHolding(id: string): Walk | null {
	const walk = run.shuffle ? run.walk : null;
	return walk?.holds(id) ? walk : null;
}

/**
 * Whether Next has anywhere to go, answered without a request. In order, the list's next file; with
 * Shuffle on, anything else in the list, which includes the case where this is the last one.
 */
export function canStepForward(id: string): boolean {
	// Read so a list moved under the file on screen draws its Next and Back again.
	void run.relisted;
	if (!run.shuffle) return neighboursOf(id).next !== null;
	const walk = walkHolding(id);
	if (walk) return walk.goesOn;
	if (indexOf(id) < 0) return false;
	return sequence.length > 1 || (continues !== null && continues.total > 1);
}

/** Whether Back has anywhere to go. With Shuffle on, only once a step has been taken. */
export function canStepBack(id: string): boolean {
	void run.relisted;
	if (!run.shuffle) return neighboursOf(id).previous !== null;
	return walkHolding(id)?.canStepBack(id) ?? false;
}

/**
 * Shuffle, pressed, while `onScreen` is the file showing.
 *
 * Turned OFF on a file the shuffled walk read from beyond the page, the list's own order has to
 * be found again around it, or the panel would hold no neighbours for it and Next and Back would
 * go blank. Immediately it is placed after the file the walk began on, the rule `showStranger`
 * keeps for a file from elsewhere; then the list is asked where the file really sits, and the block
 * around that position replaces what is held.
 */
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

/* The block of the list's own order around a file the panel was not holding. See `toggleShuffle`. */
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
	// A list re-opened or reshuffled while this was in the air is not the list it was asked of.
	if (continues !== more || run.shuffle || !block.some((each) => each.id === id)) return;
	sequence = block;
	continues = { ...more, from };
	run.moved();
}

/** Next, pressed: the next file, whatever it is. A person pressing Next means the next one. */
export async function stepForward(id: string): Promise<string | null> {
	const walk = walkFrom(id);
	if (walk) return walk.next(id, { pictures: true });
	return run.shuffle ? null : neighboursOf(id).next;
}

/** Back, pressed: the file before this one. In the walk, the one just watched. */
export function stepBack(id: string): string | null {
	if (!run.shuffle) return neighboursOf(id).previous;
	return walkHolding(id)?.previous(id) ?? null;
}

/**
 * Where a run goes when a clip finishes by itself. With Shuffle on, the SAME walk Next moves,
 * so play-through and the buttons are one order, not two.
 *
 * `pictures` is the account's answer to whether a run holds photographs (see `runGoesOn`). In
 * order, a run that holds them steps onto the list's next file, and otherwise onto the next thing
 * that plays, reading past the page and wrapping at the far end (`playOnAfter`).
 *
 * `wraps: false` is Stop at the end under Shuffle: the walk stops where the shuffled list runs out
 * rather than going round again (`run.movesOnAfter`). In order a run under Stop at the end never
 * asks, because it stops at the file's end.
 */
export async function playOn(id: string, rule: EndRule = {}): Promise<string | null> {
	const held = ahead;
	// A shuffled walk only peeked, so the end still steps it.
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

/** What the end of a file asks of `playOn`: whether a run holds photographs, and whether it wraps. */
interface EndRule {
	pictures?: boolean;
	wraps?: boolean;
}

/* --- THE NEXT FILE, FOUND WHILE THIS ONE PLAYS: its record and plan, so the end waits only on
 * the media. A plan names an address and reserves nothing, so an early one is safe. */
type AssetDetail = components['schemas']['AssetDetail'];

interface Ahead {
	key: string;
	to: Promise<string | null>;
	next: string | null;
	record: AssetDetail | null;
	/* `bellsRung` when the record was asked for. */
	bells: number;
	plan: PlaybackPlan | null;
}

/** How many library and jobs bells have rung: a record asked for before the latest may be stale. */
export function bellsRung(): number {
	return libraryChanges.generation + jobChanges.generation;
}

/** A record found ahead, and whether a bell has rung since it was asked for. */
interface HeldRecord {
	record: AssetDetail;
	stale: boolean;
}

let ahead: Ahead | null = null;

function aheadKey(id: string, { pictures = false, wraps = true }: EndRule): string {
	return [id, pictures, wraps, run.shuffle, run.relisted].join(' ');
}

/** Find where the end of `id` goes, and fetch what showing it needs. Once per file and rule. */
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

/** The record found ahead for this file, once. */
export function takeRecord(id: string): HeldRecord | null {
	if (ahead === null || ahead.next !== id || ahead.record === null) return null;
	const record = ahead.record;
	ahead.record = null;
	return { record, stale: bellsRung() !== ahead.bells };
}

/* A plan asked at the press, beside the record rather than after it. */
let beside: { id: string; plan: Promise<PlaybackPlan> } | null = null;

/** Ask how a pressed file plays while its record is read; only one the list says runs. */
function planBeside(id: string): void {
	beside = null;
	if (!sequence.some((each) => each.id === id && each.runs)) return;
	const plan = planFor(id);
	// Unclaimed, it is dropped; a claimed one's failure is the player's to hear.
	plan.catch(() => undefined);
	beside = { id, plan };
}

/** The playback plan found ahead for this file, or asked at its press, once. */
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

/** Forget what was found ahead: the player looking is closing. */
export function dropAhead(): void {
	ahead = null;
}

/**
 * Where to go when the file on screen has been deleted: on, or back, or nowhere. Read before the
 * row is dropped, or the list would answer about a gap. See `gone` in `AssetModal`.
 */
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
 * --- WHETHER THE PANEL IS BEING DISMISSED, OR LEFT FOR SOMEWHERE ELSE --------------------------
 *
 * Both look identical from the outside: `page.state.asset` goes and the panel unmounts. But they are
 * two different acts and one behaviour depends on telling them apart: a clip carries on in the
 * mini player when somebody FOLLOWS a chip out of the panel, and stops when they have finished with
 * it (see `popout.leave_to_mini`).
 *
 * The navigation itself cannot answer it. Escape and the veil go back through history, which is a
 * `popstate`; a chip is a `link` and a face's "Show the face group" is a `goto`, and so is the way out
 * of a panel that was opened cold, which is the same shape as the departure. Reading the type would
 * therefore be right most of the time and silently wrong for anybody who opened a link directly,
 * which is the worst kind of nearly.
 *
 * So the act says what it is, at the one place that knows: the panel's own close. `AssetModal` calls
 * `dismissingAsset` on its way out, and whoever needs the answer reads it ONCE. The read clears
 * it, because an intent that outlived the navigation it was about would make the next departure look
 * like a dismissal. Opening a panel clears it too, for the same reason from the other end: a close
 * that nobody read must not still be sitting there when the next panel is left.
 */
let dismissing = false;

/** The panel is being closed, rather than left behind for another page. Said by the panel itself. */
export function dismissingAsset(): void {
	dismissing = true;
}

/** Whether the navigation now beginning is that dismissal. Reading it CONSUMES it (see above). */
export function takeDismissal(): boolean {
	const was = dismissing;
	dismissing = false;
	return was;
}
