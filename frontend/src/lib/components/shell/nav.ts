import type { Verb } from '$lib/components/common/verbs';
import type { IconName } from '$lib/design/icons';

/* Where you can go. One list, read by the rail and the mobile tabs, so the two cannot disagree
 * about what the app contains.
 */

export interface NavItem {
	/**
	 * What this destination is called in storage, for as long as it exists: not the address, so a
	 * renamed address cannot quietly reshuffle somebody's remembered rail.
	 */
	id: string;
	href: string;
	label: string;
	icon: IconName;
	/** Rendered only for an admin. See below: this is not what stops a guest. */
	admin?: boolean;
	/**
	 * Opens over the current screen rather than replacing it; the href stays a real address.
	 * Settings, reached in the middle of doing something else.
	 */
	panel?: boolean;
	/** Cannot be hidden from the rail: Settings alone, since it is where a hidden item is put back. */
	fixed?: boolean;
	/**
	 * What this destination's own row offers on a right-click, beyond arranging the rail: `Verb`s,
	 * like every other menu, so a test can read them and a disabled one has its reason. A function
	 * of `RailFacts`, called while the menu is drawn, since what a row can do follows its state.
	 */
	verbs?: (facts: RailFacts) => readonly Verb[];
}

/**
 * Where a destination is found on a phone, which has no rail: `tab` on the bottom bar
 * (`MOBILE_TABS`), `library` a row of the Library screen, `more` on More, `cut` not offered (its
 * screen says so to anybody who types its address). Declared on every destination, and
 * `nav.test.ts` refuses one without it.
 */
type PhoneHome = 'tab' | 'library' | 'more' | 'cut';

/** A destination the rail draws, and where the same destination is on a phone. */
interface RailItem extends NavItem {
	phone: PhoneHome;
}

/**
 * What a row's menu may know about the application, beyond where the row goes: handed in by the
 * rail, which holds the store, so this list of destinations imports no work queue. Narrow on
 * purpose.
 */
interface RailFacts {
	/** The Downloads dot: what colour it is showing, or none. See `imports.downloadStatus`. */
	readonly downloadStatus: 'error' | 'success' | 'none';
	/** Put the dot out: the same thing opening the Downloads screen does. */
	clearDownloadStatus(): void;
}

/**
 * The Downloads row's own verb: put out the dot without opening the screen.
 *
 * "Mark downloads as seen", not "Clear", which in this application removes rows; this removes
 * nothing, and is scoped to this window, as the dot is. Refused rather than absent while the dot is
 * out, so the menu does not change shape.
 */
function downloadsVerbs(facts: RailFacts): readonly Verb[] {
	const nothingNew = facts.downloadStatus === 'none';
	return [
		{
			id: 'mark-downloads-seen',
			label: 'Mark downloads as seen',
			icon: 'done_all',
			disabled: nothingNew,
			why: 'Nothing new',
			run: () => facts.clearDownloadStatus()
		}
	];
}

/**
 * The window Theater needs: the width the desktop layout begins at, the rail's floor. A wall still
 * plays in the narrowest desktop window; below this the screen says it needs a wider one.
 */
export const THEATER_MIN_WIDTH = '(min-width: 768px)';

/**
 * Whether Theater has a screen at a phone's width. It does not.
 *
 * A phone fills its screen with one video, so a wall would be thumbnails; the phone has the Remote
 * instead, driving the walls and players at the desk. The one switch the tab, the Remote's address
 * and Theater's phone home read, so a phone Theater is one change away.
 */
export const THEATER_ON_A_PHONE: boolean = false;

/** Theater's second mode on a phone: the screens at the desk, to drive from the phone. */
export const THEATER_DESK_HREF = '/theater?mode=desk';

/** Where the phone's Theater tab opens. See `THEATER_ON_A_PHONE`. */
export function theaterTabHref(onAPhone: boolean): string {
	return onAPhone ? '/theater' : '/remote';
}

/**
 * Where the Remote's own address sends a phone: nowhere while it is the Theater tab's screen, and
 * Theater's second mode once Theater has a phone screen.
 */
