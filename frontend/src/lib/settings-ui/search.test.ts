import { describe, expect, it } from 'vitest';
import type { SettingSection } from '$lib/settings-ui/settings';
import {
	DECLARED,
	everySection,
	firstMatch,
	firstResult,
	fromRegistry,
	grouped,
	indexOf,
	matchedIn,
	matching,
	nameToFind
} from './search';
import { resolveAddress, type SettingsSection } from './sections';
import type { Searchable } from './search';

/* Finding a setting by typing what you call it.
 *
 * What is worth pinning here is the ordering and the AND, because both are decisions somebody would
 * otherwise change on taste: a name match above a help match, and every word found somewhere rather
 * than any word found anywhere. The second is the difference between "vault pin" finding one thing
 * and finding everything about either.
 */

const REGISTRY: SettingSection[] = [
	{
		name: 'Appearance',
		settings: [
			{
				key: 'appearance.theme',
				value: 'midnight',
				label: 'Theme',
				help: 'How dark the application is.'
			},
			{
				key: 'appearance.rating_scale',
				value: 5,
				label: 'Rating scale',
				help: 'How many stars a file can carry.',
				choice_labels: ['Five', 'Ten']
			}
		]
	},
	{
		name: 'Privacy',
		// No label: the registry refuses one of these, so this is a guard rather than a case.
		settings: [{ key: 'privacy.orphan', value: 1 }]
	}
];

describe('the registry half of the index', () => {
	it("files a server section by the written-down join, not by the screen's label", () => {
		/* Theater's settings are drawn on Playback and Identify's on Faces: neither server name
		   is a label on the list, and a join by name would drop both out of the search. */
		const found = fromRegistry([
			{ name: 'Theater', settings: [{ key: 'theater.layout', value: 'x', label: 'Layout' }] },
			{ name: 'Identify', settings: [{ key: 'faces.enabled', value: true, label: 'Faces' }] }
		]);

		expect(found.map((one) => one.section)).toEqual(['playback', 'faces']);
	});

	it('turns a section NAME into the section id an address is built from', () => {
		/* The reply names sections the way they are shown; a link needs the id. Getting this wrong
		   builds `/settings/Appearance`, which is a 404 dressed as a working result. */
		const [theme] = fromRegistry(REGISTRY);

		expect(theme.section).toBe('appearance');
		expect(theme.key).toBe('appearance.theme');
	});

	it('leaves out a setting with no label rather than showing its key', () => {
		/* `privacy.orphan` is a result nobody typed and nobody could read. */
		expect(fromRegistry(REGISTRY).map((one) => one.key)).not.toContain('privacy.orphan');
	});

	it('carries the words on the CHOICES as things to search by', () => {
		/* Somebody looking for "Ten" is looking for the setting that offers it, and no label says
		   so. This is most of what makes a menu findable at all. */
		const scale = fromRegistry(REGISTRY).find((one) => one.name === 'Rating scale');

		expect(scale?.keywords).toBe('Five Ten');
	});
});

describe('what matches', () => {
	const index = indexOf(REGISTRY);

	it('finds nothing for nothing typed, rather than everything', () => {
		expect(matching(index, '')).toEqual([]);
		expect(matching(index, '   ')).toEqual([]);
	});

	it('needs every word, not any word', () => {
		expect(matching(index, 'rating scale').map((one) => one.name)).toEqual(['Rating scale']);
		expect(matching(index, 'rating theme')).toEqual([]);
	});

	it('puts a name match above a help match', () => {
		/* "dark" is in Theme's HELP and in nothing else's name. Adding a second entry whose NAME
		   contains it has to jump the queue, or a search for a thing by its own name shows the
		   thing that merely mentions it first. */
		const withBoth = indexOf([
			...REGISTRY,
			{ name: 'Appearance', settings: [{ key: 'a.dark', value: 1, label: 'Dark mode' }] }
		]);

		expect(matching(withBoth, 'dark').map((one) => one.name)).toEqual(['Dark mode', 'Theme']);
	});

	it('ignores case, because nobody types a label the way it is written', () => {
		expect(matching(index, 'THEME').map((one) => one.name)).toEqual(['Theme']);
	});

	it('reaches a hand-written control through the words its pane declared', () => {
		/* The whole reason the second feeder exists. Nothing on the Users pane says "invite",
		   and "invite" is what somebody types when they want to let another person in. */
		const names = matching(index, 'invite').map((one) => one.name);

		expect(names).toContain('Guests');
	});

	it('reaches a control through a word that appears nowhere on it', () => {
		/* The control is called Tunnels and says "WireGuard". Nobody types either: they type "vpn",
		   which is what they came to do. A registry-only index cannot answer this at all. */
		const names = matching(indexOf([]), 'vpn').map((one) => one.name);

		expect(names).toContain('Tunnels');
	});
});

