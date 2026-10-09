/* The supported list's Cookies column: the cookies sheet's own badge and sentence, per Site. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get }
}));

import SupportedSites from './SupportedSites.svelte';
import source from './SupportedSites.svelte?raw';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import { COPY } from './SupportedSites.search';

function site(key: string, name: string, cookies: string, why: string) {
	return {
		key,
		name,
		hosts: [`${key}.example`],
		media: ['video'],
		walls: [],
		bulk: true,
		tested: true,
		supported: true,
		names_creators: true,
		cookies,
		cookies_why: why,
		cookies_with_a_tool: null
	};
}

const SITES = [
	site('marchfield', 'Marchfield', 'required', 'Every post needs them.'),
	site('sunhollow', 'Sunhollow', 'partial', 'Age-restricted videos need cookies.'),
	site('tidewater', 'Tidewater', 'not_needed', 'Everything downloads without them.')
];

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.get.mockResolvedValue(SITES);
});

afterEach(() => {
	phoneWidth.yes = false;
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
});

async function render() {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(SupportedSites, { target: host, props: {} });
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
}

/** One Site's Cookies cell. */
const cellOf = (name: string) =>
	[...document.querySelectorAll('tbody tr')]
		.find((row) => row.querySelector('th')?.textContent?.trim() === name)
		?.querySelector('td.cookies') ?? null;

/* The badge's own word: its text nodes, without the icon's ligature beside them. */
const badgeWord = (cell: Element | null) =>
	(cell?.querySelector('.badge .word, .badge .said')?.textContent ?? '')
		.replace(/\s+/g, ' ')
		.trim();

describe('the Cookies column', () => {
	it("says each Site's need in the sheet's word and colour, with the Site's own sentence", async () => {
		await render();

		const march = cellOf('Marchfield');
		expect(badgeWord(march)).toBe('Required');
		expect(march?.querySelector('.badge')?.classList.contains('state-failed')).toBe(true);
		expect(march?.textContent).toContain('Every post needs them.');

		const sun = cellOf('Sunhollow');
		expect(badgeWord(sun)).toBe('Partial');
		expect(sun?.querySelector('.badge')?.classList.contains('state-blocked')).toBe(true);
		expect(sun?.textContent).toContain('Age-restricted videos need cookies.');

		const tide = cellOf('Tidewater');
		expect(badgeWord(tide)).toBe('Not required');
		expect(tide?.querySelector('.badge')?.classList.contains('state-queued')).toBe(true);
	});
});

/* The table is folded under words that say how many Sites are behind them, in the pane's one
   fold shape, and the legend's sentences start in one column. */
it('folds the table under a count of Sites, as the one fold shape', async () => {
	await render();

	const fold = document.querySelector('details.fold');
	expect(fold?.querySelector('summary')?.textContent).toBe(`Show all ${SITES.length} Sites`);
	expect(document.querySelector('details.matrix')).toBeNull();
});

/* On a phone the table would scroll sideways inside the pane (650 pixels wide at 393), so each
   Site is a card there: the same words, the name and then each column's heading beside its answer. */
describe('at a phone width', () => {
	it('draws one card per Site and no table', async () => {
		phoneWidth.yes = true;
		await render();

		expect(document.querySelector('table'), 'the wide table is still drawn').toBeNull();
		const cards = [...document.querySelectorAll('.site-card')];
		expect(cards.map((card) => card.querySelector('.site-name')?.textContent?.trim())).toEqual([
			'Marchfield',
			'Sunhollow',
			'Tidewater'
		]);
	});

	it("says every column's words on each card, and the same answers as the row", async () => {
		phoneWidth.yes = true;
		await render();

		const card = [...document.querySelectorAll('.site-card')].find((one) =>
			one.textContent?.includes('Sunhollow')
		)!;
		const headings = [...card.querySelectorAll('dt')].map((one) => one.textContent?.trim());
		expect(headings).toEqual([
			COPY.columns.support,
			COPY.columns.addresses,
			COPY.columns.bulk,
			COPY.columns.cookies,
			COPY.columns.issues
		]);
		const answer = (heading: string) =>
			[...card.querySelectorAll('dl > div')].find(
				(row) => row.querySelector('dt')?.textContent?.trim() === heading
			)!;
		expect(answer(COPY.columns.addresses).textContent).toContain('sunhollow.example');
		expect(badgeWord(answer(COPY.columns.cookies))).toBe('Partial');
		expect(answer(COPY.columns.cookies).textContent).toContain(
			'Age-restricted videos need cookies.'
		);
		expect(badgeWord(answer(COPY.columns.support))).toBe(COPY.supported);
	});

	it('keeps the table on a wide window', async () => {
		await render();

		expect(document.querySelectorAll('tbody tr')).toHaveLength(SITES.length);
		expect(document.querySelector('.site-card')).toBeNull();
	});

	it('lays the card out so nothing in it runs past its edge', () => {
		const card = source.slice(source.indexOf('.site-facts {'));
		expect(card).toMatch(/^[^}]*grid-template-columns: max-content minmax\(0, 1fr\);/);
		expect(source).toMatch(/\.site-facts \.hosts \{\s*overflow-wrap: anywhere;/);
	});
});
