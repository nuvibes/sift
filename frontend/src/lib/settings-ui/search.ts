// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Finding a setting by typing what you call it.
 *
 * ## Why an index rather than filtering the screen
 *
 * A settings pane is only in the DOM while you are looking at it, and there are twenty of them. A
 * search that filtered what is drawn could only ever find the section you are already in, which is
 * the one section you did not need help reaching.
 *
 * ## The two feeders, and why there are exactly two
 *
 * **The registry feeds itself.** Every setting already carries its label, help, disclosure and
 * choice labels, and that is the same declaration the ROW is drawn from, so a setting whose words
 * changed changed the index in the same edit. That half cannot go stale.
 *
 * **A hand-written pane declares its own.** Fourteen panes draw controls with no registry key at
 * all (accounts, stash boxes, tunnels, Sites, the graphics card, the naming template, storage
 * folders), and six more mix hand-written controls into registry-driven sections. Those declare
 * `SEARCHABLE` in their own module script, BESIDE the control, so that moving or deleting the
 * control puts the declaration under the same edit. A central list of them somewhere else is a
 * thing you have to remember to open, and the failure is silent: the search simply never finds it.
 *
 * `test_every_settings_control_can_be_found.py` is what stops the second feeder rotting. It reads
 * what it can and says plainly what it cannot. See the gate.
 *
 * ## What it does NOT do
 *
 * It does not rank cleverly: a scored ranking can lose exact matches. Here a name match beats a
 * keyword match beats a sentence match, in that order, and within an order the app's own section order decides. Nothing is scored. Among the
 * name matches, a name that BEGINS with what was typed comes first: "about" is looking for About,
 * not for a setting that says the word halfway through its name.
 */

import type { SettingEntry, SettingSection } from '$lib/settings-ui/settings';
import { REGISTRY_HOME, SETTINGS_GROUPS, resolveAddress, type SettingsSection } from './sections';
import { crumbsOf } from './settings-path';

/* The second feeder: one import per pane that draws something the registry cannot describe.
 *
 * This list is unavoidable (a browser cannot look around a folder), but it is the only part
 * that lives here. The CONTENT is beside each pane, in `<Pane>.search.ts`, so a control that moves
 * or goes takes its entry with it in the same edit. What this list can still get wrong is being
 * one line short, and that is what the gate reads.
 *
 * A `.ts` sibling rather than a `<script module>` block in the component itself, and the reason is
 * measurable rather than stylistic: importing from the `.svelte` files would pull all twenty panes
 * into the bundle wherever search is used, and the panes are drawn one at a time on purpose. */
import { SEARCHABLE as music } from './Music.search';
import { SEARCHABLE as getToKnow } from './GetToKnow.search';
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

/** One thing somebody can look for, and where pressing it should take them. */
export interface Searchable {
	/** What it is called on screen. The first thing matched and the first thing shown. */
	name: string;
	/**
	 * The section id it lives in. Half of the address a result opens.
	 *
	 * An ADDRESS, so a retired id is allowed and is followed: every result opens through
	 * `resolveAddress`, and a declaration naming a section that has since moved still lands on the
	 * pane that inherited it rather than dropping out of the search.
	 */
	section: string;
	/** The tab on that section's screen, for a section drawn as tabs. See `SettingsAddress`. */
	show?: string;
	/**
	 * The registry key, where it has one. The other half of the address, and what gets rung.
	 *
	 * Absent for a thing drawn by hand (the heading over a group, a card): the result opens its
	 * section and looks for the NAME where the pane writes names (`revealNamed`), opening the fold
	 * or the sub-page it is behind. A name the pane is not drawing leaves the section at its top.
	 */
	key?: string;
	/**
	 * The sentence under it. Searched, and shown under a result that matched BECAUSE of it.
	 *
	 * Shown only then, and that is the whole of the rule. The results column is 220px, and a help
	 * line under every row would double their height for no answer: a row matched on its name has
	 * the letters marked in the name and needs nothing more. A row matched on its help has nothing
	 * marked at all: "Space for the log" appearing under a search for "let" reads as arbitrary
	 * without the sentence that explains it. See `matchedIn`.
	 */
	help?: string;
	/**
	 * Words somebody might type that are in neither the name nor the help.
	 *
	 * The whole reason a hand-written entry is worth writing by hand. Somebody looking for their
	 * vault types "hidden", "PIN" or "private"; the control is called Vault and says none of them.
	 */
	keywords?: string;
	/**
	 * The title of the sub-page the row is drawn on, for a row one level in (More settings).
	 *
	 * The page claims every entry filed under its title (`filedUnder` in `drilldown.svelte.ts`), so
	 * a result, a pasted path or a deep link opens the page before it looks for the row.
	 */
	page?: string;
}

