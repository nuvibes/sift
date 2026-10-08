/* The settings sections, in the order they are shown, grouped the way they are read.
 *
 * One list, so the section list on the left and the router that answers `/settings/{section}`
 * cannot disagree about what settings contains. A section here that nothing renders yet is a
 * deliberate placeholder rather than a dead link: it says what is coming and keeps the addresses
 * stable while it does.
 *
 * `admin` leaves a section out for a guest. It is not what stops them reaching it: every
 * endpoint behind these refuses on its own, and that refusal is the control. This is about not
 * offering somebody a door that will not open.
 *
 * ## Ids never change; labels and grouping do
 *
 * An id is an address. `/settings/faces` opens what it always opened, and a link somebody saved
 * keeps working, whatever the entry is called on screen. That is why several ids below do not
 * match their labels, and it is deliberate every time:
 *
 *   `library`   is labelled Folders: a section cannot share its group's name
 *   `faces`     is labelled Faces: its older label was Identify, and the address never moved
 *   `semantic`  is labelled Smart Search: "semantic" is a word for the technique, not the thing
 *   `tasks`     is labelled Tasks and Activity: the Tasks, App History and Logs tabs; the
 *               sections it joined, `schedule`, `jobs` and `importing`, land on its tabs
 *   `updates`   is labelled Updates and Info and holds what `about` held: that id lands here
 *
 * When a section genuinely stops existing, its id does not become a dead address: it goes in
 * `MOVED_TO` below and the router settles it onto whatever answers that question now.
 *
 * ## Groups are headings, and headings are not items
 *
 * A group heading is small, uppercase, letter-spaced, muted, and carries no icon. An item is body
 * size, full ink, and carries one. The contrast between them is the whole of what makes a heading
 * unmistakable for something to click, and it is enforced in the sidebar's own stylesheet.
 *
 * A group heading is a NOUN, in the reader's words. Not a question, not a clause, and never a
 * description of what the machine is doing: "What Sift works out" fails on all three together.
 * Appearance, Privacy and Performance are the shape: each names a thing a person looks for.
 *
 * ## The order inside a group
 *
 * By how often a section is likely to be opened, most often first, never alphabetical: the
 * list is read top down, and the section somebody wants most is the one they should meet first.
 */

import type { IconName } from '$lib/design/icons';

export interface SettingsSection {
	/** The path segment, and the section's identity. Never renamed once shipped: it is an address. */
	id: string;
	label: string;
	/** Shown to the left of the label. Every item has one; no heading does. */
	icon: IconName;
	/** Rendered only for an admin. See above: a courtesy, not a permission. */
	admin?: boolean;
}

interface SettingsGroup {
	/** Shown above the group, in the heading style that cannot be mistaken for an item. */
	heading: string;
	sections: SettingsSection[];
}

