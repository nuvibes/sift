/* Settings addresses that moved: each old id still lands on a section that is drawn today. */
import { flushSync, mount, unmount } from 'svelte';
import { describe, expect, it } from 'vitest';

import SettingsTitle from './SettingsTitle.svelte';
import {
	DEFAULT_SECTION,
	REGISTRY_HOME,
	SETTINGS_GROUPS,
	SETTINGS_SECTIONS,
	isKnownSection,
	labelFor,
	firstSectionFor,
	resolveAddress,
	sectionFor,
	settingsPath,
	settledSection
} from './sections';

const drawn = new Set(SETTINGS_SECTIONS.map((section) => section.id));

describe('a settings address that moved', () => {
	it('sends the old sign-ins address to Users', () => {
		// The id followed its label, so `/settings/accounts` is an older address.
		expect(isKnownSection('accounts')).toBe(true);
		expect(settledSection('accounts')).toBe('users');
		expect(labelFor('accounts')).toBe('User Management');
	});

	it('never sends an old id to a section that is not drawn', () => {
		// The table is read once, so a chain of two retired ids would land on nothing.
		for (const old of [
			'accounts',
			'connections',
			'platforms',
			'compression',
			'theater',
			'ledger',
			'logs',
			'about'
		]) {
			expect(drawn.has(settledSection(old)), old).toBe(true);
		}
	});

	it('sends About, and the About block Updates had, to the version at the top of Updates', () => {
		expect(drawn.has('about')).toBe(false);
		expect(settledSection('about')).toBe('updates');
		expect(labelFor('about')).toBe('Updates and Info');
		expect(resolveAddress('about', 'about.sift')).toEqual({
			section: 'updates',
			key: 'updates.version'
		});
		expect(resolveAddress('updates', 'updates.about')).toEqual({
			section: 'updates',
			key: 'updates.version'
		});
	});

	it('keeps an old link to a row that moved pane landing on the row', () => {
		expect(resolveAddress('editing', 'editing.delete.ask')).toEqual({
			section: 'general',
			key: 'editing.delete.ask'
		});
		expect(resolveAddress('privacy', 'privacy.network_sharing')).toEqual({
			section: 'general',
			key: 'privacy.network_sharing'
		});
		expect(resolveAddress('stash-boxes', 'music.lookup_route')).toEqual({
			section: 'music',
			key: 'music.lookup_route'
		});
		expect(resolveAddress('stash-boxes', 'stash-boxes.music')).toEqual({
			section: 'music',
			key: 'music.acoustid'
		});
	});

	it('draws Users under its own id', () => {
		expect(drawn.has('users')).toBe(true);
		expect(drawn.has('accounts')).toBe(false);
	});
});

/* THE ONE RESOLVER, which every door goes through: a `SettingLink`, a search result, a bookmark
 * and a refresh. */
