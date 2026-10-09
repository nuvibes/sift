// SPDX-License-Identifier: AGPL-3.0-or-later
/* Finding a setting by typing what you call it. */

import type { SettingEntry, SettingSection } from '$lib/settings-ui/settings';
import { REGISTRY_HOME, SETTINGS_GROUPS, resolveAddress, type SettingsSection } from './sections';
import { crumbsOf } from './settings-path';

/* The second feeder: one import per pane that draws something the registry cannot describe. */
import { SEARCHABLE as music } from './Music.search';
import { SEARCHABLE as getToKnow } from './GetToKnow.search';
import { SEARCHABLE as insights } from './Insights.search';
import { SEARCHABLE as recognitionSwitches } from './RecognitionSection.search';
import { SEARCHABLE as users } from './Users.search';
import { SEARCHABLE as backup } from './Backup.search';
import { SEARCHABLE as editing } from './Editing.search';
import { SEARCHABLE as faces } from './Faces.search';
import { SEARCHABLE as watermarks } from './Watermarks.search';
import { SEARCHABLE as graphicsCard } from './GraphicsCard.search';
import { SEARCHABLE as importingFolders } from './ImportingFolders.search';
import { SEARCHABLE as closingTheWindow } from './ClosingTheWindow.search';
import { SEARCHABLE as general } from './General.search';
import { SEARCHABLE as ledger } from './Ledger.search';
import { SEARCHABLE as linksOpenIn } from './LinksOpenIn.search';
import { SEARCHABLE as logs } from './Logs.search';
import { SEARCHABLE as maintenance } from './Maintenance.search';
import { SEARCHABLE as namingTemplate } from './NamingTemplate.search';
import { SEARCHABLE as networkSharing } from './NetworkSharing.search';
import { SEARCHABLE as playback } from './Playback.search';
import { SEARCHABLE as privacy } from './Privacy.search';
import { SEARCHABLE as sites } from './Sites.search';
import { SEARCHABLE as profile } from './Profile.search';
import { SEARCHABLE as shortcuts } from './Shortcuts.search';
import { SEARCHABLE as stashBoxes } from './StashBoxes.search';
import { SEARCHABLE as storageFolders } from './StorageFolders.search';
import { SEARCHABLE as supportedSites } from './SupportedSites.search';
import { SEARCHABLE as tileMarks } from './TileMarksPicture.search';
import { SEARCHABLE as downloadTools } from './DownloadTools.search';
import { SEARCHABLE as importing } from './Importing.search';
import { SEARCHABLE as performance } from './Performance.search';
import { SEARCHABLE as scheduledTasks } from './ScheduledTasks.search';
import { SEARCHABLE as semantic } from './Semantic.search';
import { SEARCHABLE as updates } from './Updates.search';
import { SEARCHABLE as downloads } from './Downloads.search';
import { SEARCHABLE as appearance } from './Appearance.search';
import { SEARCHABLE as documentation } from './Documentation.search';

/** One thing somebody can look for, and where pressing it should take them. */
export interface Searchable {
	/** What it is called on screen. The first thing matched and the first thing shown. */
	name: string;
	/** The section id it lives in. Half of the address a result opens. */
	section: string;
	/** The tab on that section's screen, for a section drawn as tabs. See `SettingsAddress`. */
	show?: string;
	/** The registry key, where it has one. The other half of the address, and what gets rung. */
	key?: string;
	/** The sentence under it. Searched, and shown under a result that matched BECAUSE of it. */
	help?: string;
	/** Words somebody might type that are in neither the name nor the help. */
	keywords?: string;
	/** The title of the sub-page the row is drawn on, for a row one level in (More settings). */
	page?: string;
}

/** Every section, flattened out of its groups, so a section id can be given its label. */
export function everySection(): SettingsSection[] {
	return SETTINGS_GROUPS.flatMap((group) => group.sections);
}

