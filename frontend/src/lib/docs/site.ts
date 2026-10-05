// SPDX-License-Identifier: AGPL-3.0-or-later
/* The whole docs site read into one book, for the build (`scripts/docs_pages.js`) and its tests. */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';

import {
	DocRefused,
	frontOf,
	readBlocks,
	type DocBlock,
	type DocBook,
	type DocInline,
	type DocLink,
	type DocMenuItem,
	type DocPage
} from './read.ts';

type Ordered = DocPage & { order?: number };

interface Sidebar {
	library: string[];
	settings: { label: string; items: string[] }[];
	routes: Record<string, string>;
}

/** Every Markdown page, by the slug the site gives it. */
function pageFiles(root: string): Map<string, string> {
	const found = new Map<string, string>();
	const walk = (dir: string) => {
		for (const entry of readdirSync(dir, { withFileTypes: true })) {
			const path = join(dir, entry.name);
			if (entry.isDirectory()) walk(path);
			else if (!entry.name.endsWith('.md')) {
				throw new DocRefused(`a file the docs pane doesn't read: ${path}`);
			} else {
				const file = relative(root, path).split(sep).join('/');
				found.set(file.replace(/\.md$/, '').replace(/(^|\/)index$/, ''), path);
			}
		}
	};
	walk(root);
	return found;
}

/** Where a link in the docs goes inside Sift: another page, a setting, a screen, or the web. */
export function linker(site: {
	slugs: Set<string>;
	routes: Record<string, string>;
	discord: string;
}): (href: string) => DocLink {
	return (href) => {
		if (href === '{{discord}}') return { kind: 'outside', href: site.discord };
		if (/^https:\/\/[^/]/.test(href)) return { kind: 'outside', href };
		if (!href.startsWith('/') || href.startsWith('//')) {
			throw new DocRefused(`a link to nowhere the pane can open: ${href}`);
		}
		const [path = '', hash = ''] = href.split('#');
		const parts = path.split('/').filter(Boolean);
		if (parts[0] === 'settings') {
			if (parts.length !== 2) throw new DocRefused(`a settings link to no one section: ${href}`);
			return { kind: 'setting', section: parts[1] as string, ...(hash ? { key: hash } : {}) };
		}
		if (`/${parts[0]}` in site.routes) {
			if (hash) throw new DocRefused(`a link to a place in Sift with an anchor: ${href}`);
			return { kind: 'place', path: `/${parts.join('/')}` };
		}
		const slug = parts.join('/');
		if (!site.slugs.has(slug)) throw new DocRefused(`a link to a page that doesn't exist: ${href}`);
		return hash && hash !== '_top' ? { kind: 'page', slug, anchor: hash } : { kind: 'page', slug };
	};
}

/** What the menu below draws from the site's config: changed there, it must be taught here. */
const MENU_WORDS = [
	"label: 'Get started'",
	"label: 'Your library'",
	"label: 'Settings'",
	"slug: 'whats-new'",
	"slug: 'help'",
	"label: 'For developers'",
	"'developers/api'"
];

/** The docs site's own menu, in its order; a page it does not reach stops the build. */
export function menuOf(
	pages: Record<string, Ordered>,
	sidebar: Pick<Sidebar, 'library' | 'settings'>,
	config: string
): DocMenuItem[] {
	const changed = MENU_WORDS.find((word) => !config.includes(word));
	if (changed !== undefined) {
		throw new DocRefused(`the docs menu changed (${changed}): teach scripts/docs_pages.js`);
	}
	const entry = (slug: string): DocMenuItem => {
		const page = pages[slug];
		if (page === undefined) throw new DocRefused(`the menu names a page that isn't there: ${slug}`);
		return { slug, title: page.title };
	};
	const started = Object.values(pages)
		.filter((page) => page.slug.startsWith('get-started/'))
		.sort((a, b) => (a.order ?? Infinity) - (b.order ?? Infinity) || a.slug.localeCompare(b.slug));
	const menu: DocMenuItem[] = [
		{ label: 'Get started', items: started.map((page) => entry(page.slug)) },
		{ label: 'Your library', items: sidebar.library.map(entry) },
		{
			label: 'Settings',
			items: sidebar.settings.map((group) => ({
				label: group.label,
				items: group.items.map(entry)
			}))
		},
		entry('whats-new'),
		entry('help'),
		{ label: 'For developers', items: ['developers', 'developers/api'].map(entry) }
	];
	const reached = new Set<string>();
	const visit = (items: DocMenuItem[]): void =>
		items.forEach((item) => ('items' in item ? visit(item.items) : reached.add(item.slug)));
	visit(menu);
	const missed = Object.keys(pages).filter((slug) => slug !== '' && !reached.has(slug));
	if (missed.length > 0) throw new DocRefused(`pages the menu doesn't reach: ${missed.join(', ')}`);
	return menu;
}

