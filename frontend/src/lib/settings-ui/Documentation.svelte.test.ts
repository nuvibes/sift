/* Settings > Documentation, drawn from the pages the build wrote. */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { replaceState } from '$app/navigation';

import Documentation from './Documentation.svelte';
import { COPY } from './Documentation.search';
import { BOOK, SCREENS } from '$lib/generated/docs/pages';
import type { DocBlock, DocLink, DocMenuItem } from '$lib/docs/read';
import { copySectionLink } from '$lib/docs/link';

vi.mock('$lib/docs/link', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/docs/link')>()),
	copySectionLink: vi.fn(async () => true)
}));

let host: HTMLDivElement;
let shown: ReturnType<typeof mount> | undefined;

function close() {
	if (shown) unmount(shown);
	shown = undefined;
	host?.remove();
}

async function open(show?: string): Promise<HTMLDivElement> {
	close();
	window.history.replaceState({}, '', show ? `/settings/documentation?show=${show}` : '/');
	host = document.createElement('div');
	document.body.append(host);
	shown = mount(Documentation, { target: host });
	await vi.waitFor(() => expect(host.querySelector('.docs')?.childElementCount).toBeGreaterThan(0));
	flushSync();
	return host;
}

const scrolled = vi.fn();

beforeAll(() => {
	Element.prototype.scrollIntoView = scrolled;
});

afterEach(() => {
	close();
	window.history.replaceState({}, '', '/');
});

/** The first page with a block like this. */
function withBlock(like: (block: DocBlock) => boolean): string {
	const found = Object.values(BOOK.pages).find((page) => page.blocks.some(like));
	if (!found) throw new Error('no page has such a block');
	return found.slug;
}

/** The first page with a link like this, and the link. */
function withLink(like: (link: DocLink) => boolean): [string, DocLink] {
	for (const page of Object.values(BOOK.pages)) {
		const runs = page.blocks.flatMap((block) =>
			block.kind === 'list'
				? block.items.flatMap((item) => item.inline)
				: 'inline' in block
					? block.inline
					: []
		);
		for (const run of runs) {
			if (typeof run !== 'string' && run.kind === 'link' && like(run.to))
				return [page.slug, run.to];
		}
	}
	throw new Error('no page has such a link');
}

const slugs = (items: DocMenuItem[]): string[] =>
	items.flatMap((item) => ('items' in item ? slugs(item.items) : [item.slug]));

describe('the contents', () => {
	it("lists every page in the docs' own menu, under its groups", async () => {
		const pane = await open();
		const links = [...pane.querySelectorAll<HTMLAnchorElement>('.doc-menu a')];
		expect(links.map((one) => new URL(one.href).searchParams.get('show'))).toEqual(
			slugs(BOOK.menu)
		);
		expect(links.length).toBe(Object.keys(BOOK.pages).length);
		const headings = [...pane.querySelectorAll('.doc-menu .doc-heading')].map(
			(one) => one.textContent
		);
		expect(headings).toContain('Get started');
		expect(headings).toContain('Settings');
	});

	it('opens a page in place on a plain click, and goes back to the contents', async () => {
		const pane = await open();
		const first = pane.querySelector<HTMLAnchorElement>('.doc-menu a') as HTMLAnchorElement;
		const slug = new URL(first.href).searchParams.get('show') as string;
		first.click();
		flushSync();
		await vi.waitFor(() =>
			expect(pane.querySelector('.doc-title')?.textContent).toBe(BOOK.pages[slug]?.title)
		);
		expect(replaceState).toHaveBeenLastCalledWith(
			`/settings/documentation?show=${encodeURIComponent(slug)}`,
			expect.anything()
		);
		(pane.querySelector('button') as HTMLButtonElement).click();
		flushSync();
		await vi.waitFor(() => expect(pane.querySelector('.doc-menu')).not.toBeNull());
	});

	it('brings the heading a link names into view', async () => {
		const [from, to] = withLink((link) => link.kind === 'page' && link.anchor !== undefined);
		const pane = await open(from);
		const across = [...pane.querySelectorAll<HTMLAnchorElement>('.docs a')].find(
			(one) => new URL(one.href).searchParams.get('show') === (to as { slug: string }).slug
		) as HTMLAnchorElement;
		scrolled.mockClear();
		across.click();
		await vi.waitFor(() => expect(scrolled).toHaveBeenCalled());
		expect(pane.querySelector('.doc-title')?.textContent).toBe(
			BOOK.pages[(to as { slug: string }).slug]?.title
		);
		expect((scrolled.mock.contexts.at(-1) as HTMLElement).id).toBe(
			`doc-${(to as { anchor: string }).anchor}`
		);
	});

	it('says so when the address names a page this version does not have', async () => {
		const pane = await open('library/nothing-here');
		expect(pane.textContent).toContain(COPY.missing);
		expect(pane.querySelector('.doc-menu')).not.toBeNull();
	});
});