export function remoteGoesTo(onAPhone: boolean): string | null {
	return onAPhone ? THEATER_DESK_HREF : null;
}

/**
 * The rule across the rail, as a position in the order rather than a fixed boundary: the ways of
 * LOOKING at a library above, the places you go TO below. Items can be dragged across it.
 */
export const RAIL_DIVIDER = '--';

/*
 * Every destination the rail can show, in the order it ships (`DEFAULT_RAIL_ORDER`).
 *
 * Above the rule, ways of looking: everything, its groupings, the finer ways in, Favorites, and
 * Theater. Below, places to go: Organize, Downloads, Hidden, Insights, Settings, then Recently
 * viewed, the row reached most often, at the end of the reach. Storage and the machine's work are
 * Settings sections. `admin: true` only hides a door the server would refuse; the refusal is the
 * control.
 */
export const RAIL_NAV: RailItem[] = [
	{ id: 'browse', href: '/browse', label: 'Browse', icon: 'browse', phone: 'tab' },
	{ id: 'people', href: '/people', label: 'People', icon: 'person', phone: 'library' },
	{ id: 'sites', href: '/sites', label: 'Sites', icon: 'public', phone: 'library' },
	{ id: 'collections', href: '/collections', label: 'Collections', icon: 'box', phone: 'library' },
	/* Pictures that arrived together; after Collections, which somebody assembled. */
	{
		id: 'photo-sets',
		href: '/photo-sets',
		label: 'Photo Sets',
		icon: 'photo_library',
		phone: 'library'
	},
	{ id: 'tags', href: '/tags', label: 'Tags', icon: 'shoppingmode', phone: 'library' },
	/* The songs the files carry, still at `/songs`, which bookmarks and stored orders name. A finer
	   way in, like a tag, and about what plays, like a Loop. */
	{ id: 'songs', href: '/songs', label: 'Music', icon: 'music_note_2', phone: 'library' },
	/* The marked stretches: a piece of one file, the smallest unit the library has. */
	{ id: 'loops', href: '/loops', label: 'Loops', icon: 'all_inclusive', phone: 'library' },
	{ id: 'favorites', href: '/favorites', label: 'Favorites', icon: 'favorite', phone: 'library' },
	/* Several videos at once: a way of LOOKING, so the last row above the rule. No `admin`, as for
	 * Browse. Cut from a phone, which has the Remote (`THEATER_ON_A_PHONE`). */
	{
		id: 'theater',
		href: '/theater',
		label: 'Theater',
		icon: 'interactive_space',
		phone: THEATER_ON_A_PHONE ? 'tab' : 'cut'
	},
	/* The first row below the rule: where files become the groups above it. */
	{
		id: 'organize',
		href: '/organize',
		label: 'Organize',
		icon: 'inbox',
		admin: true,
		phone: 'library'
	},
	/* The bare arrow, the application's one mark for downloading. What is new here is said by the
	 * status dot cut into the glyph, and only when it is true. */
	{
		id: 'downloads',
		href: '/downloads',
		label: 'Downloads',
		icon: 'download',
		admin: true,
		verbs: downloadsVerbs,
		phone: 'tab'
	},
	/* The vault as a place to LOOK at what is hidden; the top bar's control still opens and shuts it
	 * from anywhere. Called Hidden, as the top bar calls it (`$lib/shell/vault.svelte`). */
	{ id: 'hidden', href: '/hidden', label: 'Hidden', icon: 'visibility_off', phone: 'library' },
	/* What you viewed, organized and added, in numbers. No `admin`: an account's figures are its
	 * own, and the server leaves Sift's own block out of a guest's answer. */
	{ id: 'insights', href: '/insights', label: 'Insights', icon: 'insights', phone: 'library' },
	{
		id: 'settings',
		href: '/settings',
		label: 'Settings',
		icon: 'settings',
		panel: true,
		fixed: true,
		phone: 'more'
	},
	/* Last of all: the row below the rule reached most often, at the end of the reach. */
	{ id: 'recent', href: '/recent', label: 'Recently viewed', icon: 'history', phone: 'library' }
	/* Profile is the first section of Settings > You, where everything that is yours already is.
	 * The DESIGN GALLERY (`/design`), in a build that includes it, is reached by typing its
	 * address: a row about the application itself is in the way of somebody browsing files. */
];

