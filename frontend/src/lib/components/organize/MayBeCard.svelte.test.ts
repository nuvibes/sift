/* "These groups may be her": the card, its ticks, and what each answer sends.
 *
 * The card is the one place a whole GROUP is asked about (the server's `ToCheckKind.MAY_BE`). What
 * is pinned here is what the two answers are ABOUT: Yes is the ticked groups and the faces the card
 * showed of them (the faces the server confirms) and No is every group on the card.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { words as readable } from '$lib/design/testing.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), pushState: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/state', () => ({
	page: { params: {}, url: new URL('http://sift.test/organize/faces'), state: {} },
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));

import MayBeCard from './MayBeCard.svelte';
import type { components } from '$lib/api/schema';

type MayBeFace = components['schemas']['MayBeGroup']['faces'][number];

/* The four fields the card draws of a face; the wire carries fourteen, so the stand-in is cast. */
function face(id: string): MayBeFace {
	return {
		track_id: id,
		asset_id: `asset-${id}`,
		started_ms: 0,
		ended_ms: 0
	} as unknown as MayBeFace;
}

/* Invented for this file, as the rule for a fixture in this repo asks. */
function card() {
	return {
		kind: 'may_be' as const,
		id: 'person-1',
		size: 12,
		person_name: 'Wren Halloway',
		best: 0.58,
		faces: [],
		person_id: null,
		source: null,
		status: null,
		groups: [
			{
				pile_id: 'close',
				size: 47,
				likeness: 0.58,
				ticked: true,
				faces: [face('c1'), face('c2')],
				reasons: [
					{
						kind: 'likeness',
						folder_id: null,
						folder_name: null,
						in_folder: null,
						group_files: null,
						box_names: [] as string[]
					}
				]
			},
			{
				pile_id: 'folder',
				size: 12,
				likeness: 0.2,
				ticked: true,
				faces: [face('f1')],
				reasons: [
					{
						kind: 'folder',
						folder_id: 'dir-1',
						folder_name: 'Wren Halloway',
						in_folder: 41,
						group_files: 47,
						box_names: [] as string[]
					}
				]
			},
			{
				pile_id: 'doubtful',
				size: 6,
				likeness: 0.37,
				ticked: false,
				faces: [face('d1')],
				reasons: [
					{
						kind: 'likeness',
						folder_id: null,
						folder_name: null,
						in_folder: null,
						group_files: null,
						box_names: [] as string[]
					}
				]
			}
		]
	};
}

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	// The menu is portalled to the end of the document, so removing the host leaves it behind.
	document.body.innerHTML = '';
});

function render(onyes = vi.fn(), onno = vi.fn(), drawn = card()) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(MayBeCard, {
		target: host,
		props: { card: drawn, busy: false, answering: false, onyes, onno }
	}) as Record<string, unknown>;
	flushSync();
	return { onyes, onno };
}

function rows(): HTMLButtonElement[] {
	return [...host.querySelectorAll<HTMLButtonElement>('.group button[aria-pressed]')];
}

function yes(): HTMLButtonElement {
	const lead = host.querySelector<HTMLButtonElement>('.answers .lead button');
	if (!lead) throw new Error('the card has no Yes');
	return lead;
}

it('asks about every group, closest first, with the ticks the server started them at', () => {
	render();

	expect(readable(host.querySelector('h3') as HTMLElement)).toBe(
		'These 3 groups may be Wren Halloway'
	);
	expect(rows().map((row) => readable(row))).toEqual([
		'47 faces \u00b7 58%',
		'12 faces',
		'6 faces \u00b7 37%'
	]);
	expect(rows().map((row) => row.getAttribute('aria-pressed'))).toEqual(['true', 'true', 'false']);
	// The folder's sentence, said once for the card, and the group's own share of it under it.
	expect(host.textContent).toContain(
		'Some of these files are in the folder Wren Halloway, which Sift added to Wren Halloway.'
	);
	expect(host.querySelector('.fact')?.textContent).toBe('41 of its 47 files are in Wren Halloway');
});

