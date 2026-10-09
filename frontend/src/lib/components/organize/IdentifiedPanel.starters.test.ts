/*
 * People Sift can recognize, with the People known from starter pictures alone set apart.
 *
 * The control sits on the tab line where Unnamed faces keeps its small groups: one press shows
 * only them, the other everybody else, each with its count, and the narrowing is in the address so
 * a refresh and Back keep it. Pressing the half that is showing goes back to everybody.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({
	identifiedPeople: vi.fn(),
	at: { url: new URL('http://sift.test/organize/known-people') } as { url: URL }
}));

vi.mock('$app/state', () => ({
	page: {
		params: {},
		get url() {
			return mocks.at.url;
		},
		state: {}
	},
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));

vi.mock('$lib/people/faces.svelte', () => ({
	CROPS_ON_A_CARD: 12,
	IDENTIFIED_PEOPLE_PER_PAGE: 24,
	identifiedPeople: (...a: unknown[]) => mocks.identifiedPeople(...a),
	confirmMatches: vi.fn(),
	confirmLookAlikes: vi.fn(),
	rejectLookAlikes: vi.fn(),
	rejectMatches: vi.fn(),
	referenceStrengths: vi.fn().mockResolvedValue(null),
	recognitionOf: vi.fn(),
	referenceVerdict: () => 'good',
	cropUrl: (id: string) => `/api/faces/${id}/crop`,
	faceCoverUrl: (id: string) => `/api/faces/${id}/cover`,
	blankOnRefusal: () => {}
}));

vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: () => {} } }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: () => {} }));
vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: () => {}
}));

import Probe from './StartersProbe.test.svelte';

/* Invented for this file, as the rule for a fixture in this repo asks. */
const WREN = {
	person_id: 'person-1',
	person_name: 'Wren Halloway',
	size: 3,
	waiting: 3,
	matched: 0,
	confirmed: 0,
	surest: null,
	faces: [{ track_id: 'face-a', asset_id: 'asset-1', started_ms: 0, ended_ms: 0 }]
};

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

async function render(search: string, counts: { starters_only: number; others: number }) {
	mocks.at.url = new URL(`http://sift.test/organize/known-people${search}`);
	mocks.identifiedPeople.mockResolvedValue({ people: [WREN], total: 1, offset: 0, ...counts });
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Probe, { target: host }) as Record<string, unknown>;
	flushSync();
	for (let turn = 0; turn < 6; turn += 1) await tick();
	flushSync();
}

/** The control's two halves as drawn: words, where each goes, and which is showing. */
function halves(): [string, string | null, string | null][] {
	return [...host.querySelectorAll<HTMLAnchorElement>('.tabline a')].map((one) => [
		(one.textContent ?? '').replace(/\s+/g, ' ').trim(),
		one.getAttribute('href'),
		one.getAttribute('aria-current')
	]);
}

beforeEach(() => mocks.identifiedPeople.mockReset());

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
});

it('sets the starter pictures apart on the tab line, each half with its count', async () => {
	await render('', { starters_only: 329, others: 18 });

	expect(mocks.identifiedPeople).toHaveBeenCalledWith(
		expect.objectContaining({ limit: 24 }),
		null,
		''
	);
	expect(halves()).toEqual([
		['Starter pictures only 329', '/organize/known-people?starters=only', null],
		['Everyone else 18', '/organize/known-people?starters=without', null]
	]);
});

it('asks for the half the address names, and pressing it again goes back to everybody', async () => {
	await render('?starters=only', { starters_only: 329, others: 18 });

	expect(mocks.identifiedPeople).toHaveBeenCalledWith(expect.anything(), 'only', '');
	expect(halves()).toEqual([
		['Starter pictures only 329', '/organize/known-people', 'page'],
		['Everyone else 18', '/organize/known-people?starters=without', null]
	]);
});

it('draws no control with nobody to set apart and no narrowing to leave', async () => {
	await render('', { starters_only: 0, others: 1 });

	expect(halves()).toEqual([]);
});

it('puts the search box on the tab line, holding the words the address carries', async () => {
	await render('?who=wren', { starters_only: 0, others: 1 });

	// Narrowed on the server by name and alias, so the counts are of what the words found.
	expect(mocks.identifiedPeople).toHaveBeenCalledWith(expect.anything(), null, 'wren');
	const box = host.querySelector<HTMLInputElement>('.tabline input[type="search"]');
	expect(box?.value).toBe('wren');
	expect(box?.placeholder).toBe('Search people');
});
