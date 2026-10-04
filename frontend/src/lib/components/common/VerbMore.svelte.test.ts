/*
 * The door at the end of a selection bar is named for what it acts on, counted the way the bar
 * counts: never "More for this person" with two picked.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';
import VerbMore from './VerbMore.svelte';
import type { Verb } from './verbs';

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
});

const verbs: Verb[] = [{ id: 'hide', label: 'Hide', icon: 'visibility_off', run: () => {} }];

function labelFor(ids: string[], noun: string, plural?: string): string | null {
	const host = document.createElement('div');
	document.body.append(host);
	instance = mount(VerbMore, { target: host, props: { verbs, ids, noun, plural } });
	flushSync();
	return host.querySelector('button')?.getAttribute('aria-label') ?? null;
}

describe('the More door on a selection bar', () => {
	it('names the one thing when one is picked', () => {
		expect(labelFor(['a'], 'person', 'people')).toBe('More for this person');
	});

	it('counts what is picked when there are several, in the bar\u2019s own plural', () => {
		expect(labelFor(['a', 'b'], 'person', 'people')).toBe('More for 2 people');
	});

	it('adds an s where no plural is given', () => {
		expect(labelFor(['a', 'b', 'c'], 'file')).toBe('More for 3 files');
	});
});
