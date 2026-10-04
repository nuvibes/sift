/* The facts under the name: the other names somebody goes by, and where they can be found.
 *
 * Two claims are worth holding here and neither is about layout.
 *
 * The first is the split, from the other side: this draws what the server marked as belonging on
 * the record, and only that. Together with the panel's own test that is the whole registry, once,
 * with nothing in both places, which is the property that lets a field move between the two by
 * changing one word on the server.
 *
 * The second is what the links carry. They are the only thing on an entity page that leaves this
 * install, and they are drawn as a site's own mark with no words on them, so what a screen reader
 * is given, and what the browser is told not to send, are the whole of what makes them usable and
 * safe. Neither is visible on the screen, so neither is noticed if it goes.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { FieldDescription } from '$lib/entity/records.svelte';
import { flushSync, mount, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({
	all: [] as FieldDescription[]
}));

/* A real registry with its one request replaced, rather than an object shaped like one. Which
   fields a record draws, and which sit behind the switch, are then answered by the class under
   test's own rules: an object retyping them here is a second copy that drifts, and a filter added
   to the registry would leave such a double answering that it did not exist. */
vi.mock('$lib/entity/records.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/entity/records.svelte')>();
	class Standing extends real.Fields {
		override of(): FieldDescription[] {
			return mocks.all;
		}
	}
	return { ...real, fields: new Standing() };
});

/* The clipboard, answered: pressing an other name copies it through the one helper every copy in
   the app goes through. */
const copied = vi.hoisted(() => ({ text: [] as string[] }));
vi.mock('$lib/shell/clipboard', () => ({
	copyText: (text: string) => {
		copied.text.push(text);
		return Promise.resolve(true);
	}
}));

import RecordSummary from './RecordSummary.svelte';

function field(over: Partial<FieldDescription> = {}): FieldDescription {
	return {
		key: 'aliases',
		subject: 'person',
		label: 'Aliases',
		kind: 'names',
		shown: 'record',
		group: 'record',
		editable: true,
		links_to: null,
		help: null,
		imported: false,
		suggests: null,
		entry: null,
		ordered: false,
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

beforeEach(() => {
	mocks.all = [];
});

function draw(values: Record<string, unknown>): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(RecordSummary, {
		target: host,
		props: { subject: 'person', values, label: 'Jane at a glance' }
	}) as Record<string, unknown>;
	flushSync();
	return host;
}

it('draws only what the server marked as belonging on the record', () => {
	mocks.all = [
		field(),
		field({ key: 'details', label: 'Details', kind: 'paragraph', shown: 'more' })
	];

	draw({ aliases: ['Janet'], details: 'a note' });

	expect(host.textContent).toContain('Janet');
	// The panel beside the name draws this one. Here as well it is on screen twice.
	expect(host.textContent).not.toContain('a note');
});

it('names the row of other names without printing a word over it', () => {
	mocks.all = [field()];

	draw({ aliases: ['Janet', 'Jan'] });

	const row = host.querySelector('ul') as HTMLElement;
	// The label is on the row for a screen reader and nowhere on the screen: two chips under a
	// heading already read as other names, and "Aliases" over them is furniture.
	expect(row.getAttribute('aria-label')).toBe('Aliases');
	expect(host.querySelectorAll('li')).toHaveLength(2);
	expect(host.textContent).not.toContain('Aliases');
});

it('reads an alias out of the shape a page happens to hold it in', () => {
	mocks.all = [field()];

	draw({ aliases: [{ alias: 'Janet' }, 'Jan'] });

	expect((host.textContent ?? '').replace(/\s+/g, ' ')).toContain('Janet Jan');
});

it('copies an other name when its chip is pressed', async () => {
	mocks.all = [field()];
	copied.text = [];

	draw({ aliases: ['Janet', 'Jan'] });
	const chips = host.querySelectorAll<HTMLButtonElement>('li.alias button');
	expect(chips).toHaveLength(2);
	chips[1].click();
	await vi.waitFor(() => expect(copied.text).toEqual(['Jan']));
});

it('draws nothing for a list nobody has filled in', () => {
	mocks.all = [field()];

	draw({ aliases: [] });

	// Not an empty row and not a dash: under a heading, a row with nothing in it is furniture.
	expect(host.querySelector('ul')).toBeNull();
});

it('draws nothing at all when the record has no facts on it', () => {
	mocks.all = [field({ shown: 'more' })];

	draw({ aliases: ['Janet'] });

	expect(host.querySelector('.summary')).toBeNull();
});

it('opens a link in a new page without handing over where it came from', () => {
	mocks.all = [field({ key: 'links', label: 'Links', kind: 'links' })];

	draw({ links: [{ url: 'https://example.test/jane', site_name: null }] });

	const link = host.querySelector('a') as HTMLAnchorElement;
	expect(link.getAttribute('target')).toBe('_blank');
	expect(link.getAttribute('rel')).toContain('noopener');
	// This install's address is not sent to the site it links out to.
	expect(link.getAttribute('rel')).toContain('noreferrer');
});

it('gives a wordless link a name to be read out', () => {
	mocks.all = [field({ key: 'links', label: 'Links', kind: 'links' })];

	draw({
		links: [
			{ url: 'https://www.example.test/jane?utm=1', site_name: null },
			{ url: 'https://other.test/jane', site_name: 'Other' }
		]
	});

	const links = [...host.querySelectorAll('a')].map((one) => one.getAttribute('aria-label'));
	// The site when it is known; otherwise the host, without the www and without the tracking
	// parameter: a screen reader reading out a query string is worse than one reading nothing.
	expect(links).toEqual(['example.test', 'Other']);
});

/*
 * The site's mark on every link the pack knows: the row reads `linkMarks`, the one answer the
 * record panel reads too (the pack's logo by host, then the plain glyph), so links with no site
 * filed behind them still show whose they are.
 */
it("draws each link with the pack's mark for its host, the glyph where the pack has none", () => {
	mocks.all = [field({ key: 'links', label: 'Links', kind: 'links' })];

	draw({
		links: [
			{ url: 'https://other.test/jane', site_name: 'Other' },
			{ url: 'https://third.test/jane', site_name: null }
		]
	});

	const first = (): HTMLImageElement | null => host.querySelectorAll('li')[0].querySelector('img');
	const second = (): HTMLImageElement | null => host.querySelectorAll('li')[1].querySelector('img');
	expect(first()?.getAttribute('src')).toBe('/api/sites/icons/for?host=other.test');
	expect(second()?.getAttribute('src')).toBe('/api/sites/icons/for?host=third.test');

	// The pack knows neither host: both have nothing left, so each is the plain link glyph, and
	// still a link.
	first()?.dispatchEvent(new Event('error'));
	second()?.dispatchEvent(new Event('error'));
	flushSync();
	expect(first()).toBeNull();
	expect(host.querySelectorAll('li')[0].querySelector('.icon')).not.toBeNull();
	expect(second()).toBeNull();
	expect(host.querySelectorAll('li')[1].querySelector('.icon')).not.toBeNull();
	expect(host.querySelectorAll('a')).toHaveLength(2);
});

it('leaves a fact that needs its label to the facts beside the name', () => {
	// The two above are unlabelled because their shapes say what they are. A bare value under a
	// heading is a fact nobody can name, so a labelled fact is drawn by RecordFacts, beside the
	// name, and never here.
	mocks.all = [field({ key: 'born', label: 'Born', kind: 'date' })];

	draw({ born: '2021-01-01T12:00:00Z' });

	expect(host.textContent).not.toContain('Born');
	expect(host.textContent).not.toContain('2021');
});
