/* The settings sections, in the order they are shown, grouped the way they are read. */

import type { IconName } from '$lib/design/icons';

export interface SettingsSection {
	/** The path segment, and the section's identity. Never renamed once shipped: it is an address. */
	id: string;
	label: string;
	icon: IconName;
	/** Rendered only for an admin. See above: a courtesy, not a permission. */
	admin?: boolean;
}

interface SettingsGroup {
	heading: string;
	sections: SettingsSection[];
}

export const SETTINGS_GROUPS: SettingsGroup[] = [
	{
		heading: 'Library',
		sections: [
			/* Admin-only: every route behind it (the folders, the grants, the quarantine) is an
			   admin's. */
			{ id: 'library', label: 'Folders', icon: 'folder', admin: true },
			/* WHEN work runs, what each import stage does and what Sift has done, beside
			   Folders. */
			{ id: 'tasks', label: 'Tasks and Activity', icon: 'calendar_clock', admin: true },
			/* Sift's own noun for the thing, which the vocabulary gate insists on. */
			{ id: 'sites', label: 'Sites and Tunnels', icon: 'public', admin: true },
			{ id: 'downloads', label: 'Downloads', icon: 'download', admin: true },
			/* Compression is here: two doors for two questions asked in the same breath would be
			   one too many. */
			{ id: 'editing', label: 'Editing', icon: 'content_cut', admin: true },
			/* Theater is a group on this pane, not a section: "how Theater starts" is about
			   playing things. */
			{ id: 'playback', label: 'Playback', icon: 'video_settings' }
		]
	},
	{
		/* Yours rather than the install's, most opened first. */
		heading: 'Personal',
		sections: [
			/* No `admin`: every signed-in user has a name, a password and a session. */
			{ id: 'profile', label: 'Profile', icon: 'account_circle' },
			{ id: 'appearance', label: 'Appearance', icon: 'palette' },
			/* Learning the app is about the person doing it, so it sits beside who they are. */
			{ id: 'get-to-know', label: 'Get to know Sift', icon: 'checklist' },
			/* Which recaps Sift creates for this person. No `admin`: everybody has Insights. */
			{ id: 'insights', label: 'Insights', icon: 'insights' },
			{ id: 'privacy', label: 'Privacy and Security', icon: 'shield_person' },
			/* The people who may SIGN IN: the word "account" is off the screen entirely. */
			{ id: 'users', label: 'User Management', icon: 'supervised_user_circle', admin: true },
			/* Not a preference, and here anyway: a list of what the keyboard does is looked UP
			   in Settings. */
			{ id: 'shortcuts', label: 'Shortcuts', icon: 'keyboard' }
		]
	},
	{
		heading: 'Recognition',
		sections: [
			/* Faces, as the Organize tab calls the same feature: one name for one thing. */
			{ id: 'faces', label: 'Faces', icon: 'person', admin: true },
			{ id: 'semantic', label: 'Smart Search', icon: 'auto_awesome', admin: true },
			/* `language` is what a stash-box is already drawn as. */
			{ id: 'stash-boxes', label: 'Stash-boxes', icon: 'language', admin: true },
			/* Which song a file uses: the fingerprints and the AcoustID lookup, one feature with
			   one door. */
			{ id: 'music', label: 'Music', icon: 'music_note_2', admin: true },
			/* Its label is the SECTION name the server registers its settings into, lower-cased:
			   the two agree. */
			{ id: 'watermarks', label: 'Watermarks', icon: 'position_bottom_right', admin: true }
		]
	},
	{
		/* The machine and the install: the whole app first, then the device, then what is set
		   once. */
		heading: 'System',
		sections: [
			/* What the application does on THIS device and the choices made about the whole app. */
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

/** Where an address lands: a section, and optionally the TAB on it and the ROW in it. */
export interface SettingsAddress {
	section: string;
	show?: string;
	/** The row to ring: a registry key, or the id a hand-written control carries. */
	key?: string;
}

/** Addresses that do not name a section any more, and what answers their question now. */
const MOVED_TO: Readonly<Record<string, Omit<SettingsAddress, 'key'>>> = {
	/* `connections` held the logins, the tunnels and the routing, and those are Sites. */
	connections: { section: 'sites' },
	/* `platforms` is the older word for Sites. */
	platforms: { section: 'sites' },
	compression: { section: 'editing' },
	accounts: { section: 'users' },
	/* Theater is a group of its own on Playback; the rows are the same settings. */
	theater: { section: 'playback' },
	/* The sections Tasks and Activity joined land on their own tab. */
	schedule: { section: 'tasks' },
	jobs: { section: 'tasks', show: 'now' },
	/* Importing's stages are Import tasks on the Tasks tab, each beside its Run now. */
	importing: { section: 'tasks' },
	ledger: { section: 'tasks', show: 'history' },
	logs: { section: 'tasks', show: 'log' },
	about: { section: 'updates' }
};

/** ROWS that moved, by the key they were addressed by, and where each one is drawn now. */
const KEY_MOVED_TO: Readonly<Record<string, SettingsAddress & { key: string }>> = {
	'appearance.links_open_in': { section: 'general', key: 'general.links_open_in' },
	/* The stored word followed the on-screen one: GIF, as every screen says. */
	'edit.animation_format': { section: 'editing', key: 'edit.gif_format' },
	'appearance.closing_the_window': { section: 'general', key: 'general.closing_the_window' },
	/* Registered under Downloads and drawn on the Logs tab, because it is about the log. */
	'download.verbose': { section: 'tasks', show: 'log', key: 'download.verbose' },
	/* "How long tasks take" left Performance for App History, narrowed to the runs. */
	'performance.scan_history': { section: 'tasks', show: 'history', key: 'activity.runs' },
	'downloads.supported': { section: 'sites', key: 'sites.supported' },
	/* Drawn beside the tunnels, where the Swap tunnels block says which one a join goes through. */
	'swap.guest_tunnel': { section: 'sites', key: 'sites.swap_join' },
	'downloads.tools': { section: 'updates', key: 'updates.download_tools' },
	'downloads.tools.yt-dlp': { section: 'updates', key: 'updates.download_tools.yt-dlp' },
	/* Quiet hours are the one clock and every arrival switch is its task's When. */
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
	/* The update check's When is drawn nowhere, so a link to it lands on its switch on Updates. */
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
	'importing.quiet-hours': { section: 'schedule', key: 'tasks.quiet-hours' },
	'performance.generate_fingerprints': {
		section: 'tasks',
		key: 'performance.generate_fingerprints'
	},
	'quarantine.keep_days': { section: 'maintenance', key: 'quarantine.keep_days' },
	'tasks.quarantine-prune.when': { section: 'maintenance', key: 'maintenance.quarantine' },
	'tasks.search-records-prune.when': { section: 'privacy', key: 'privacy.search_history' },
	'importing.identify': { section: 'schedule', key: 'tasks.identify.when' },
	'importing.scan-now': { section: 'tasks', key: 'tasks.scan.when' },
	'importing.generate-now': { section: 'tasks', key: 'tasks.generate.when' },
	'importing.identify-now': { section: 'tasks', key: 'tasks.identify.when' },
	'importing.scan-stage': { section: 'tasks', key: 'tasks.scan.when' },
	'importing.generate-stage': { section: 'tasks', key: 'tasks.generate.when' },
	'importing.identify-stage': { section: 'tasks', key: 'tasks.identify.when' },
	/* Linked to as a Performance row, so the "Turn it on" under a file that stutters rings it. */
	'performance.repair_playback': { section: 'tasks', key: 'performance.repair_playback' },
	/* Drawn on Concurrency's page on Performance, so an old link naming Faces or Importing lands
	   there. */
	'faces.machine_budget': { section: 'performance', key: 'faces.machine_budget' },
	'faces.core_share': { section: 'performance', key: 'faces.core_share' },
	'faces.thread_count': { section: 'performance', key: 'faces.thread_count' },
	/* The two confirmations are about the whole app rather than about editing, so General. */
	'editing.delete.ask': { section: 'general', key: 'editing.delete.ask' },
	'editing.remove.ask': { section: 'general', key: 'editing.remove.ask' },
	/* Who can reach the library over the network, and where the library is: General too. */
	'privacy.network_sharing': { section: 'general', key: 'privacy.network_sharing' },
	'music.lookup': { section: 'music', key: 'music.lookup' },
	'music.lookup_route': { section: 'music', key: 'music.lookup_route' },
	'stash-boxes.music': { section: 'music', key: 'music.acoustid' },
	'stash-boxes.music-key': { section: 'music', key: 'music.acoustid-key' },
	/* The press for the files already fingerprinted is the lookup task's Run now on Tasks. */
	'stash-boxes.music-owed': { section: 'tasks', key: 'tasks.music-lookup.when' },
	'music.acoustid-owed': { section: 'tasks', key: 'tasks.music-lookup.when' },
	'stash-boxes.music-test': { section: 'music', key: 'music.acoustid-test' },
	/* About's one searchable line, then Updates' About block: both are the version at its top. */
	/* The device id a swap tells another Sift is drawn on Updates and Info. */
	'privacy.swaps': { section: 'updates', key: 'updates.device_id' },
	'about.sift': { section: 'updates', key: 'updates.version' },
	'updates.about': { section: 'updates', key: 'updates.version' }
};

/** THE ONE RESOLVER: any address a person can arrive on, turned into the place that answers it. */
export function resolveAddress(section: string, key?: string, show?: string): SettingsAddress {
	const row = key === undefined ? undefined : KEY_MOVED_TO[key];
	if (row) {
		/* A row may be sent to an ADDRESS that is itself redirected, so its section goes through
		   the map. */
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

/** Which section draws the settings the SERVER files under each of its section names. */
export const REGISTRY_HOME: Readonly<Record<string, string>> = {
	Library: 'library',
	/* An address, as `Logs` is: the import stages' pages are on the Tasks tab. */
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
	/* An address rather than a drawn section: these are drawn on the Logs TAB of Tasks and
	   Activity. */
	Logs: 'logs',
	'Privacy and Security': 'privacy',
	Insights: 'insights',
	Appearance: 'appearance',
	Backup: 'backup',
	Updates: 'updates',
	/* An address rather than a drawn section: About is the foot of Updates. */
	About: 'about'
};

export const SETTINGS_SECTIONS: SettingsSection[] = SETTINGS_GROUPS.flatMap(
	(group) => group.sections
);

/** Where `/settings` with no section lands, for an admin: the first section on the list. */
export const DEFAULT_SECTION = SETTINGS_SECTIONS[0].id;

/** Where `/settings` with no section lands for this account: the first section on ITS list. */
export function firstSectionFor(admin: boolean): string {
	return SETTINGS_SECTIONS.find((one) => admin || !one.admin)?.id ?? DEFAULT_SECTION;
}

export function isKnownSection(id: string): boolean {
	return SETTINGS_SECTIONS.some((section) => section.id === id) || id in MOVED_TO;
}

/** The id that actually renders, for any address a person can arrive on. */
export function settledSection(id: string): string {
	return resolveAddress(id).section;
}

/** The declaration behind any address a person can arrive on, after a retired id is followed. */
export function sectionFor(id: string): SettingsSection | undefined {
	const settled = settledSection(id);
	return SETTINGS_SECTIONS.find((section) => section.id === settled);
}

export function labelFor(id: string): string {
	return sectionFor(id)?.label ?? 'Settings';
}