describe('where a row was found, which is what the list shows to explain itself', () => {
	/*
	 * The three tiers are the ORDER above and they are also the answer to "why is this row here".
	 * Answering that twice is how the list and the order would come to disagree about which rows are
	 * explained, and the list's answer would be the wrong one, so `matching` is written in terms of
	 * this, and the results column reads it.
	 */
	const row = { name: 'Space for the log', section: 'privacy', help: 'How much Sift keeps.' };

	it('says nothing was found when nothing was typed', () => {
		expect(matchedIn(row, '')).toBeNull();
		expect(matchedIn(row, '   ')).toBeNull();
	});

	it('says nothing was found when the words are in none of the three', () => {
		expect(matchedIn(row, 'zzz')).toBeNull();
	});

	it('names the NAME when the letters are in it, however they fall', () => {
		expect(matchedIn(row, 'log')).toBe('name');
		expect(matchedIn(row, 'SPACE')).toBe('name');
	});

	it('names the KEYWORDS for a word that is in neither the name nor the sentence', () => {
		expect(matchedIn({ ...row, keywords: 'retention' }, 'retention')).toBe('keyword');
	});

	it('names the HELP for a row the sentence is the only reason for', () => {
		/* The case that matters: "let" is inside "Sift" and inside nothing else here, so the row
		   appears with not one letter marked on it. */
		expect(matchedIn(row, 'much')).toBe('help');
	});

	it('needs EVERY word in one tier, so a word each side is a help match', () => {
		/* "log" is in the name and "much" is only in the sentence. The name alone does not answer
		   what was typed, so the sentence is what put the row on the list and is what explains it. */
		expect(matchedIn(row, 'log much')).toBe('help');
	});

	it('agrees with the order `matching` puts things in', () => {
		/* The two must never part company. A row `matching` placed in the help tier and this called a
		   name match would be a row shown with an unnecessary sentence under it, and the other way
		   round is a row shown with nothing. */
		const index = indexOf(REGISTRY);
		for (const found of matching(index, 'dark')) {
			expect(matchedIn(found, 'dark')).not.toBeNull();
		}
	});
});

describe('the declared half', () => {
	it('names only addresses that land on a section that exists', () => {
		/* A result opening `/settings/typo` is a dead end that looks like a working search, and
		   nothing else would ever catch it: the entry compiles, matches, and goes nowhere. A retired
		   id is allowed (every result opens through the resolver), but only one that LANDS. */
		const ids = new Set(everySection().map((one) => one.id));

		for (const entry of DECLARED) {
			const lands = resolveAddress(entry.section, entry.key, entry.show).section;
			expect(ids, `${entry.name} names section ${entry.section}`).toContain(lands);
		}
	});

	it('gives every entry a name and something to find it by', () => {
		for (const entry of DECLARED) {
			expect(entry.name.trim()).not.toBe('');
			expect(`${entry.keywords ?? ''}${entry.help ?? ''}`.trim()).not.toBe('');
		}
	});

	it('does not declare the same thing twice', () => {
		const seen = DECLARED.map((one) => `${one.section}:${one.name}`);

		expect(new Set(seen).size).toBe(seen.length);
	});
});