describe('the resolver behind every settings address', () => {
	it('keeps an address that is already current exactly as it is', () => {
		expect(resolveAddress('playback', 'playback.resume_enabled')).toEqual({
			section: 'playback',
			key: 'playback.resume_enabled'
		});
		expect(resolveAddress('updates')).toEqual({ section: 'updates' });
	});

	it('lands a Theater address on Playback, with the row it named', () => {
		expect(resolveAddress('theater', 'theater.layout')).toEqual({
			section: 'playback',
			key: 'theater.layout'
		});
	});

	it('lands History and Logs on their TAB of Tasks and Activity, not on Tasks', () => {
		expect(resolveAddress('ledger')).toEqual({ section: 'tasks', show: 'history' });
		expect(resolveAddress('logs', 'logs.level')).toEqual({
			section: 'tasks',
			show: 'log',
			key: 'logs.level'
		});
		expect(settingsPath(resolveAddress('logs', 'logs.level'))).toBe(
			'/settings/tasks?show=log#logs.level'
		);
	});

	it('lands the two sections Tasks and Activity joined on their tabs, rows and all', () => {
		expect(resolveAddress('schedule')).toEqual({ section: 'tasks' });
		expect(resolveAddress('schedule', 'tasks.quiet-hours')).toEqual({
			section: 'tasks',
			key: 'tasks.quiet-hours'
		});
		// `jobs` always opened on the queue, which is the Activity tab.
		expect(resolveAddress('jobs')).toEqual({ section: 'tasks', show: 'now' });
		expect(settingsPath(resolveAddress('jobs'))).toBe('/settings/tasks?show=now');
		// A row that moved to a task's row before still lands on it.
		expect(resolveAddress('importing', 'importing.scan')).toEqual({
			section: 'tasks',
			key: 'tasks.scan.when'
		});
		expect(labelFor('schedule')).toBe('Tasks and Activity');
		expect(labelFor('jobs')).toBe('Tasks and Activity');
	});

	it('lands Importing, and every row and stage it drew, on Tasks', () => {
		expect(resolveAddress('importing')).toEqual({ section: 'tasks' });
		expect(labelFor('importing')).toBe('Tasks and Activity');
		expect(resolveAddress('importing', 'shoots.auto_file')).toEqual({
			section: 'tasks',
			key: 'shoots.auto_file'
		});
		for (const stage of ['scan', 'generate', 'identify']) {
			for (const old of [`importing.${stage}-stage`, `importing.${stage}-now`]) {
				expect(resolveAddress('importing', old), old).toEqual({
					section: 'tasks',
					key: `tasks.${stage}.when`
				});
			}
		}
		expect(settledSection(REGISTRY_HOME.Importing)).toBe('tasks');
	});

	it('lands the device id, which left Privacy, on Updates and Info', () => {
		expect(resolveAddress('privacy', 'privacy.swaps')).toEqual({
			section: 'updates',
			key: 'updates.device_id'
		});
	});

	it('follows a ROW that moved off a section that still exists', () => {
		// Appearance is still a section, so only the row can say it left.
		expect(resolveAddress('appearance', 'appearance.closing_the_window')).toEqual({
			section: 'general',
			key: 'general.closing_the_window'
		});
		// Drawn on Generate's page under Import tasks; linked to as Performance's.
		expect(resolveAddress('performance', 'performance.repair_playback')).toEqual({
			section: 'tasks',
			key: 'performance.repair_playback'
		});
		expect(resolveAddress('appearance', 'appearance.links_open_in')).toEqual({
			section: 'general',
			key: 'general.links_open_in'
		});
	});

	it('keeps a tab asked for explicitly over one a redirect implies', () => {
		expect(resolveAddress('jobs', undefined, 'history')).toEqual({
			section: 'tasks',
			show: 'history'
		});
	});
});