describe('a page', () => {
	it.each(Object.keys(BOOK.pages))('draws %s with its title and every block', async (slug) => {
		const pane = await open(slug);
		expect(pane.querySelector('.doc-title')?.textContent).toBe(BOOK.pages[slug]?.title);
		expect(pane.querySelector('.docs')?.childElementCount).toBeGreaterThan(
			BOOK.pages[slug]?.blocks.length ?? 0
		);
	});

	it('shows the screens copied from the site, with their words', async () => {
		const slug = withBlock((block) => block.kind === 'image');
		const picture = (await open(slug)).querySelector('img.screen') as HTMLImageElement;
		const block = BOOK.pages[slug]?.blocks.find((one) => one.kind === 'image');
		expect(picture.getAttribute('src')).toBe(SCREENS[(block as { screen: string }).screen]);
		expect(picture.alt).toBe((block as { alt: string }).alt);
	});

	it('opens a setting in the panel, a screen in Sift, and the web in the browser', async () => {
		const [settingPage] = withLink((link) => link.kind === 'setting');
		const setting = (await open(settingPage)).querySelector(
			'a[href^="/settings/"]:not([href^="/settings/documentation"])'
		);
		expect(setting?.classList.contains('setting-link')).toBe(true);
		const [outsidePage] = withLink((link) => link.kind === 'outside');
		const outside = (await open(outsidePage)).querySelector<HTMLAnchorElement>(
			'a[href^="https://"]'
		);
		expect(outside?.target).toBe('_blank');
		expect(outside?.rel).toContain('external');
		const [placePage, place] = withLink((link) => link.kind === 'place');
		const there = (await open(placePage)).querySelector<HTMLAnchorElement>(
			`a[href="${(place as { path: string }).path}"]`
		);
		expect(there?.target).toBe('');
	});

	it('ends each heading in a press that copies the address at that heading', async () => {
		const slug = withBlock((block) => block.kind === 'heading');
		const headings = [...(await open(slug)).querySelectorAll<HTMLElement>('.docs > .doc-heading')];
		expect(headings.length).toBeGreaterThan(0);
		for (const heading of headings) {
			expect(heading.querySelector('button[aria-label="Copy link"]')).not.toBeNull();
		}
		const last = headings.at(-1) as HTMLElement;
		(last.querySelector('button') as HTMLButtonElement).click();
		expect(copySectionLink).toHaveBeenCalledWith(
			`${location.origin}/settings/documentation?show=${encodeURIComponent(slug)}#${last.id}`
		);
	});

	it("draws the headings as elements, a level under the pane's title", async () => {
		const tags = (pane: Element, selector: string) =>
			[...pane.querySelectorAll(selector)].map((one) => one.tagName);
		const contents = await open();
		expect(new Set(tags(contents, '.doc-menu .doc-heading'))).toEqual(new Set(['H2', 'H3']));
		const slug = withBlock((block) => block.kind === 'heading' && block.level === 3);
		const page = await open(slug);
		expect(page.querySelector('.doc-title')?.tagName).toBe('H2');
		const levels = BOOK.pages[slug]?.blocks.flatMap((one) =>
			one.kind === 'heading' ? [`H${one.level + 1}`] : []
		);
		expect(tags(page, '.docs > .doc-heading')).toEqual(levels);
	});

	it('draws a table, a note and an anchor as the page writes them', async () => {
		const table = withBlock((block) => block.kind === 'table');
		expect((await open(table)).querySelectorAll('table tbody tr').length).toBeGreaterThan(0);
		expect(
			(await open(withBlock((block) => block.kind === 'note'))).querySelector('.doc-note')
		).not.toBeNull();
		const anchored = withBlock(
			(block) => block.kind === 'list' && Boolean(block.items[0]?.anchors)
		);
		const block = BOOK.pages[anchored]?.blocks.find(
			(one) => one.kind === 'list' && one.items[0]?.anchors
		);
		const id = (block as { items: { anchors: string[] }[] }).items[0]?.anchors[0];
		expect((await open(anchored)).querySelector(`[id="doc-${id}"]`)).not.toBeNull();
	});
});