export const SETTINGS_GROUPS: SettingsGroup[] = [
	{
		/* Where the files are and what is done to them on the way in, most opened first. */
		heading: 'Library',
		sections: [
			/* Admin-only: every route behind it (the folders, the grants, the quarantine) is an
			   admin's, and a guest's folders are the ones shared with them, which Browse shows. */
			{ id: 'library', label: 'Folders', icon: 'folder', admin: true },
			/* WHEN work runs, what each import stage does and what Sift has done: one door for
			   "what does Sift do with my library, and when", beside Folders. See `MOVED_TO`. */
			{ id: 'tasks', label: 'Tasks and Activity', icon: 'calendar_clock', admin: true },
			/* Sift's own noun for the thing, and the vocabulary gate insists on it:
			   `test_one_word_per_thing.py` maps `platform` to Site and records why.

			   Two older ids name this section, and both are in `MOVED_TO` below: `connections`
			   and `platforms`, the older word for a Site. An id is an address, so neither old one
			   is a dead link. */
			{ id: 'sites', label: 'Sites and Tunnels', icon: 'public', admin: true },
			{ id: 'downloads', label: 'Downloads', icon: 'download', admin: true },
			/* Compression is here: one setting behind one door and four behind another would be
			   two doors for two questions a person asks in the same breath. Both keep a heading. */
			{ id: 'editing', label: 'Editing', icon: 'content_cut', admin: true },
			/* Theater is a group of its own on this pane, not a section: a person looking for
			   "how Theater starts" reaches for the pane about playing things, and one door per
			   question is the rule the whole list keeps. An address naming Theater lands here.
			   See `MOVED_TO`. */
			{ id: 'playback', label: 'Playback', icon: 'video_settings' }
		]
	},
	{
		/* Yours rather than the install's, most opened first: who you are, how it looks to you,
		   what you keep hidden, and who else may sign in. */
		heading: 'Personal',
		sections: [
			/* No `admin`: every signed-in user has a name, a password and a session, so a guest
			   gets this where they do not get Users. See `Profile.svelte` for why it is a section
			   rather than a rail destination. */
			{ id: 'profile', label: 'Profile', icon: 'account_circle' },
			{ id: 'appearance', label: 'Appearance', icon: 'palette' },
			/* The steps, the quests, the streak and the achievements: learning the app is about
			   the person doing it, so it sits beside who they are. No `admin`: everybody learns. */
			{ id: 'get-to-know', label: 'Get to know Sift', icon: 'checklist' },
			{ id: 'privacy', label: 'Privacy and Security', icon: 'shield_person' },
			/* The people who may SIGN IN, as `users` and `Users.svelte`: the word "account" is
			   off the screen entirely, because a name on a site is a "username" and two words
			   for two things is the whole point. The older address still lands here. See
			   `MOVED_TO`. */
			{ id: 'users', label: 'User Management', icon: 'supervised_user_circle', admin: true },
			/* Not a preference (nothing on it can be changed), and here anyway, because a list of
			   what the keyboard does is a thing people look UP, and Settings is where they look.
			   No `admin`: the keys are everybody's. */
			{ id: 'shortcuts', label: 'Shortcuts', icon: 'keyboard' }
		]
	},
	{
		/* The features that work out what a file is, rather than being told, most opened first.

		   Named Recognition rather than Intelligence, and the contents decide that rather than
		   taste: a stash-box look-up is a database query against a fingerprint, and
		   calling that intelligence would be the group heading telling a small lie.

		   All admin-only: what they decide is a capability the whole install either has or has
		   not, rather than a preference about how one person browses. */
		heading: 'Recognition',
		sections: [
			/* Faces, as the Organize tab calls the same feature: one name for one thing. "Identify"
			   stays the name of the import STAGE, which is the work rather than the feature. */
			{ id: 'faces', label: 'Faces', icon: 'person', admin: true },
			{ id: 'semantic', label: 'Smart Search', icon: 'auto_awesome', admin: true },
			/* `language` is what a stash-box is already drawn as: the look-up button on a tag
			   page carries it. One icon per thing, the same rule as one word per thing. */
			{ id: 'stash-boxes', label: 'Stash-boxes', icon: 'language', admin: true },
			/* Which song a file uses: the fingerprints and the AcoustID lookup, one feature with
			   one door. */
			{ id: 'music', label: 'Music', icon: 'music_note_2', admin: true },
			/* Its label is the SECTION name the server registers its settings into, lower-cased:
			   the two have to agree or the settings this pane draws would arrive under a heading
			   nothing renders. Last, because it is set up once and rarely opened again. */
			{ id: 'watermarks', label: 'Watermarks', icon: 'position_bottom_right', admin: true }
		]
	},
	{
		/* The machine and the install: the choices about the whole app first, then how much of
		   the device it uses, then what is set once and left. */
		heading: 'System',
		sections: [
			/* What the application does on THIS device and the choices made about the whole app:
			   which browser a link opens in, the close button, starting with Windows, the
			   confirmations, network sharing and where the library is. Offered to everybody: a
			   row that is an admin's decision says so and draws only for an admin. `apps`
			   because the gear is Settings itself, and one icon per thing is the rule. */
			{ id: 'general', label: 'General', icon: 'apps' },
			{ id: 'performance', label: 'Performance', icon: 'readiness_score', admin: true },
			{ id: 'maintenance', label: 'Maintenance', icon: 'mop', admin: true },
			{ id: 'backup', label: 'Backup and restore', icon: 'settings_backup_restore', admin: true },
			/* Everybody's: the AGPL keeps the licence and notices at its foot reachable. */
			{ id: 'updates', label: 'Updates and Info', icon: 'upgrade' },
			{ id: 'documentation', label: 'Documentation', icon: 'menu_book' }
		]
	}
];