/* The list: four groups, and the ids that are addresses. */
describe('the sections and their groups', () => {
	/* THE TREE, pinned whole. */
	it('is the tree, in the order each group is opened', () => {
		expect(
			SETTINGS_GROUPS.map((group) => [group.heading, group.sections.map((one) => one.label)])
		).toEqual([
			[
				'Library',
				['Folders', 'Tasks and Activity', 'Sites and Tunnels', 'Downloads', 'Editing', 'Playback']
			],
			[
				'Personal',
				[
					'Profile',
					'Appearance',
					'Get to know Sift',
					'Insights',
					'Privacy and Security',
					'User Management',
					'Shortcuts'
				]
			],
			['Recognition', ['Faces', 'Smart Search', 'Stash-boxes', 'Music', 'Watermarks']],
			[
				'System',
				[
					'General',
					'Performance',
					'Maintenance',
					'Backup and restore',
					'Updates and Info',
					'Documentation'
				]
			]
		]);
	});

	it('offers Documentation to everybody, under Updates and Info, as a book', () => {
		const at = SETTINGS_SECTIONS.findIndex((one) => one.id === 'documentation');
		expect(SETTINGS_SECTIONS[at - 1]?.id).toBe('updates');
		expect(SETTINGS_SECTIONS[at]).toEqual({
			id: 'documentation',
			label: 'Documentation',
			icon: 'menu_book'
		});
	});

	it('draws Music for an admin and Get to know Sift for everybody', () => {
		const music = SETTINGS_SECTIONS.find((one) => one.id === 'music');
		const learn = SETTINGS_SECTIONS.find((one) => one.id === 'get-to-know');
		expect(music?.admin).toBe(true);
		expect(learn, 'Get to know Sift is not a section').toBeDefined();
		expect(learn?.admin).toBeUndefined();
		/* Everybody reads the licence, so the section that holds it is everybody's. */
		expect(SETTINGS_SECTIONS.find((one) => one.id === 'updates')?.admin).toBeUndefined();
	});

	it("draws Insights for everybody, where the server's Insights section is filed", () => {
		const insights = SETTINGS_SECTIONS.find((one) => one.id === 'insights');
		expect(insights).toEqual({ id: 'insights', label: 'Insights', icon: 'insights' });
		expect(REGISTRY_HOME.Insights).toBe('insights');
	});

	it('is the four groups, in order, with Personal second', () => {
		expect(SETTINGS_GROUPS.map((group) => group.heading)).toEqual([
			'Library',
			'Personal',
			'Recognition',
			'System'
		]);
	});

	it('draws General, and draws none of Theater, History, Logs, Tasks or Activity as a section', () => {
		expect(drawn.has('general')).toBe(true);
		for (const gone of ['theater', 'ledger', 'logs', 'schedule', 'jobs', 'importing'])
			expect(drawn.has(gone), gone).toBe(false);
		expect(labelFor('faces')).toBe('Faces');
		expect(SETTINGS_SECTIONS.find((one) => one.id === 'tasks')?.icon).toBe('calendar_clock');
	});

	it("files every server section's settings under an address that lands on a drawn section", () => {
		for (const [name, home] of Object.entries(REGISTRY_HOME)) {
			expect(drawn.has(settledSection(home)), `${name} -> ${home}`).toBe(true);
		}
	});
});

/* The title above a pane is drawn by the frame from the section's ONE declaration (the same
   words and the same icon the list on the left draws), so the two cannot disagree. */
describe('the title a section is drawn under', () => {
	it('is the declaration the list draws, followed through a moved address', () => {
		const users = SETTINGS_SECTIONS.find((section) => section.id === 'users');
		expect(sectionFor('accounts')).toBe(users);
		expect(sectionFor('no-such-section')).toBeUndefined();
	});

	it('names the section beside its icon, and a sub-page without one', () => {
		const host = document.createElement('div');
		document.body.append(host);
		const backup = sectionFor('backup')!;

		const titled = mount(SettingsTitle, {
			target: host,
			props: { label: backup.label, icon: backup.icon }
		});
		flushSync();
		const heading = host.querySelector('h1');
		expect(heading?.textContent?.trim()).toContain('Backup and restore');
		expect(heading?.querySelector('.icon')).not.toBeNull();
		unmount(titled);

		const sub = mount(SettingsTitle, { target: host, props: { label: 'Stash-box fields' } });
		flushSync();
		expect(host.querySelector('h1 .icon')).toBeNull();
		unmount(sub);
		host.remove();
	});
});

describe('who Folders is for', () => {
	it('is an admin section: every route behind it answers an admin only', () => {
		expect(SETTINGS_SECTIONS.find((one) => one.id === 'library')?.admin).toBe(true);
	});

	it("lands Settings with no section named on the first section of each account's own list", () => {
		expect(firstSectionFor(true)).toBe(DEFAULT_SECTION);
		const guest = firstSectionFor(false);
		expect(SETTINGS_SECTIONS.find((one) => one.id === guest)?.admin).toBeUndefined();
		// The first a guest's list draws, so the lit row and the pane agree.
		expect(guest).toBe(SETTINGS_SECTIONS.filter((one) => !one.admin)[0].id);
	});
});
