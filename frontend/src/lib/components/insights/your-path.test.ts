/* The small celebration: one line for a goal found reached since the last read this session, and
 * nothing on the first read (or every goal reached a month ago would be announced). */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { celebrate, forgetPathSession, pathProgress } from './your-path';

vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

function goal(id: string, done: boolean) {
	return { id, title: `Goal ${id}`, help: [], done, done_at: null, href: '/' };
}

function paths(...goals: ReturnType<typeof goal>[]) {
	return { paths: [{ id: 'one', title: 'One', sentence: [], goals }] };
}

beforeEach(() => forgetPathSession());

describe('celebrate', () => {
	it('says nothing on the first read of a session', () => {
		expect(celebrate(paths(goal('a', true)))).toEqual([]);
	});

	it('says one line for each goal newly reached, and nothing for the rest', () => {
		celebrate(paths(goal('a', true), goal('b', false)));
		expect(celebrate(paths(goal('a', true), goal('b', true)))).toEqual(['Goal reached: Goal b']);
		expect(celebrate(paths(goal('a', true), goal('b', true)))).toEqual([]);
	});
});

describe('the words around the sentences', () => {
	it('says how far along a path is plainly', () => {
		expect(pathProgress({ goals: [goal('a', true), goal('b', false)] })).toBe('1 of 2 done');
		expect(pathProgress({ goals: [] })).toBe('0 of 0 done');
	});
});