/** Every page link's anchor is a heading or an anchor on the page it names. */
export function checkAnchors(pages: Record<string, DocPage>): void {
	const ids = new Map(
		Object.values(pages).map((page) => [page.slug, new Set(anchorsIn(page.blocks))])
	);
	for (const page of Object.values(pages)) {
		for (const to of linksIn(page.blocks)) {
			if (to.kind === 'page' && to.anchor && !ids.get(to.slug)?.has(to.anchor)) {
				throw new DocRefused(
					`${page.slug}: a link to #${to.anchor}, which ${to.slug} doesn't have`
				);
			}
		}
	}
}

function anchorsIn(blocks: DocBlock[]): string[] {
	return blocks.flatMap((block) => {
		if (block.kind === 'list') return block.items.flatMap((item) => item.anchors ?? []);
		return 'anchors' in block ? (block.anchors ?? []) : [];
	});
}

/** Every link on a page, wherever it is drawn. */
function linksIn(blocks: DocBlock[]): DocLink[] {
	const runs = blocks.flatMap((block): DocInline[] => {
		if (block.kind === 'list') return block.items.flatMap((item) => item.inline);
		if (block.kind === 'table') return [...block.head, ...block.rows.flat()].flat();
		return 'inline' in block ? block.inline : [];
	});
	return runs.flatMap((run) => (typeof run !== 'string' && run.kind === 'link' ? [run.to] : []));
}

/** Read the whole site into a book, and the screens it shows by file name. */
export function readSite(site: string): { book: DocBook; screens: Map<string, string> } {
	const files = pageFiles(join(site, 'src', 'content', 'docs'));
	const sidebar = JSON.parse(
		readFileSync(join(site, 'generated', 'sidebar.json'), 'utf-8')
	) as Sidebar;
	const config = readFileSync(join(site, 'astro.config.mjs'), 'utf-8');
	const discord = /const DISCORD = '([^']+)'/.exec(config)?.[1];
	if (discord === undefined) throw new DocRefused('astro.config.mjs no longer names the invite');
	const link = linker({ slugs: new Set(files.keys()), routes: sidebar.routes, discord });
	const shelf = resolve(site, 'src', 'assets', 'screens');
	const screens = new Map<string, string>();
	const pages: Record<string, Ordered> = {};
	for (const [slug, path] of files) {
		const screen = (src: string) => {
			const file = resolve(dirname(path), src);
			if (dirname(file) !== shelf || !existsSync(file)) {
				throw new DocRefused(`a picture that isn't one of the screens: ${src}`);
			}
			screens.set(basename(file), file);
			return basename(file);
		};
		try {
			const { title, order, body } = frontOf(readFileSync(path, 'utf-8'));
			pages[slug] = { slug, title, order, blocks: readBlocks(body, { link, screen }) };
		} catch (error) {
			throw error instanceof DocRefused ? new DocRefused(`${slug}: ${error.message}`) : error;
		}
	}
	checkAnchors(pages);
	const menu = menuOf(pages, sidebar, config);
	const { ['']: home, ...rest } = pages;
	if (home === undefined) throw new DocRefused('the docs have no front page');
	const book = Object.fromEntries(
		Object.entries(rest).map(([slug, page]) => [
			slug,
			{ slug, title: page.title, blocks: page.blocks }
		])
	);
	return { book: { home: home.blocks, menu, pages: book }, screens };
}

/** The module the pane loads: the screens as the bundler's own files, the book as one string. */
export function moduleOf(book: DocBook, names: string[]): string {
	const lines = [
		'// GENERATED by scripts/docs_pages.js from docs-site. Never edited.',
		"import type { DocBook } from '$lib/docs/read';",
		...names.map((name, at) => `import screen${at} from './screens/${name}';`),
		'export const SCREENS: Record<string, string> = {',
		...names.map((name, at) => `\t${JSON.stringify(name)}: screen${at},`),
		'};',
		`export const BOOK = JSON.parse(${JSON.stringify(JSON.stringify(book))}) as DocBook;`
	];
	return lines.join('\n') + '\n';
}
