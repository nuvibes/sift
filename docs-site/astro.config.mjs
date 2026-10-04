// SPDX-License-Identifier: AGPL-3.0-or-later
// The docs site. Its menu follows the screen's order (generated/sidebar.json, written from the
// client's rail and settings panes); a link to a place in Sift lands on the page describing it.
import { copyFileSync, readFileSync, readdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { satteri } from '@astrojs/markdown-satteri';

const menu = JSON.parse(
	readFileSync(new URL('./generated/sidebar.json', import.meta.url), 'utf-8')
);

// GitHub Pages serves a project site under the repository's name; the workflow says where.
const site = process.env.SIFT_DOCS_SITE ?? 'https://nuvibes.github.io';
const base = process.env.SIFT_DOCS_BASE ?? '/sift';

// Sift's Discord invite, written here once: a page links to it as [words]({{discord}}).
const DISCORD = 'https://discord.gg/VjHQDA3kSw';

// The site's name is the app's own lockup (src/components/SiteTitle.astro), taken from the
// component the app draws it with, so the mark has one source. A Wordmark.svelte that no longer
// holds one <svg> stops the build rather than drawing a header with no name.
function lockup() {
	const source = readFileSync(
		new URL('../frontend/src/lib/brand/Wordmark.svelte', import.meta.url),
		'utf-8'
	);
	const svg = source.match(/<svg\b[\s\S]*<\/svg>/)?.[0];
	if (!svg || !svg.includes('style:height=')) {
		throw new Error('frontend/src/lib/brand/Wordmark.svelte no longer holds the lockup <svg>');
	}
	return svg.replace(/\s*style:height="[^"]*"/, ' aria-hidden="true"');
}

// A font ships with its licence text beside it, as the app's do (frontend/scripts/prepare_fonts.js):
// each font file the build wrote gets its package's LICENSE, and a font from no known package, or a
// package whose LICENSE is not the Open Font License, stops the build.
const FONT_PACKAGES = { archivo: '@fontsource-variable/archivo' };
const require = createRequire(import.meta.url);

const fontLicences = {
	name: 'sift-font-licences',
	hooks: {
		'astro:build:done': ({ dir }) => {
			const out = join(fileURLToPath(dir), '_astro');
			const families = new Set(
				readdirSync(out)
					.filter((name) => name.endsWith('.woff2'))
					.map((name) => name.split('-')[0])
			);
			for (const family of families) {
				const pkg = FONT_PACKAGES[family];
				if (!pkg) throw new Error(`${family}: a font with no package to take its licence from`);
				const licence = join(dirname(require.resolve(`${pkg}/package.json`)), 'LICENSE');
				if (!readFileSync(licence, 'utf-8').includes('SIL Open Font License')) {
					throw new Error(`${pkg}: its LICENSE does not read as the SIL Open Font License`);
				}
				copyFileSync(licence, join(out, `${family}-LICENSE.txt`));
			}
			// The tab's icon is the app's own (frontend/static/brand), beside the lockup in the header.
			copyFileSync(
				new URL('../frontend/static/brand/favicon.svg', import.meta.url),
				join(fileURLToPath(dir), 'favicon.svg')
			);
		}
	}
};

// A link to a place in Sift (`/browse`, `/organize/faces`) opens that place when Sift serves the
// page, and the page describing it here. `/settings/<pane>#<key>` is the reference row as written.
function inSite(url) {
	if (!url.startsWith('/') || url.startsWith('//')) return url;
	const [path, hash] = url.split('#');
	const first = '/' + (path.split('/')[1] ?? '');
	const page = menu.routes[first] ?? path;
	const slash = page.endsWith('/') ? page : `${page}/`;
	return `${base}${slash}${hash ? '#' + hash : ''}`;
}

const siftLinks = {
	name: 'sift-links',
	link(node, ctx) {
		const url = node.url === '{{discord}}' ? DISCORD : inSite(node.url);
		ctx.replaceNode(node, { ...node, url });
	}
};

const pages = (items) => items.map((slug) => ({ slug }));

export default defineConfig({
	site,
	base,
	markdown: { processor: satteri({ mdastPlugins: [siftLinks] }) },
	vite: { define: { __SIFT_LOCKUP__: JSON.stringify(lockup()) } },
	integrations: [
		starlight({
			title: 'Sift',
			description: 'How to use Sift, the self-hosted media library and downloader.',
			customCss: ['@fontsource-variable/archivo/wght.css', './src/styles/sift.css'],
			components: {
				Hero: './src/components/Hero.astro',
				SiteTitle: './src/components/SiteTitle.astro',
				ThemeProvider: './src/components/ThemeProvider.astro',
				ThemeSelect: './src/components/ThemeSelect.astro'
			},
			social: [
				{ icon: 'github', label: 'GitHub', href: 'https://github.com/nuvibes/sift' },
				{ icon: 'discord', label: 'Discord', href: DISCORD }
			],
			editLink: { baseUrl: 'https://github.com/nuvibes/sift/edit/main/docs-site/' },
			lastUpdated: false,
			sidebar: [
				{ label: 'Get started', items: [{ autogenerate: { directory: 'get-started' } }] },
				{ label: 'Your library', items: pages(menu.library) },
				{
					label: 'Settings',
					items: menu.settings.map((group) => ({ label: group.label, items: pages(group.items) }))
				},
				{ slug: 'whats-new' },
				{ slug: 'help' },
				{ label: 'For developers', items: pages(['developers', 'developers/api']) }
			]
		}),
		fontLicences
	]
});
