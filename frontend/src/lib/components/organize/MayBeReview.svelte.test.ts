/* One group that may be somebody, face by face: what each answer on the page sends.
 *
 * The page adds no door of its own. A Yes is the card's own `confirmGroups` with the faces it is
 * about; a No on one face is `rejectFace`; the No for the whole group is the card's `rejectGroups`
 * for this group alone. These pin which door each press uses and with what.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { words } from '$lib/design/testing.svelte';

const mocks = vi.hoisted(() => ({
	faceGroup: vi.fn(),
	confirmGroups: vi.fn(),
	rejectFace: vi.fn(),
	rejectGroups: vi.fn(),
	one: vi.fn(),
	goto: vi.fn(),
	decided: vi.fn()
}));

vi.mock('$app/navigation', () => ({ goto: mocks.goto, pushState: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/state', () => ({
	page: {
		params: { person: 'person-1', pile: 'pile-1' },
		url: new URL('http://sift.test/organize/may-be/person-1/pile-1'),
		state: {}
	},
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));
vi.mock('$lib/people/faces.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	faceGroup: mocks.faceGroup,
	confirmGroups: mocks.confirmGroups,
	rejectFace: mocks.rejectFace,
	rejectGroups: mocks.rejectGroups
}));
vi.mock('$lib/people/people.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	people: { one: mocks.one }
}));
vi.mock('$lib/organize/organize.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	decided: mocks.decided,
	heldBoard: { found: null, ensure: async () => {}, refresh: async () => null }
}));

import MayBeReview from './MayBeReview.svelte';

function face(id: string) {
	return {
		track_id: id,
		asset_id: `asset-${id}`,
		started_ms: 0,
		ended_ms: 0,
		picture_ms: 0,
		person_id: null,
		attribution: null
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	for (const one of Object.values(mocks)) one.mockReset();
	mocks.one.mockResolvedValue({ id: 'person-1', name: 'Wren Halloway' });
	mocks.faceGroup.mockResolvedValue({
		group: {
			id: 'pile-1',
			size: 3,
			status: 'unidentified',
			faces: [face('f1'), face('f2'), face('f3')]
		},
		offset: 0,
		total: 3
	});
	mocks.confirmGroups.mockResolvedValue({ changed: 1, offered: 2, decision_id: 'receipt-1' });
	mocks.rejectFace.mockResolvedValue(undefined);
	mocks.rejectGroups.mockResolvedValue({ changed: 3, decision_id: 'receipt-2' });
});

afterEach(() => {
	if (drawn) void unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
});

async function render(): Promise<void> {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(MayBeReview, { target: host }) as Record<string, unknown>;
	flushSync();
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();
}

function button(label: string): HTMLButtonElement {
	const found = host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
	if (!found) throw new Error(`no button named ${label}`);
	return found;
}

async function settle(): Promise<void> {
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();
}

it('names the group by its size and who it may be, with a Yes and a No on every face', async () => {
	await render();

	expect(mocks.faceGroup).toHaveBeenCalledWith('pile-1', { limit: 60, offset: 0 });
	expect(words(host.querySelector('.page-header h1'))).toBe('3 faces that may be Wren Halloway');
	expect(host.querySelectorAll('.faces li')).toHaveLength(3);
	expect(
		host.querySelectorAll('button[aria-label="Yes, this face is Wren Halloway"]')
	).toHaveLength(3);
	expect(
		host.querySelectorAll(`button[aria-label="No, this face isn't Wren Halloway"]`)
	).toHaveLength(3);
});

it('says No about one face through the one-face door, and that face leaves the page', async () => {
	await render();

	button("No, this face isn't Wren Halloway").click();
	await settle();

	expect(mocks.rejectFace).toHaveBeenCalledWith('f1', 'person-1');
	expect(host.querySelectorAll('.faces li')).toHaveLength(2);
});

it("says Yes about one face through the card's own door, then carries on in her review", async () => {
	await render();

	button('Yes, this face is Wren Halloway').click();
	await settle();

	expect(mocks.confirmGroups).toHaveBeenCalledWith('person-1', ['pile-1'], ['f1']);
	expect(mocks.decided).toHaveBeenCalledWith(
		'One face is named. 2 more are waiting for your answer',
		'receipt-1'
	);
	expect(mocks.goto).toHaveBeenCalledWith(
		'/organize/known-people/person-1?show=suggested&via=faces'
	);
});

it('says Yes about every face on the page in one press', async () => {
	await render();

	const lead = host.querySelector<HTMLButtonElement>('.answers .lead button');
	expect(words(lead)).toBe('Yes, all 3');
	lead?.click();
	await settle();

	expect(mocks.confirmGroups).toHaveBeenCalledWith('person-1', ['pile-1'], ['f1', 'f2', 'f3']);
});