/** The registry's half of the index. */
export function fromRegistry(sections: SettingSection[]): Searchable[] {
	return sections.flatMap((section) =>
		(section.settings ?? [])
			.filter((entry: SettingEntry) => Boolean(entry.label))
			.map((entry: SettingEntry) => ({
				name: entry.label as string,
				/* The written-down join, not a guess from the name. */
				section: REGISTRY_HOME[section.name] ?? section.name.toLowerCase(),
				key: entry.key,
				help: entry.help,
				// The words on the choices themselves. Somebody looking for "Never" or "Ask me
				// first" is looking for the setting that offers it, and the label rarely says it.
				keywords: (entry.choice_labels ?? []).join(' ') || undefined
			}))
	);
}

/** What matches, best kind of match first. */
export function matching(index: Searchable[], typed: string): Searchable[] {
	const phrase = typed.toLowerCase().split(/\s+/).filter(Boolean).join(' ');
	const byLead: Searchable[] = [];
	const byName: Searchable[] = [];
	const byKeyword: Searchable[] = [];
	const byHelp: Searchable[] = [];
	for (const entry of index) {
		const where = matchedIn(entry, typed);
		if (where === 'name' && entry.name.toLowerCase().startsWith(phrase)) byLead.push(entry);
		else if (where === 'name') byName.push(entry);
		else if (where === 'keyword') byKeyword.push(entry);
		else if (where === 'help') byHelp.push(entry);
	}
	return [...byLead, ...byName, ...byKeyword, ...byHelp];
}

/* Which of the three things a row was found by, or null when it was not found at all. */
type MatchedIn = 'name' | 'keyword' | 'help' | null;

/** WHERE the typed words were found in one entry. */
export function matchedIn(entry: Searchable, typed: string): MatchedIn {
	const words = typed.toLowerCase().split(/\s+/).filter(Boolean);
	if (words.length === 0) return null;

	const name = entry.name.toLowerCase();
	const keywords = (entry.keywords ?? '').toLowerCase();
	const help = (entry.help ?? '').toLowerCase();
	if (!words.every((word) => `${name} ${keywords} ${help}`.includes(word))) return null;
	if (words.every((word) => name.includes(word))) return 'name';
	if (words.every((word) => `${name} ${keywords}`.includes(word))) return 'keyword';
	return 'help';
}

/** Everything a hand-written pane declared about itself. See the import block above. */
export const DECLARED: Searchable[] = [
	...importing,
	...performance,
	...scheduledTasks,
	...semantic,
	...updates,
	...downloads,
	...music,
	...getToKnow,
	...insights,
	...recognitionSwitches,
	...users,
	...backup,
	...editing,
	...faces,
	...watermarks,
	...graphicsCard,
	...importingFolders,
	...closingTheWindow,
	...general,
	...ledger,
	...linksOpenIn,
	...logs,
	...maintenance,
	...namingTemplate,
	...networkSharing,
	...playback,
	...privacy,
	...sites,
	...profile,
	...shortcuts,
	...stashBoxes,
	...storageFolders,
	...supportedSites,
	...tileMarks,
	...downloadTools,
	...appearance,
	...documentation
];

/** The whole index: what the registry declares, plus what the panes declare about themselves. */
export function indexOf(sections: SettingSection[]): Searchable[] {
	return [...fromRegistry(sections), ...DECLARED];
}

/* One section with whatever matched on it: what the results are drawn as. */
interface FoundSection {
	section: SettingsSection;
	/** The settings on it that matched. Possibly EMPTY, and that is a real result rather than a
	 * degenerate one: it is the section whose own name was what matched. */
	entries: Searchable[];
}