describe('gathered under the section each thing is on', () => {
	/* Three sections, so "only what this caller draws" and "the order groups come out in" are both
	   askable. Real shapes, invented ids: this is about the gathering, not about Sift's own list. */
	const SECTIONS: SettingsSection[] = [
		{ id: 'appearance', label: 'Appearance', icon: 'palette' },
		{ id: 'sites', label: 'Sites', icon: 'public' },
		{ id: 'secret', label: 'Staff only', icon: 'lock' }
	];

	const INDEX: Searchable[] = [
		{ name: 'Theme', section: 'appearance', key: 'appearance.theme', help: 'How dark it is.' },
		{
			name: 'Rating scale',
			section: 'appearance',
			key: 'appearance.rating_scale',
			help: 'How many stars.'
		},
		{ name: 'Tunnels', section: 'sites', keywords: 'vpn wireguard' },
		{ name: 'Staff switch', section: 'secret', keywords: 'vpn' },
		{ name: 'Orphaned', section: 'nowhere', keywords: 'vpn' }
	];

	it('puts each thing under its own section, once', () => {
		const found = grouped(INDEX, SECTIONS, 'how');

		expect(found.map((one) => one.section.id)).toEqual(['appearance']);
		expect(found[0].entries.map((one) => one.name)).toEqual(['Theme', 'Rating scale']);
	});

	it('leads with a section whose own NAME matches, with nothing under it', () => {
		/* A section is something Settings can be searched for, not only the settings ON it:
		   otherwise typing its name finds whatever happens to mention it in a sentence. A group
		   with no entries is the honest shape for it: the heading IS the result. */
		const found = grouped(INDEX, SECTIONS, 'sites');

		expect(found.map((one) => one.section.id)).toEqual(['sites']);
		expect(found[0].entries).toEqual([]);
	});

	it('keeps the order `matching` decided, so nothing is ranked twice', () => {
		/* "vpn" is a keyword on two entries. Which group leads is decided by where its best match
		   came in the flat list, and nothing here re-scores anything. */
		const found = grouped(INDEX, SECTIONS, 'vpn');

		expect(found.map((one) => one.section.id)).toEqual(['sites', 'secret']);
	});

	it('draws nothing for a section this caller did not offer', () => {
		/* How a guest is kept from being shown a door that will not open: the shell passes the
		   sections it is willing to draw, and this drops the rest rather than the caller filtering
		   afterwards and having to remember to. */
		const forAGuest = SECTIONS.filter((one) => one.id !== 'secret');
		const found = grouped(INDEX, forAGuest, 'vpn');

		expect(found.map((one) => one.section.id)).toEqual(['sites']);
	});

	it('drops a result whose section answers to nothing at all', () => {
		/* It can only happen if the server renames a section out from under the label-to-id join
		   in `fromRegistry`. There is no heading to draw for it (no name and no icon), and
		   the alternative is a result that opens the WRONG pane. A Python gate is what stops
		   this being a silent loss; see `test_settings_sections_agree_across_the_wire.py`. */
		const found = grouped(INDEX, SECTIONS, 'vpn').flatMap((one) => one.entries);

		expect(found.map((one) => one.name)).not.toContain('Orphaned');
	});

	it('files an entry naming a retired section under the pane that inherited it', () => {
		/* History is a tab of Activity. A declaration still naming `ledger` is drawn under
		   Activity, and keeps its own section, which is what opens it on the right TAB. */
		const withActivity: SettingsSection[] = [
			...SECTIONS,
			{ id: 'tasks', label: 'Tasks and Activity', icon: 'calendar_clock' }
		];
		const entry: Searchable = { name: 'Who did what', section: 'ledger', keywords: 'audit vpn' };
		const found = grouped([entry], withActivity, 'audit');

		expect(found.map((one) => one.section.id)).toEqual(['tasks']);
		expect(found[0].entries[0].section).toBe('ledger');
	});

	it('finds nothing for an empty box, rather than every section there is', () => {
		expect(grouped(INDEX, SECTIONS, '   ')).toEqual([]);
	});

	it('says which section the pane beside it should show', () => {
		expect(firstMatch(grouped(INDEX, SECTIONS, 'vpn'))).toBe('sites');
	});

	it('opens the first setting found on Enter, key and all, the way pressing it does', () => {
		expect(firstResult(grouped(INDEX, SECTIONS, 'rating scale'), 'rating scale')).toEqual({
			section: 'appearance',
			key: 'appearance.rating_scale',
			show: undefined
		});
	});

	it('hands a result with no key its name, to be looked for on the pane', () => {
		const heading: Searchable = { name: 'Auto-lock', section: 'privacy', keywords: 'idle' };
		const keyed: Searchable = { name: 'Rating scale', section: 'appearance', key: 'a.b' };
		expect(nameToFind(heading)).toBe('Auto-lock');
		expect(nameToFind(keyed)).toBeUndefined();
	});

	it('opens a section found by its own name on Enter as the section', () => {
		expect(firstResult(grouped(INDEX, SECTIONS, 'sites'), 'sites')).toEqual({ section: 'sites' });
		expect(firstResult(grouped(INDEX, SECTIONS, 'zzzz'), 'zzzz')).toBeNull();
	});

	it('says nothing rather than a section when nothing matched', () => {
		/* Null, so the pane keeps what it was showing. Emptying it because somebody mistyped a letter
		   on the way to a word that matches is a screen that flickers while you type. */
		expect(firstMatch(grouped(INDEX, SECTIONS, 'zzzz'))).toBeNull();
	});
});