/**
 * Where an address lands: a section, and optionally the TAB on it and the ROW in it.
 *
 * `show` is the tab, carried in the address as `?show=`: the query the entity pages already use for
 * theirs (`/people/{id}?show=history`), so a tab is a place with an address in Settings too, and Back
 * steps between tabs the way it does there.
 */
export interface SettingsAddress {
	section: string;
	/** The tab on that section's screen, where it has tabs. */
	show?: string;
	/** The row to ring: a registry key, or the id a hand-written control carries. */
	key?: string;
}

/**
 * Addresses that do not name a section any more, and what answers their question now.
 *
 * A retired id is not a dead link. Somebody bookmarked `/settings/connections`, a sentence
 * elsewhere in the app links to it, and a deep link into one of its settings carries the old
 * section in the path: all three have to keep landing somewhere sensible rather than on a
 * placeholder that says the section does not exist.
 *
 * It maps to the section that inherited the WORK, not to the top of Settings. Sending somebody who
 * asked for their Site logins to a default pane is the same failure as a 404 with better manners.
 */
const MOVED_TO: Readonly<Record<string, Omit<SettingsAddress, 'key'>>> = {
	/* `connections` held the logins, the tunnels and the routing (what nearly everybody was
	   there for), and those are Sites. Stash-boxes, network sharing and the browser choice are
	   sections or rows of their own.

	   Straight to `sites` rather than to `platforms`, and that is not a shortcut: this map is
	   read ONCE, so a chain of two retired ids would land somebody on a section that does not
	   exist. Every entry here names a section that is drawn today. */
	connections: { section: 'sites' },
	/* `platforms` is the older word for Sites. The word changed; the address somebody saved did
	   not. */
	platforms: { section: 'sites' },
	compression: { section: 'editing' },
	/* `accounts` is the older id of the sign-ins section. A bookmark, a link in an old message
	   or a deep link into one of its settings still holds the old word. */
	accounts: { section: 'users' },
	/* Theater is a group of its own on Playback. A deep link keeps its key: the rows are the
	   same settings, drawn on the pane that inherited them. */
	theater: { section: 'playback' },
	/* The sections Tasks and Activity joined land on their own tab, which answers what the old
	   address asked. `jobs` always opened on the queue. */
	schedule: { section: 'tasks' },
	jobs: { section: 'tasks', show: 'now' },
	/* Importing's stages are Import tasks on the Tasks tab, each beside its Run now. */
	importing: { section: 'tasks' },
	/* The two record-keeping sections are tabs of it too: App History and Logs. */
	ledger: { section: 'tasks', show: 'history' },
	logs: { section: 'tasks', show: 'log' },
	/* About is the foot of Updates: the version, the licence and the notices. */
	about: { section: 'updates' }
};

/**
 * ROWS that moved, by the key they were addressed by, and where each one is drawn now.
 *
 * A section redirect cannot carry these: the row left a section that still EXISTS, so the old
 * address names a live pane that simply does not draw it. Without this,
 * `/settings/appearance#appearance.closing_the_window` opens Appearance and rings nothing, which
 * is exactly the silent dead end a deep link is not allowed to be.
 *
 * A row whose setting was RETIRED outright is not here: there is nowhere to send it, and landing on
 * the section it was on is the honest answer (`appearance.show_recently_viewed`, whose strip was on
 * no screen).
 */