/** What matched, gathered under the section each one is on. */
export function grouped(
	index: Searchable[],
	sections: SettingsSection[],
	typed: string
): FoundSection[] {
	const words = typed.toLowerCase().split(/\s+/).filter(Boolean);
	if (words.length === 0) return [];

	/* A settings path (`Settings > Privacy > Auto-lock > ...`) is an address, not words to
	   match: its one result is the place it names. */
	const crumbs = crumbsOf(typed);
	if (crumbs) {
		const landed = landing(index, sections, crumbs);
		return landed ? [{ section: landed.section, entries: landed.entry ? [landed.entry] : [] }] : [];
	}

	const known = new Map(sections.map((one) => [one.id, one]));
	const order: string[] = [];
	const under = new Map<string, Searchable[]>();

	/** Start a group for this section, or say it is not one this caller draws. */
	function start(id: string): boolean {
		if (under.has(id)) return true;
		if (!known.has(id)) return false;
		order.push(id);
		under.set(id, []);
		return true;
	}

	for (const one of sections) {
		if (words.every((word) => one.label.toLowerCase().includes(word))) start(one.id);
	}

	/* Then the settings, in the order `matching` put them in, so a group's position is decided
	   by the best match on it, and nothing here scores anything a second time. */
	/* ONE result for one place. */
	const placed = new Set<string>();
	for (const entry of matching(index, typed)) {
		const address = resolveAddress(entry.section, entry.key, entry.show);
		const lands = address.section;
		const row = address.key === undefined ? undefined : `${lands}#${address.key}`;
		if (row !== undefined && placed.has(row)) continue;
		if (!start(lands)) continue;
		if (row !== undefined) placed.add(row);
		under.get(lands)?.push(entry);
	}

	return order.map((id) => ({
		section: known.get(id) as SettingsSection,
		entries: under.get(id) ?? []
	}));
}

/** Where a settings path lands: its section, and the row it names where the index knows it. */
export function landing(
	index: Searchable[],
	sections: SettingsSection[],
	crumbs: readonly string[]
): { section: SettingsSection; entry?: Searchable } | null {
	const said = (text: string) => text.trim().toLowerCase();
	const section = sections.find((one) => said(one.label) === said(crumbs[0] ?? ''));
	if (!section) return null;
	const here = index.filter(
		(entry) => resolveAddress(entry.section, entry.key, entry.show).section === section.id
	);
	const below = crumbs.slice(1);
	for (const crumb of [...below].reverse()) {
		const entry = here.find((one) => said(one.name) === said(crumb));
		if (entry) return { section, entry };
	}
	const wordsOf = (text: string) => said(text).split(/\s+/).filter(Boolean);
	for (let at = below.length - 1; at >= 0; at--) {
		const words = wordsOf(below[at]);
		const named = here.filter((one) => words.every((word) => said(one.name).includes(word)));
		if (named.length === 0) continue;
		const above = below.slice(0, at).flatMap(wordsOf);
		const told = (one: Searchable) =>
			said([one.name, one.keywords, one.help, one.key, one.page].filter(Boolean).join(' '));
		const chosen = named.find((one) => above.every((word) => told(one).includes(word)));
		return { section, entry: chosen ?? named[0] };
	}
	return { section };
}

/** The first thing a search found, as a section id: what the pane beside the results should show. */
export function firstMatch(groups: FoundSection[]): string | null {
	return groups[0]?.section.id ?? null;
}

/** What Enter opens when the arrows have not moved: the first result, as pressing it would. */
export function firstResult(
	groups: FoundSection[],
	typed: string
): { section: string; key?: string; show?: string; name?: string } | null {
	const group = groups[0];
	if (!group) return null;
	const words = typed.toLowerCase().split(/\s+/).filter(Boolean);
	const byName = words.every((word) => group.section.label.toLowerCase().includes(word));
	const entry = byName ? undefined : group.entries[0];
	if (!entry) return { section: group.section.id };
	return { section: entry.section, key: entry.key, show: entry.show, name: nameToFind(entry) };
}

/** The name a result is looked for by on its pane: only where it has no key to be found by. */
export function nameToFind(entry: Searchable): string | undefined {
	return entry.key ? undefined : entry.name;
}
