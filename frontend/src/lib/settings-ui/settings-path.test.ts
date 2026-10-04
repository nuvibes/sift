/* A settings path: the copy button's builder, and the search box reading one back. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const copied = vi.hoisted(() => ({ texts: [] as string[] }));
vi.mock('$lib/shell/clipboard', () => ({
	copyText: async (text: string) => {
		copied.texts.push(text);
		return true;
	}
}));

import Probe from './PathCopyProbe.test.svelte';
import { drilldown, filedUnder } from './drilldown.svelte';
import { crumbsOf, pathOf } from './settings-path';
import { grouped, firstResult, landing, type Searchable } from './search';
import { type SettingsSection } from './sections';
import { SEARCHABLE as SEMANTIC } from './Semantic.search';
import { SEARCHABLE as FACES } from './Faces.search';
import { SEARCHABLE as WATERMARKS } from './Watermarks.search';

describe('writing and reading a path', () => {
	it('writes the crumbs after Settings, joined the way a person writes them', () => {
		expect(pathOf(['Privacy', 'Auto-lock', 'Lock Hidden when you switch away'])).toBe(
			'Settings > Privacy > Auto-lock > Lock Hidden when you switch away'
		);
		expect(pathOf(['Importing', undefined, ' ', 'Folders'])).toBe('Settings > Importing > Folders');
	});

	it('reads a path back however a copy picked it up', () => {
		const crumbs = ['Importing', 'Folders'];
		expect(crumbsOf('Settings > Importing > Folders')).toEqual(crumbs);
		expect(crumbsOf('`Settings > Importing > Folders`')).toEqual(crumbs);
		expect(crumbsOf('  settings>Importing >  Folders > ')).toEqual(crumbs);
		expect(crumbsOf('Settings \u203a Importing \u203a Folders')).toEqual(crumbs);
	});

	it('leaves an ordinary search alone', () => {
		expect(crumbsOf('lock hidden')).toBeNull();
		expect(crumbsOf('Settings')).toBeNull();
		expect(crumbsOf('Settings >')).toBeNull();
	});

	it('reads a path written without the leading Settings', () => {
		expect(crumbsOf('Playback > Theater > Default')).toEqual(['Playback', 'Theater', 'Default']);
		expect(crumbsOf('Privacy > Auto-lock')).toEqual(['Privacy', 'Auto-lock']);
		// One crumb and a stray separator is a word, not a path.
		expect(crumbsOf('Privacy >')).toBeNull();
	});
});

const SECTIONS: SettingsSection[] = [
	{ id: 'privacy', label: 'Privacy', icon: 'shield_person' },
	{ id: 'semantic', label: 'Smart Search', icon: 'auto_awesome' },
	{ id: 'importing', label: 'Importing', icon: 'inbox' }
];

const INDEX: Searchable[] = [
	{ name: 'Lock Hidden when you switch away', section: 'privacy', key: 'privacy.lock_on_blur' },
	{ name: 'Download the models again', section: 'semantic', key: 'semantic.models' },
	{ name: 'More settings', section: 'semantic', key: 'semantic.more' },
	{ name: 'Folders', section: 'importing', key: 'importing.folders' }
];

describe('the search box reading a path backwards', () => {
	it('lands on the row a path names, through its group', () => {
		const crumbs = crumbsOf('Settings > Privacy > Auto-lock > Lock Hidden when you switch away');
		expect(landing(INDEX, SECTIONS, crumbs!)).toEqual({ section: SECTIONS[0], entry: INDEX[0] });
	});

	it('lands on the row when the path runs on to the press on it', () => {
		const typed =
			'Settings > Smart Search > More settings > Edit > Models > Download the models again > Download';
		const target = firstResult(grouped(INDEX, SECTIONS, typed), typed);
		expect(target).toEqual({ section: 'semantic', key: 'semantic.models', show: undefined });
	});

	/* Against the index the panes really declare, not a stand-in: a row drawn on a sub-page is
	   only reachable by path when it is in the index, and a stand-in can have it where the real
	   one does not. */
	it('lands on the models row through the real Smart Search index', () => {
		const typed =
			'Settings > Smart Search > More settings > Edit > Models > Download the models again > Download';
		const target = firstResult(grouped(SEMANTIC, SECTIONS, typed), typed);
		expect(target).toEqual({ section: 'semantic', key: 'semantic.models', show: undefined });
	});

	/* Which rows are on which page is the settings sub-page gate's question; this is the claim
	   that reads its answer. */
	it('claims for a page exactly the entries filed under it', () => {
		const index = [...SEMANTIC, ...FACES, ...WATERMARKS];
		const filed = index.filter((one) => one.page);
		expect(filed.length).toBeGreaterThan(0);
		for (const one of filed) expect(filedUnder(index, one.page as string)).toContain(one.key);
		expect(filedUnder(index, 'No such page')).toEqual([]);
		expect(filedUnder(index, 'More settings')).not.toContain('faces.more');
	});

	it('opens the section where the path names nothing it knows, and nothing for no section', () => {
		const typed = 'Settings > Importing > Nowhere at all';
		expect(firstResult(grouped(INDEX, SECTIONS, typed), typed)).toEqual({ section: 'importing' });
		expect(grouped(INDEX, SECTIONS, 'Settings > Nowhere > Folders')).toEqual([]);
	});

	/* A path as somebody writes it from memory: shortened, with or without the
	   root. It lands on the row it means, ringed, as picking that row from a typed search would. */
	it('lands a shortened path on the row it means, through the crumbs above it', () => {
		const sections: SettingsSection[] = [
			{ id: 'playback', label: 'Playback', icon: 'video_settings' }
		];
		const index: Searchable[] = [
			{ name: 'Start muted', section: 'playback', key: 'playback.muted' },
			{ name: 'Default volume guess', section: 'playback', key: 'playback.volume' },
			{ name: 'Start playing when Theater opens', section: 'playback', key: 'theater.autoplay' },
			{ name: 'Default layout when Theater opens', section: 'playback', key: 'theater.layout' }
		];
		for (const typed of [
			'Settings > Playback > Theater > Default',
			'Playback > Theater > Default',
			'settings > playback > theater > default >'
		]) {
			expect(firstResult(grouped(index, sections, typed), typed)).toEqual({
				section: 'playback',
				key: 'theater.layout',
				show: undefined
			});
		}
		// A heading alone lands on its group's first row; a whole name beats a shortened one.
		expect(landing(index, sections, ['Playback', 'Theater'])?.entry).toBe(index[2]);
		expect(landing(index, sections, ['Playback', 'Start muted', 'Start'])?.entry).toBe(index[0]);
		// The section alone opens the section; a path naming no section finds nothing.
		expect(firstResult(grouped(index, sections, 'Playback > '), 'Playback >')).toBeNull();
		expect(firstResult(grouped(index, sections, 'Settings > Playback'), 'x')).toEqual({
			section: 'playback'
		});
		expect(grouped(index, sections, 'Theater > Default')).toEqual([]);
	});

	it('keeps to the named section when two sections share a row name', () => {
		const typed = 'Settings > Importing > Folders';
		expect(grouped(INDEX, SECTIONS, typed)).toEqual([
			{ section: SECTIONS[2], entries: [INDEX[3]] }
		]);
	});
});