/** Every section, flattened out of its groups, so a section id can be given its label. */
export function everySection(): SettingsSection[] {
	return SETTINGS_GROUPS.flatMap((group) => group.sections);
}

/**
 * The registry's half of the index.
 *
 * A setting with no label is skipped rather than shown under its key: a result reading
 * `download.remember` is a result nobody typed and nobody wants, and the registry refuses a
 * label-less setting anyway, so this is a guard rather than a case.
 */
export function fromRegistry(sections: SettingSection[]): Searchable[] {
	return sections.flatMap((section) =>
		(section.settings ?? [])
			.filter((entry: SettingEntry) => Boolean(entry.label))
			.map((entry: SettingEntry) => ({
				name: entry.label as string,
				/* The written-down join, not a guess from the name. See `REGISTRY_HOME`. The name
				   lower-cased is what an unmapped section falls to, and `grouped` drops it if that
				   answers to nothing; the wire gate is what keeps the map whole. */
				section: REGISTRY_HOME[section.name] ?? section.name.toLowerCase(),
				key: entry.key,
				help: entry.help,
				// The words on the choices themselves. Somebody looking for "Never" or "Ask me
				// first" is looking for the setting that offers it, and the label rarely says it.
				keywords: (entry.choice_labels ?? []).join(' ') || undefined
			}))
	);
}

/**
 * What matches, best kind of match first.
 *
 * Case-insensitive, and every word typed has to be found SOMEWHERE in the entry, so "vault pin"
 * finds the one thing that is about both rather than everything about either. Two words that each
 * match a different part still count, because that is how people describe a thing they half
 * remember.
 */
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

/* Which of the three things a row was found by, or null when it was not found at all.
   Not exported: the only thing that needs the name is the function below, and callers read the
   value rather than the type. `public-surface.test.ts` refuses an exported name nobody reads. */
type MatchedIn = 'name' | 'keyword' | 'help' | null;

/**
 * WHERE the typed words were found in one entry.
 *
 * ONE QUESTION WITH TWO READERS, named rather than answered twice. The order above needs it (a
 * name match beats a keyword match beats a sentence match), and so does the results list, which
 * shows a row's help sentence only when the help is WHY the row is on the list. Answering it
 * separately in the two places is how the two would drift into disagreeing about which rows are
 * explained, and the list's answer would be the wrong one.
 *
 * Every word must be found somewhere, and the tier is decided by where ALL of them are: a search
 * for two words with one in the name and one in the help matched on the help, because the name
 * alone does not answer it.
 */
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
	...appearance
];

/**
 * The whole index: what the registry declares, plus what the panes declare about themselves.
 *
 * Registry entries first, because a registered setting has a row to ring and a hand-written entry
 * only has a pane to open, so where both match, the one that can put somebody exactly where they
 * were going goes above the one that can only get them close.
 */
export function indexOf(sections: SettingSection[]): Searchable[] {
	return [...fromRegistry(sections), ...DECLARED];
}

/* One section with whatever matched on it: what the results are drawn as.
 *
 * Not exported. Both functions below that speak it are, and their callers read the shape off them
 * rather than naming it: an exported name nobody imports is a promise this module has not been
 * asked to keep. See `public-surface.test.ts`. */
interface FoundSection {
	section: SettingsSection;
	/**
	 * The settings on it that matched. Possibly EMPTY, and that is a real result rather than a
	 * degenerate one: it is the section whose own name was what matched.
	 */
	entries: Searchable[];
}