/* The sections that moved keep being found by the words a person already uses for them. Real
   declarations and the real list: a search for a thing that moved must land where it is now. */
describe('the sections that moved', () => {
	const REAL_REGISTRY: SettingSection[] = [
		{
			name: 'Stash-boxes',
			settings: [{ key: 'music.lookup', value: false, label: 'Name songs with AcoustID' }]
		}
	];
	const EVERYTHING = indexOf(REAL_REGISTRY);
	const ALL = everySection();

	it('finds Music by its name and by AcoustID, on the Music pane', () => {
		expect(firstMatch(grouped(EVERYTHING, ALL, 'music'))).toBe('music');
		expect(firstMatch(grouped(EVERYTHING, ALL, 'acoustid'))).toBe('music');
	});

	it('finds About on the foot of Updates', () => {
		expect(firstMatch(grouped(EVERYTHING, ALL, 'about'))).toBe('updates');
	});

	it('finds the confirmations and network sharing on General', () => {
		const lands = (typed: string) => grouped(EVERYTHING, ALL, typed).map((one) => one.section.id);
		expect(lands('ask before deleting from disk')[0]).toBe('general');
		expect(lands('share this library on my network')[0]).toBe('general');
		expect(lands('ask before deleting from disk')).not.toContain('editing');
		expect(lands('share this library on my network')).not.toContain('privacy');
	});
});

describe('one place, one result', () => {
	it('shows the tunnel for joining a swap once, on the row the Swap tunnels block draws', () => {
		/* The registry files it under Privacy and it is drawn beside the tunnels, where Sites and
		   Tunnels also declares the row: both halves answer, and the list must say it once. */
		const registry: SettingSection[] = [
			{
				name: 'Privacy',
				settings: [
					{
						key: 'swap.guest_tunnel',
						value: '',
						label: 'Tunnel for joining a swap',
						help: 'The tunnel a join goes through.'
					}
				]
			}
		];
		const found = grouped(indexOf(registry), everySection(), 'tunnel for joining a swap');
		const rows = found.flatMap((one) => one.entries);
		expect(rows.map((one) => one.name)).toEqual(['Tunnel for joining a swap']);
		const only = rows[0];
		expect(resolveAddress(only.section, only.key, only.show)).toMatchObject({
			section: 'sites',
			key: 'sites.swap_join'
		});
	});
});

describe('a name that begins with what was typed', () => {
	it('comes before a name that has it further in', () => {
		const index: Searchable[] = [
			{ name: 'Ask about a match above', section: 'faces', key: 'faces.ask' },
			{ name: 'Version', section: 'updates', key: 'updates.version' }
		];
		expect(matching(index, 'ver').map((one) => one.name)).toEqual(['Version']);
		expect(matching(index, 'about').map((one) => one.name)).toEqual(['Ask about a match above']);
	});

	it('lands a search for "version" on Updates, over a setting that says it further in', () => {
		/* The registry half comes first in the index, so without the lead a setting naming a
		   version halfway through would be the first result and Enter would open its pane. */
		const registry: SettingSection[] = [
			{
				name: 'Identify',
				settings: [{ key: 'faces.ask', value: true, label: 'Ask about a newer version' }]
			}
		];
		expect(firstMatch(grouped(indexOf(registry), everySection(), 'version'))).toBe('updates');
	});
});