const KEY_MOVED_TO: Readonly<Record<string, SettingsAddress & { key: string }>> = {
	/* To General, with the rest of what the application does on this device. */
	'appearance.links_open_in': { section: 'general', key: 'general.links_open_in' },
	/* The stored word followed the on-screen one: GIF, as every screen says. */
	'edit.animation_format': { section: 'editing', key: 'edit.gif_format' },
	'appearance.closing_the_window': { section: 'general', key: 'general.closing_the_window' },
	/* Registered under Downloads and drawn on the Logs tab, because it is about the log. */
	'download.verbose': { section: 'tasks', show: 'log', key: 'download.verbose' },
	/* "How long tasks take" left Performance for App History, narrowed to the runs. */
	'performance.scan_history': { section: 'tasks', show: 'history', key: 'activity.runs' },
	/* Out of Settings > Downloads: the pane keeps what is set once about downloading. */
	'downloads.supported': { section: 'sites', key: 'sites.supported' },
	/* Drawn beside the tunnels, where the Swap tunnels block says which one a join goes through;
	   it is chosen on the swap screens. */
	'swap.guest_tunnel': { section: 'sites', key: 'sites.swap_join' },
	'downloads.tools': { section: 'updates', key: 'updates.download_tools' },
	'downloads.tools.yt-dlp': { section: 'updates', key: 'updates.download_tools.yt-dlp' },
	/* The Tasks model: quiet hours are the one clock and every arrival switch is its task's
	   When, so an old link to any of them lands on the task's row. */
	'faces.nightly_from': { section: 'tasks', key: 'tasks.quiet_from' },
	'faces.nightly_until': { section: 'tasks', key: 'tasks.quiet_until' },
	'importing.scan': { section: 'tasks', key: 'tasks.scan.when' },
	'importing.generate': { section: 'tasks', key: 'tasks.generate.when' },
	'performance.scan_faces_on_import': { section: 'tasks', key: 'tasks.faces.when' },
	'semantic.describe_on_import': { section: 'tasks', key: 'tasks.smart-search.when' },
	'watermarks.read_on_import': { section: 'tasks', key: 'tasks.watermarks.when' },
	'music.fingerprint': { section: 'tasks', key: 'tasks.music.when' },
	'dedup.scan': { section: 'tasks', key: 'tasks.duplicates.when' },
	'suggestions.scan': { section: 'tasks', key: 'tasks.suggestions.when' },
	'stash_boxes.ask_new_files': { section: 'tasks', key: 'tasks.enrichment.when' },
	/* The update check is upkeep nobody times: its When is drawn nowhere, and its one switch on
	   Updates reads and writes it, so a link to the When lands on the switch. */
	'tasks.update-check.when': { section: 'updates', key: 'updates.check_for_new_versions' },
	/* Rows that moved onto an import stage's page, and the quarantine rows onto Maintenance. */
	'shoots.auto_file': { section: 'tasks', key: 'shoots.auto_file' },
	/* How much Sift does at the same time is Concurrency's page on Performance. */
	'performance.generation_limit': { section: 'performance', key: 'performance.generation_limit' },
	'performance.scan_limit': { section: 'performance', key: 'performance.scan_limit' },
	'performance.share_reads_at_once': {
		section: 'performance',
		key: 'performance.share_reads_at_once'
	},
	'importing.at-once': { section: 'performance', key: 'performance.concurrency' },
	'importing.limits': { section: 'performance', key: 'performance.concurrency' },
	/* Quiet hours and every When are chosen on Tasks alone. */
	'importing.quiet-hours': { section: 'schedule', key: 'tasks.quiet-hours' },
	'performance.generate_fingerprints': {
		section: 'tasks',
		key: 'performance.generate_fingerprints'
	},
	'quarantine.keep_days': { section: 'maintenance', key: 'quarantine.keep_days' },
	'tasks.quarantine-prune.when': { section: 'maintenance', key: 'maintenance.quarantine' },
	'tasks.search-records-prune.when': { section: 'privacy', key: 'privacy.search_history' },
	'importing.identify': { section: 'schedule', key: 'tasks.identify.when' },
	/* Each import stage is its task's row on Tasks. */
	'importing.scan-now': { section: 'tasks', key: 'tasks.scan.when' },
	'importing.generate-now': { section: 'tasks', key: 'tasks.generate.when' },
	'importing.identify-now': { section: 'tasks', key: 'tasks.identify.when' },
	'importing.scan-stage': { section: 'tasks', key: 'tasks.scan.when' },
	'importing.generate-stage': { section: 'tasks', key: 'tasks.generate.when' },
	'importing.identify-stage': { section: 'tasks', key: 'tasks.identify.when' },
	/* Registered into Importing and linked to as a Performance row: the "Turn it on" under a
	   file that stutters would open Performance and ring nothing. Every old link and bookmark
	   follows it here; the resolution gate holds that. */
	'performance.repair_playback': { section: 'tasks', key: 'performance.repair_playback' },
	/* How much of this device face recognition may use: drawn on Concurrency's page on Performance,
	   so an old link naming Faces or Importing lands on the row. */
	'faces.machine_budget': { section: 'performance', key: 'faces.machine_budget' },
	'faces.core_share': { section: 'performance', key: 'faces.core_share' },
	'faces.thread_count': { section: 'performance', key: 'faces.thread_count' },
	/* The two confirmations are about the whole app rather than about editing, so General. The
	   rows keep their ids. */
	'editing.delete.ask': { section: 'general', key: 'editing.delete.ask' },
	'editing.remove.ask': { section: 'general', key: 'editing.remove.ask' },
	/* Who can reach the library over the network, and where the library is: General too. */
	'privacy.network_sharing': { section: 'general', key: 'privacy.network_sharing' },
	/* Music has its own section. The switch and the route are registry keys and follow their
	   section by themselves; these are an old link naming them on Stash-boxes, and the block's
	   own rows, whose ids changed with the section. */
	'music.lookup': { section: 'music', key: 'music.lookup' },
	'music.lookup_route': { section: 'music', key: 'music.lookup_route' },
	'stash-boxes.music': { section: 'music', key: 'music.acoustid' },
	'stash-boxes.music-key': { section: 'music', key: 'music.acoustid-key' },
	/* The press that asked about the files already fingerprinted is the lookup task's Run now:
	   an old link to it lands on that task's row on Tasks. */
	'stash-boxes.music-owed': { section: 'tasks', key: 'tasks.music-lookup.when' },
	'music.acoustid-owed': { section: 'tasks', key: 'tasks.music-lookup.when' },
	'stash-boxes.music-test': { section: 'music', key: 'music.acoustid-test' },
	/* About's one searchable line, then Updates' About block: both are the version at its top. */
	/* The device id a swap tells another Sift: an old link naming it on Privacy lands on Updates
	   and Info, where it is drawn. */
	'privacy.swaps': { section: 'updates', key: 'updates.device_id' },
	'about.sift': { section: 'updates', key: 'updates.version' },
	'updates.about': { section: 'updates', key: 'updates.version' }
};