/**
 * What matched, gathered under the section each one is on.
 *
 * ## Why grouped rather than a flat list
 *
 * The results stand exactly where the section list stands, and that list is grouped. A flat list of
 * a dozen rows that each repeat their section in small grey type underneath is the same information
 * written twelve times, in the column where the ungrouped version of it was a moment ago. Gathering
 * them says the same thing once and lets the eye skip a whole section at a time, which is what
 * the library's own search dropdown already does with its headings, so this is the shape the app
 * has.
 *
 * ## A section whose own NAME matches is a result
 *
 * Somebody typing "playback" wants the Playback section, and if only the settings ON it could
 * match, the section itself would be the one thing in Settings that could not be searched for.
 * Those lead, because a section name is the plainest kind of match there is.
 *
 * ## Anything on a section this caller does not know is DROPPED
 *
 * The caller passes the sections it is willing to show, so a guest never sees a group for a pane
 * that is not theirs. It also drops a result whose section id answers to nothing, which can only
 * happen if the server renames a section out from under the label-to-id join in `fromRegistry`.
 * Dropping is the honest end for it: this needs the section's name and its icon to draw a heading
 * and has neither, and the alternative is a result that opens the WRONG pane.
 * `test_settings_sections_agree_across_the_wire.py` is what stops that being a silent loss.
 */
export function grouped(
	index: Searchable[],
	sections: SettingsSection[],
	typed: string
): FoundSection[] {
	const words = typed.toLowerCase().split(/\s+/).filter(Boolean);
	if (words.length === 0) return [];

	/* A settings path (`Settings > Privacy > Auto-lock > ...`) is an address, not words to match:
	   its one result is the place it names. */
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
	   by the best match on it, and nothing here scores anything a second time.

	   Filed under the section the entry LANDS on, through the one resolver: a declaration naming
	   a retired section, or a row that moved to another pane, is drawn under the pane that will
	   actually open. Filed under the id it names, it would be dropped: the retired id is on no
	   list. */
	/* ONE result for one place. A registered setting that moved to a row a pane declares for
	   itself (`swap.guest_tunnel` to `sites.swap_join`) answers under both halves of the index
	   with the same name and the same landing, which would read as two rows for one thing. The first
	   match stands; a later one landing on the same row is the same result said again. */
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

/**
 * Where a settings path lands: its section, and the row it names where the index knows it.
 *
 * The copy button's builder read backwards (`settings-path.ts`). The first crumb is a section's
 * name from the list. The rest are read from the END: the deepest crumb that is the name of
 * something on that section is where the path points, so a path that runs on past a row (to the
 * press on it) still lands on the row, and a heading or a sub-page between them costs nothing.
 *
 * ## A path written from memory
 *
 * Somebody who types a path rather than copying it shortens it: `Playback > Theater > Default`
 * for the row called "Default layout when Theater opens". So when no crumb is a whole name, the
 * same walk from the end asks the search's own question of each crumb (every word of it inside a
 * name), and where more than one row on the section answers, the crumbs above it choose: the row
 * whose words, key or page also say them ("Theater" is in the Theater rows' keys and names). A
 * whole name always wins over a shortened one, so a copied path lands exactly where it did.
 *
 * A path naming nothing the index knows opens the section at its top, the honest answer. Null
 * when the first crumb is no section this caller may open.
 */
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

/**
 * The first thing a search found, as a section id: what the pane beside the results should show.
 *
 * Null when nothing matched, which is the honest answer: the pane keeps whatever it was showing
 * rather than being emptied because somebody mistyped a letter on the way to a word that matches.
 */
export function firstMatch(groups: FoundSection[]): string | null {
	return groups[0]?.section.id ?? null;
}

/**
 * What Enter opens when the arrows have not moved: the first result, as pressing it would.
 *
 * The first setting found, where the first group is there for its settings, so Enter on "Identify
 * faces" opens Tasks and rings that row. The section alone, where the first group is there for its
 * own name: "playback" means the pane.
 */
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
