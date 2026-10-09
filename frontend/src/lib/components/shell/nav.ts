import type { Verb } from '$lib/components/common/verbs';
import type { IconName } from '$lib/design/icons';

// One list of destinations, read by the rail and the mobile tabs so they cannot disagree.

export interface NavItem {
	/** The storage name, not the address, so a renamed address cannot reshuffle a rail. */
	id: string;
	href: string;
	label: string;
	icon: IconName;
	/** Rendered only for an admin; the server's refusal is what stops a guest. */
	admin?: boolean;
	/** Opens over the current screen (Settings); the href stays a real address. */
	panel?: boolean;
	/** Cannot be hidden from the rail: Settings alone, since it is where a hidden item is put back. */
	fixed?: boolean;
	/** The row's own right-click `Verb`s, from `RailFacts` read while the menu is drawn. */
	verbs?: (facts: RailFacts) => readonly Verb[];
}

/** Where a destination is on a phone, which has no rail; `nav.test.ts` requires one. */
type PhoneHome = 'tab' | 'library' | 'more' | 'cut';

/** A destination the rail draws, and where the same destination is on a phone. */
interface RailItem extends NavItem {
	phone: PhoneHome;
}

/** What a row's menu may know, handed in by the rail so this list imports no work queue. */
interface RailFacts {
	/** The Downloads dot: what colour it is showing, or none. See `imports.downloadStatus`. */
	readonly downloadStatus: 'error' | 'success' | 'none';
	/** Put the dot out: the same thing opening the Downloads screen does. */
	clearDownloadStatus(): void;
}

/** Put out the Downloads dot without opening the screen; refused, not absent, when out. */
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

/** The width Theater needs: where the desktop layout begins. */
export const THEATER_MIN_WIDTH = '(min-width: 768px)';

/** Whether a phone gets a Theater screen; it does not, as a phone has the Remote instead. */
export const THEATER_ON_A_PHONE: boolean = false;

/** Theater's second mode on a phone: the screens at the desk, to drive from the phone. */
export const THEATER_DESK_HREF = '/theater?mode=desk';

/** Where the phone's Theater tab opens. See `THEATER_ON_A_PHONE`. */
export function theaterTabHref(onAPhone: boolean): string {
	return onAPhone ? '/theater' : '/remote';
}

/** Where the Remote's address sends a phone: nowhere while it is the Theater tab's screen. */
export function remoteGoesTo(onAPhone: boolean): string | null {
	return onAPhone ? THEATER_DESK_HREF : null;
}

/** The rule across the rail, a position in the order: ways of looking above, places below. */
export const RAIL_DIVIDER = '--';

// In shipped order. `admin: true` only hides a door the server would refuse.
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
	/* Music still lives at `/songs`, which bookmarks and stored orders name. */
	{ id: 'songs', href: '/songs', label: 'Music', icon: 'music_note_2', phone: 'library' },
	{ id: 'loops', href: '/loops', label: 'Loops', icon: 'all_inclusive', phone: 'library' },
	{ id: 'favorites', href: '/favorites', label: 'Favorites', icon: 'favorite', phone: 'library' },
	/* No `admin`, as for Browse; cut from a phone, which has the Remote. */
	{
		id: 'theater',
		href: '/theater',
		label: 'Theater',
		icon: 'interactive_space',
		phone: THEATER_ON_A_PHONE ? 'tab' : 'cut'
	},
	{
		id: 'organize',
		href: '/organize',
		label: 'Organize',
		icon: 'inbox',
		admin: true,
		phone: 'library'
	},
	/* What is new is said by the status dot cut into the glyph. */
	{
		id: 'downloads',
		href: '/downloads',
		label: 'Downloads',
		icon: 'download',
		admin: true,
		verbs: downloadsVerbs,
		phone: 'tab'
	},
	/* The vault as a place to look; the top bar's control still opens and shuts it. */
	{ id: 'hidden', href: '/hidden', label: 'Hidden', icon: 'visibility_off', phone: 'library' },
	/* No `admin`: an account's figures are its own. */
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
	/* Profile is in Settings; the DESIGN GALLERY (`/design`) is reached by its address. */
];

/** The shipped arrangement, which reset puts back; `nav.test.ts` holds it to `RAIL_NAV`. */
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

/** Rows of the Library screen: Recently viewed first, then the shipped order. */
export const LIBRARY_ROWS: RailItem[] = [
	declared('recent'),
	...DEFAULT_RAIL_ORDER.filter((id) => id !== RAIL_DIVIDER && id !== 'recent')
		.map(declared)
		.filter((item) => item.phone === 'library')
];

// The phone bar is fixed, never the rail's stored order, so a desktop change never reshapes it.

/** A tab of the phone bar, and the addresses it stays lit on beyond its own. */
interface MobileTab extends NavItem {
	/** The screens reached from this tab's own list, which are still "in" it. */
	covers?: readonly string[];
}

/** Tab for the Remote, every signed-in person's; not a rail destination. */
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
