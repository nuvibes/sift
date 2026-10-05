/* The docs site read for the build: where links go, the menu, and the real site as it is now. */
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { DocPage } from './read';
import { checkAnchors, linker, menuOf, moduleOf, readSite } from './site';

const link = linker({
	slugs: new Set(['library/browse', 'developers']),
	routes: { '/browse': '/library/browse/', '/organize': '/library/organize/' },
	discord: 'https://discord.example/invite'
});

describe('a link in the docs', () => {
	it('opens a setting, a screen, another page or the web', () => {
		expect(link('/settings/updates#updates.version')).toEqual({
			kind: 'setting',
			section: 'updates',
			key: 'updates.version'
		});
		expect(link('/settings/general')).toEqual({ kind: 'setting', section: 'general' });
		expect(link('/organize/quarantine')).toEqual({ kind: 'place', path: '/organize/quarantine' });
		expect(link('/library/browse/#sort-the-wall')).toEqual({
			kind: 'page',
			slug: 'library/browse',
			anchor: 'sort-the-wall'
		});
		expect(link('/developers/#_top')).toEqual({ kind: 'page', slug: 'developers' });
		expect(link('{{discord}}')).toEqual({
			kind: 'outside',
			href: 'https://discord.example/invite'
		});
		expect(link('https://example.com/a')).toEqual({
			kind: 'outside',
			href: 'https://example.com/a'
		});
	});

	it.each([
		'/nowhere/',
		'../relative',
		'http://plain.example',
		'/settings',
		'/settings/a/b',
		'/browse#part'
	])('refuses %s', (href) => {
		expect(() => link(href)).toThrow();
	});
});

const page = (slug: string, title = slug) => ({ slug, title, blocks: [] });

interface Entry {
	label?: string;
	slug?: string;
	title?: string;
	items?: Entry[];
}

describe('the menu', () => {
	const config = [
		"label: 'Get started'",
		"label: 'Your library'",
		"label: 'Settings'",
		"slug: 'whats-new'",
		"slug: 'help'",
		"label: 'For developers'",
		"'developers/api'"
	].join('\n');
	const sidebar = {
		library: ['library/browse'],
		settings: [{ label: 'System', items: ['settings/general'] }]
	};
	const pages = () =>
		Object.fromEntries(
			[
				{ ...page('get-started/b'), order: 2 },
				{ ...page('get-started/a'), order: 1 },
				page('library/browse', 'Browse'),
				page('settings/general'),
				page('whats-new'),
				page('help'),
				page('developers'),
				page('developers/api'),
				page('')
			].map((one) => [one.slug, one])
		);

	it("follows the site's own order, steps by their number", () => {
		const menu = menuOf(pages(), sidebar, config) as Entry[];
		expect(menu.map((item) => item.label ?? item.slug)).toEqual([
			'Get started',
			'Your library',
			'Settings',
			'whats-new',
			'help',
			'For developers'
		]);
		expect(menu[0]?.items?.map((item) => item.slug)).toEqual(['get-started/a', 'get-started/b']);
		expect(menu[1]?.items).toEqual([{ slug: 'library/browse', title: 'Browse' }]);
	});

	it('refuses a page it does not reach, a page it names that is missing, and a changed site menu', () => {
		expect(() =>
			menuOf({ ...pages(), 'library/lost': page('library/lost') }, sidebar, config)
		).toThrow('library/lost');
		expect(() =>
			menuOf(pages(), { ...sidebar, library: ['library/browse', 'library/gone'] }, config)
		).toThrow('library/gone');
		expect(() => menuOf(pages(), sidebar, config.replace('Your library', 'Library'))).toThrow(
			'teach scripts/docs_pages.js'
		);
	});
});

describe('an anchor a link names', () => {
	const linked = (anchor: string): Record<string, DocPage> => ({
		a: {
			slug: 'a',
			title: 'A',
			blocks: [
				{
					kind: 'paragraph',
					inline: [{ kind: 'link', text: 'b', to: { kind: 'page', slug: 'b', anchor } }]
				}
			]
		},
		b: {
			slug: 'b',
			title: 'B',
			blocks: [
				{ kind: 'heading', level: 2, anchors: ['the-part'], inline: ['The part'] },
				{ kind: 'list', ordered: false, items: [{ anchors: ['row.key'], inline: ['Row'] }] }
			]
		}
	});

	it('is a heading or an anchor on that page, or the build stops', () => {
		expect(() => checkAnchors(linked('the-part'))).not.toThrow();
		expect(() => checkAnchors(linked('row.key'))).not.toThrow();
		expect(() => checkAnchors(linked('missing'))).toThrow('a: a link to #missing');
	});
});

describe('the docs site as it is now', () => {
	it('reads every page, with a picture for each screen it shows', () => {
		const { book, screens } = readSite(resolve('..', 'docs-site'));
		expect(Object.keys(book.pages).length).toBeGreaterThan(40);
		expect(book.home.length).toBeGreaterThan(0);
		expect(screens.size).toBeGreaterThan(20);
		const source = moduleOf(book, [...screens.keys()]);
		expect(source).toContain("import screen0 from './screens/");
		expect(source).toContain('export const BOOK = JSON.parse(');
	});
});