it('says each reason once, however many groups it is true of', () => {
	/* The same sentence under every group would be the card repeating itself; a group row carries only
	   its own numbers. */
	const drawn = card();
	const starter = {
		kind: 'stash-box',
		folder_id: null,
		folder_name: null,
		in_folder: null,
		group_files: null,
		box_names: [] as string[]
	};
	drawn.groups = drawn.groups.map((group) => ({ ...group, reasons: [starter] }));
	render(vi.fn(), vi.fn(), drawn);

	expect(host.querySelectorAll('.why')).toHaveLength(1);
	expect(host.querySelector('.why')?.textContent).toBe(
		"Compared with a stash-box's pictures of Wren Halloway, not with faces you confirmed."
	);
});

it("opens each group's own review, face by face, and offers the closest one's in its menu", async () => {
	const { goto } = await import('$app/navigation');
	render();

	const links = [...host.querySelectorAll<HTMLAnchorElement>('.group a.faces')];
	expect(links.map((one) => one.getAttribute('href'))).toEqual([
		'/organize/may-be/person-1/close',
		'/organize/may-be/person-1/folder',
		'/organize/may-be/person-1/doubtful'
	]);

	const door = host.querySelector<HTMLButtonElement>('.answers .trail button');
	door?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	flushSync();
	const review = [...document.querySelectorAll<HTMLElement>('.ui-menu [role="menuitem"]')].find(
		(one) => readable(one) === 'Review each face'
	);
	review?.click();

	expect(goto).toHaveBeenCalledWith('/organize/may-be/person-1/close');
});

it('says Yes about the ticked groups and the faces it showed of them, and no others', () => {
	const { onyes } = render();

	rows()[0].click();
	flushSync();
	yes().click();

	expect(onyes).toHaveBeenCalledWith(['folder'], ['f1']);
});

it('cannot say Yes about nothing', () => {
	render();

	rows()[0].click();
	rows()[1].click();
	flushSync();

	expect(yes().disabled).toBe(true);
});

it('says No about every group on the card, ticked or not', () => {
	const { onno } = render();

	const door = host.querySelector<HTMLButtonElement>('.answers .trail button');
	if (!door) throw new Error('the card has no menu half');
	door.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	flushSync();
	const no = [...document.querySelectorAll<HTMLElement>('.ui-menu [role="menuitem"]')].find(
		(one) => readable(one) === 'No'
	);
	if (!no) throw new Error('no row reading No');
	no.click();

	expect(onno).toHaveBeenCalledWith(['close', 'folder', 'doubtful']);
});

it("says a group was compared with a stash-box's pictures, with the percentage it came to", () => {
	/*
	 * Somebody known by starter pictures alone: the likeness is to the box's pictures of her,
	 * weaker evidence than her own faces, and the card says so under the group.
	 */
	const drawn = card();
	drawn.groups = [
		{
			...drawn.groups[0],
			reasons: [
				{
					kind: 'stash-box',
					folder_id: null,
					folder_name: null,
					in_folder: null,
					group_files: null,
					box_names: ['Northlight']
				}
			]
		}
	];
	render(vi.fn(), vi.fn(), drawn);

	expect(rows().map((row) => readable(row))).toEqual(['47 faces \u00b7 58%']);
	expect(host.querySelector('.why')?.textContent).toBe(
		"Compared with Northlight's pictures of Wren Halloway, not with faces you confirmed."
	);
});

it('names every stash-box the pictures came from, once each', () => {
	const drawn = card();
	const from = (box_names: string[]) => ({
		kind: 'stash-box',
		folder_id: null,
		folder_name: null,
		in_folder: null,
		group_files: null,
		box_names
	});
	drawn.groups = drawn.groups.map((group, index) => ({
		...group,
		reasons: [from(index === 0 ? ['Southwind', 'Northlight'] : ['Northlight'])]
	}));
	render(vi.fn(), vi.fn(), drawn);

	expect(host.querySelectorAll('.why')).toHaveLength(1);
	expect(host.querySelector('.why')?.textContent).toBe(
		"Compared with Northlight's and Southwind's pictures of Wren Halloway, not with faces you confirmed."
	);
});

it('says what to do with one group in words that fit one', () => {
	const drawn = card();
	drawn.groups = [drawn.groups[0]];
	render(vi.fn(), vi.fn(), drawn);

	expect(readable(host.querySelector('h3') as HTMLElement)).toBe('This group may be Wren Halloway');
	expect(readable(host.querySelector('.detail') as HTMLElement)).toBe(
		"Deselect it if it isn't Wren Halloway."
	);
});