/**
 * THE ONE RESOLVER: any address a person can arrive on, turned into the place that answers it.
 *
 * Every door goes through it (a `SettingLink`, a search result, a pasted or bookmarked address, a
 * refresh), so a moved section, a moved row and a section that became a tab are followed in ONE
 * place rather than once per door. A row that moved decides first, because it names the thing
 * somebody was sent to; then the section; a tab asked for explicitly is kept over one a redirect
 * implies.
 */
export function resolveAddress(section: string, key?: string, show?: string): SettingsAddress {
	const row = key === undefined ? undefined : KEY_MOVED_TO[key];
	if (row) {
		/* A row may be sent to an ADDRESS that is itself redirected (`logs` is the Logs tab),
		   so its section goes through the same map as any other, still read once. */
		const home = MOVED_TO[row.section];
		const landed: SettingsAddress = { section: home?.section ?? row.section, key: row.key };
		const tab = row.show ?? home?.show;
		if (tab !== undefined) landed.show = tab;
		return landed;
	}
	const moved = MOVED_TO[section];
	const landed: SettingsAddress = { section: moved?.section ?? section };
	const tab = show ?? moved?.show;
	if (tab !== undefined) landed.show = tab;
	if (key !== undefined) landed.key = key;
	return landed;
}

/** The path an address is written as: `/settings/{section}?show={tab}#{key}`. */
export function settingsPath(address: SettingsAddress): string {
	const tab = address.show ? `?show=${encodeURIComponent(address.show)}` : '';
	const row = address.key ? `#${address.key}` : '';
	return `/settings/${address.section}${tab}${row}`;
}