/**
 * The arrangement Sift ships with, and what "reset" puts back. The rule is one of the entries.
 * This and `RAIL_NAV` must agree or a destination is quietly never drawn; `nav.test.ts` refuses
 * the pair when they differ.
 */
export const DEFAULT_RAIL_ORDER: string[] = [
	'browse',
	'people',
	'sites',
	'collections',
	'photo-sets',
	'tags',
	'songs',
	'loops',
	'favorites',
	'theater',
	RAIL_DIVIDER,
	'organize',
	'downloads',
	'hidden',
	'insights',
	'settings',
	/* Under Settings, at the end of the reach; anybody who disagrees drags it. */
	'recent'
];

/** A destination by its id, or nothing when the id is not one of ours. */
export function navItem(id: string): NavItem | undefined {
	return RAIL_NAV.find((item) => item.id === id);
}

/** A destination the rail declares, by its id. For the lists below, which name only real ones. */
function declared(id: string): RailItem {
	const item = RAIL_NAV.find((one) => one.id === id);
	if (!item) throw new Error(`nav: no destination is declared as '${id}'`);
	return item;
}

/**
 * The rows of the Library screen: Recently viewed first, the most reached, then the rail's shipped
 * order. Not the rail's arrangement: on a phone this list is the only way to a row.
 */
export const LIBRARY_ROWS: RailItem[] = [
	declared('recent'),
	...DEFAULT_RAIL_ORDER.filter((id) => id !== RAIL_DIVIDER && id !== 'recent')
		.map(declared)
		.filter((item) => item.phone === 'library')
];

/* A phone has no rail: its bottom bar holds the few places reached from anywhere, and the rest is
 * one press into Library or More. Not rearrangeable and not the rail's stored order, so a desktop
 * change never reshapes a phone. Searching is the box on every screen, not a tab. */

/** A tab of the phone bar, and the addresses it stays lit on beyond its own. */
interface MobileTab extends NavItem {
	/** The screens reached from this tab's own list, which are still "in" it. */
	covers?: readonly string[];
}

/**
 * The Remote's tab: the phone driving what is open at the desk. A tab, reached while something
 * plays; every signed-in person's; not a rail destination, since a desk has the screens.
 */
const REMOTE_TAB: MobileTab = {
	id: 'remote',
	href: '/remote',
	label: 'Remote',
	icon: 'settings_remote'
};

/** The third tab: Theater where a phone has a Theater screen, else the Remote. */
export function thirdTab(onAPhone: boolean): MobileTab {
	return onAPhone
		? { ...declared('theater'), href: theaterTabHref(true), covers: ['/theater', '/remote'] }
		: REMOTE_TAB;
}

/* Browse, Library, Remote, Downloads, More. */
export const MOBILE_TABS: MobileTab[] = [
	declared('browse'),
	{
		id: 'library',
		href: '/library',
		label: 'Library',
		icon: 'newsstand',
		covers: LIBRARY_ROWS.map((item) => item.href)
	},
	thirdTab(THEATER_ON_A_PHONE),
	declared('downloads'),
	{
		id: 'more',
		href: '/more',
		label: 'More',
		icon: 'more_vert',
		/* Settings is reached from More on a phone, so a section open over it is still More. */
		covers: ['/settings']
	}
];

/** Whether a tab is lit at `pathname`: its own address, or one of the screens its list leads to. */
export function isTabActive(tab: MobileTab, pathname: string): boolean {
	return [tab.href, ...(tab.covers ?? [])].some((href) => isActive(href, pathname));
}

/** Is `href` the section the current path is in? `/browse` is active at `/browse?tags=x`. */
export function isActive(href: string, pathname: string): boolean {
	return pathname === href || pathname.startsWith(href + '/');
}
