/* A ranked list's covers when the list is handed new rows in place. */
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import type { components } from '$lib/api/schema';
import NamedListProbe from './NamedListProbe.test.svelte';

type List = components['schemas']['NamedList'];
type Piece = components['schemas']['HistoryPiece'];

function thing(kind: string, id: string, text: string): Piece {
	return { text, kind, id, href: null, gone: false, rest: [], lead: '' };
}

function list(id: string, name: string): List {
	return {
		title: 'Tags',
		rows: [
			{
				piece: thing('tag', id, name),
				value: 60_000,
				unit: 'ms',
				cover: `/api/tags/${id}/cover`,
				said: ''
			}
		]
	};
}

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
});

describe('a cover handed a new address', () => {
	it('is a new picture, not the old one asked about another address', () => {
		host = document.createElement('div');
		document.body.append(host);
		const probe = mount(NamedListProbe, { target: host, props: { first: list('t1', 'Harbour') } });
		drawn = probe;
		flushSync();

		const before = host.querySelector('[data-avatar-root]');
		expect(before).not.toBeNull();

		(probe as unknown as { show: (next: List) => void }).show(list('t2', 'Lantern'));
		flushSync();

		const after = host.querySelector('[data-avatar-root]');
		expect(after).not.toBeNull();
		expect(after).not.toBe(before);
		expect(after?.querySelector('img')?.getAttribute('src')).toBe('/api/tags/t2/cover');
	});
});