/**
 * Which section draws the settings the SERVER files under each of its section names.
 *
 * Joining the two lists by NAME (the server's section lower-cased against a label here) breaks
 * silently the first time either side renames one, and cannot express a server section drawn on a
 * pane of another name at all (Theater's settings on Playback, Identify's on Faces, Logs' on
 * the Logs tab of Tasks and Activity). So the join is written down, once, and
 * `test_settings_sections_agree_across_the_wire.py` holds every server section to an entry here
 * that names an address `resolveAddress` can land.
 */
export const REGISTRY_HOME: Readonly<Record<string, string>> = {
	Library: 'library',
	/* An address, as `Logs` is: the import stages' pages are on the Tasks tab, and `MOVED_TO`
	   knows that. */
	Importing: 'importing',
	Editing: 'editing',
	Downloads: 'downloads',
	'Sites and Tunnels': 'sites',
	Playback: 'playback',
	Theater: 'playback',
	Identify: 'faces',
	'Smart Search': 'semantic',
	Watermarks: 'watermarks',
	'Stash-boxes': 'stash-boxes',
	Music: 'music',
	Performance: 'performance',
	'Scheduled tasks': 'tasks',
	Maintenance: 'maintenance',
	/* An address rather than a drawn section, and deliberately: these are drawn on the Logs TAB of
	   Tasks and Activity, and the tab is what `MOVED_TO` knows. Every reader sends this through
	   `resolveAddress`, so naming the old address here is how a result lands on the tab rather than
	   on Tasks. */
	Logs: 'logs',
	'Privacy and Security': 'privacy',
	Appearance: 'appearance',
	Backup: 'backup',
	Updates: 'updates',
	/* An address rather than a drawn section, for the reason `Logs` is one: About is the foot of
	   Updates, and `MOVED_TO` is what knows that. */
	About: 'about'
};

/** Every section, flattened. The router's list of what is a real address. */
export const SETTINGS_SECTIONS: SettingsSection[] = SETTINGS_GROUPS.flatMap(
	(group) => group.sections
);

/** Where `/settings` with no section lands, for an admin: the first section on the list. */
export const DEFAULT_SECTION = SETTINGS_SECTIONS[0].id;

/**
 * Where `/settings` with no section lands for this account: the first section on ITS list.
 *
 * The first section is an admin's, so a guest sent to `DEFAULT_SECTION` would land on a door that
 * does not open. Every place that opens Settings without naming a section asks this instead.
 */
export function firstSectionFor(admin: boolean): string {
	return SETTINGS_SECTIONS.find((one) => admin || !one.admin)?.id ?? DEFAULT_SECTION;
}

export function isKnownSection(id: string): boolean {
	return SETTINGS_SECTIONS.some((section) => section.id === id) || id in MOVED_TO;
}

/**
 * The id that actually renders, for any address a person can arrive on.
 *
 * Called by everything that turns a path segment into a pane, so a moved section is followed in
 * one place rather than in the page, the modal and the deep-link helper separately.
 */
export function settledSection(id: string): string {
	return resolveAddress(id).section;
}

/**
 * The declaration behind any address a person can arrive on, after a retired id is followed.
 *
 * What the frame titles a section from (its name AND its icon), so the title above a pane and
 * the lit row in the list are drawn from one entry and cannot disagree.
 */
export function sectionFor(id: string): SettingsSection | undefined {
	const settled = settledSection(id);
	return SETTINGS_SECTIONS.find((section) => section.id === settled);
}

/** The name of a section, for a page that has to title itself. */
export function labelFor(id: string): string {
	return sectionFor(id)?.label ?? 'Settings';
}
