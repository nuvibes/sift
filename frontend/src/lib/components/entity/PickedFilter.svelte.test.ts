/* The control beside an entity page's tab words: nothing while nothing is picked, the count of the
 * picks, and the press that takes them all off in place. There is no press that spends them any
 * more: a pick filters the files the moment it is made (see `picks.ts`). */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { page } from '$app/state';
import { goto } from '$app/navigation';

import PickedFilter from './PickedFilter.svelte';

const address = page as unknown as { url: URL };
const moved = vi.mocked(goto);

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

function draw(path: string) {
	address.url = new URL(`http://localhost${path}`);
	drawn = mount(PickedFilter, { target: host, props: {} }) as Record<string, unknown>;
	flushSync();
}

beforeEach(() => {
	moved.mockClear();
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
	address.url = new URL('http://localhost/');
});

it('draws nothing while nothing is picked, nor for a typed exclusion', () => {
	draw('/people/p1?show=tags&people=-Jane');
	expect(host.querySelector('button')).toBeNull();
});

it('counts the picks and offers no second press to apply them', () => {
	draw('/people/p1?show=tags&people=Jane+Else&tags=beach');
	const buttons = [...host.querySelectorAll('button')];
	expect(buttons).toHaveLength(1);
	expect(buttons[0]?.querySelector('.count')?.textContent).toBe('2');
	expect(host.textContent).not.toContain('Filter files');
});

it('clears the picks in place, leaving a typed filter on the same field', () => {
	draw('/people/p1?show=tags&tags=beach&tags=-dunes');
	(host.querySelector('button') as HTMLButtonElement).click();
	expect(moved).toHaveBeenCalledWith('/people/p1?show=tags&tags=-dunes', {
		replaceState: true,
		keepFocus: true,
		noScroll: true
	});
});