describe('the copy button beside a settings name', () => {
	let drawn: Record<string, unknown> | null = null;

	afterEach(() => {
		if (drawn) unmount(drawn);
		drawn = null;
		drilldown.close();
		copied.texts.length = 0;
		document.body.innerHTML = '';
	});

	function draw(): HTMLElement {
		const host = document.createElement('div');
		document.body.append(host);
		drawn = mount(Probe, { target: host }) as Record<string, unknown>;
		flushSync();
		return host;
	}

	async function copyBeside(host: HTMLElement, words: string): Promise<string | undefined> {
		const holder = [...host.querySelectorAll<HTMLElement>('.path-host')].find((one) =>
			one.textContent?.includes(words)
		);
		holder?.querySelector<HTMLButtonElement>('button[aria-label="Copy settings path"]')?.click();
		await vi.waitFor(() => expect(copied.texts.length).toBeGreaterThan(0), { interval: 1 });
		return copied.texts.pop();
	}

	it('copies the path of the title, a group heading and a row, from the names on screen', async () => {
		const host = draw();
		expect(await copyBeside(host, 'Smart Search')).toBe('Settings > Smart Search');
		expect(await copyBeside(host, 'Auto-lock')).toBe('Settings > Smart Search > Auto-lock');
		expect(await copyBeside(host, 'Lock Hidden when you switch away')).toBe(
			'Settings > Smart Search > Auto-lock > Lock Hidden when you switch away'
		);
	});

	it('carries a sub-page and the press that opened it into every path on it', async () => {
		const host = draw();
		host.querySelector<HTMLButtonElement>('.control button')?.click();
		flushSync();
		expect(await copyBeside(host, 'Download the models again')).toBe(
			'Settings > Smart Search > More settings > Edit > Models > Download the models again'
		);
	});

	it('is named for what it does, and leaves the tab order to the rows', () => {
		const host = draw();
		const press = host.querySelector<HTMLButtonElement>('button[aria-label="Copy settings path"]');
		expect(press).not.toBeNull();
		expect(press?.tabIndex).toBe(-1);
	});
});
