// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The marks on a person's faces: one family, one glyph at most on a crop. None on a face somebody
 * confirmed, the question on one waiting for an answer, and Sift's own on one Sift named, whose
 * tooltip says when Sift also identifies others by it. The server's answers are faked.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const at = vi.hoisted(() => ({ url: new URL('http://localhost/organize/known-people/person-1') }));

vi.mock('$app/state', () => ({
	page: {
		get url() {
			return at.url;
		},
		params: { id: 'person-1' },
		state: {}
	},
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: () => {}, pushState: () => {} }));
vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: () => {}
}));
/* Each tab holds one plain face and one Sift identifies others by. */
function face(id: string, attribution: string, reference: boolean) {
	return {
		track_id: id,
		asset_id: `asset-${id}`,
		started_ms: 0,
		ended_ms: 0,
		art: 'a',
		person_id: 'person-1',
		person_name: 'Wren Halloway',
		confidence: null,
		attribution,
		is_reference: reference
	};
}

vi.mock('$lib/api/client', async (importActual) => ({
	...(await importActual<typeof import('$lib/api/client')>()),
	api: {
		get: vi.fn(async (path: string, options?: { query?: { attribution?: string } }) => {
			if (!path.startsWith('/faces/identified/people/')) return { jobs: [] };
			const kind = options?.query?.attribution ?? 'suggested';
			return {
				items: [face(`${kind}-plain`, kind, false), face(`${kind}-reference`, kind, true)],
				total: 2,
				offset: 0,
				person_name: 'Wren Halloway',
				waiting: 2,
				confirmed: 2,
				matched: 2
			};
		}),
		post: vi.fn(async () => ({})),
		put: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	}
}));

import IdentifiedForPerson from './IdentifiedForPerson.svelte';
import codepoints from '$lib/generated/icon-codepoints.json';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	document.body.innerHTML = '';
});

const glyph = (name: keyof typeof codepoints) =>
	String.fromCodePoint(parseInt(codepoints[name], 16));

/** Each crop's marks on a tab, as the glyphs drawn. */
async function marksOn(show: string): Promise<string[][]> {
	at.url = new URL(`http://localhost/organize/known-people/person-1?show=${show}`);
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(IdentifiedForPerson, { target: host }) as Record<string, unknown>;
	await vi.waitFor(() => {
		flushSync();
		expect(host.querySelectorAll('ul.faces > li')).toHaveLength(2);
	});
	return [...host.querySelectorAll('ul.faces > li')].map((crop) =>
		[...crop.querySelectorAll('.face-mark')].map(
			(mark) => mark.querySelector('.icon')?.textContent ?? ''
		)
	);
}

/** What a crop's mark says when pointed at: decorative, so it is read off its tooltip. */
async function saysOn(crop: number): Promise<string> {
	document.querySelectorAll('[role="tooltip"]').forEach((bubble) => bubble.remove());
	const mark = host.querySelectorAll('ul.faces > li')[crop].querySelector('.face-mark');
	mark?.closest('.wrap')?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
	let words = '';
	await vi.waitFor(() => {
		flushSync();
		words = document.querySelector('[role="tooltip"]')?.textContent ?? '';
		expect(words).not.toBe('');
	});
	mark?.closest('.wrap')?.dispatchEvent(new PointerEvent('pointerleave'));
	return words;
}

it('draws nothing on a face somebody confirmed, reference or not', async () => {
	expect(await marksOn('confirmed')).toEqual([[], []]);
});

it('draws the question alone on a face waiting for an answer', async () => {
	const marks = await marksOn('suggested');
	expect(marks).toEqual([[glyph('help')], [glyph('help')]]);
});

it("draws Sift's one glyph on a face Sift named, a reference or not, and never the wand", async () => {
	const marks = await marksOn('matched');
	expect(
		marks.map((crop) => crop.length),
		'two glyphs on one crop'
	).toEqual([1, 1]);
	expect(marks.flat()).toEqual([glyph('familiar_face_and_zone'), glyph('familiar_face_and_zone')]);
	expect(host.innerHTML).not.toContain(glyph('auto_fix_high'));
});

it('says a reference only in the words of its one glyph', async () => {
	await marksOn('matched');
	expect(await saysOn(0)).toBe('Recognized by Sift');
	expect(await saysOn(1)).toBe('Sift recognized Wren in this one and uses it to identify them');
});
