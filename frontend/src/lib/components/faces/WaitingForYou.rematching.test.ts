// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The working mark after a Yes about somebody: on while the re-match the press asked for is queued
 * or running, off once the queue holds none, and off when the queue cannot be read. The queue's
 * answers are faked; the clock is the test's.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';

const queue = vi.hoisted(() => ({
	states: [] as string[],
	agreeing: [] as string[],
	fails: false
}));

vi.mock('$lib/api/client', async (importActual) => ({
	...(await importActual<typeof import('$lib/api/client')>()),
	api: {
		get: vi.fn(async (path: string, options: { query: { type: string } }) => {
			if (path !== '/jobs') throw new Error(`unexpected read of ${path}`);
			if (queue.fails) throw new Error('the queue is out of reach');
			const states = options.query.type === 'face_agree' ? queue.agreeing : queue.states;
			return { jobs: states.map((state, at) => ({ id: `j${at}`, state })), total: 0 };
		})
	}
}));

vi.mock('$lib/people/faces.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	identifiedForPerson: vi.fn(async () => ({
		items: [],
		offset: 0,
		total: 0,
		person_name: 'Marisol Vane',
		confirmed: 3,
		matched: 0,
		waiting: 0,
		unnamed_from_folder: 0
	}))
}));

import WaitingForYou, { agreeingWith, matchingSaid, rematching } from './WaitingForYou.svelte';

beforeEach(() => {
	vi.useFakeTimers();
	queue.states = ['queued'];
	queue.agreeing = [];
	queue.fails = false;
});

afterEach(() => {
	rematching.people.clear();
	vi.useRealTimers();
	document.body.innerHTML = '';
});

it('says whose face the rest of the library is matched against', () => {
	expect(matchingSaid('Marisol Vane')).toBe(
		"Sift is matching the rest of the library against Marisol's face"
	);
	expect(matchingSaid(null)).toBe('Sift is matching the rest of the library against their face');
});

it('marks her while a re-match is queued or running, and not once it has run', async () => {
	rematching.after('p1');
	expect(rematching.people.has('p1')).toBe(true);

	await vi.advanceTimersByTimeAsync(2000);
	expect(rematching.people.has('p1')).toBe(true);

	queue.states = ['running'];
	await vi.advanceTimersByTimeAsync(2000);
	expect(rematching.people.has('p1')).toBe(true);

	queue.states = ['done'];
	await vi.advanceTimersByTimeAsync(2000);
	expect(rematching.people.has('p1')).toBe(false);
});

it('keeps the mark while the agreeing task has still to run', async () => {
	queue.states = [];
	queue.agreeing = ['running'];
	rematching.after('p1');
	await vi.advanceTimersByTimeAsync(2000);
	expect(rematching.people.has('p1')).toBe(true);

	queue.agreeing = ['done'];
	await vi.advanceTimersByTimeAsync(2000);
	expect(rematching.people.has('p1')).toBe(false);
});

it('says how many faces a Yes handed over', () => {
	expect(agreeingWith(1)).toBe('Agreeing with 1 face');
	expect(agreeingWith(1204)).toBe('Agreeing with 1,204 faces');
});

it('drops the mark when the queue cannot be read', async () => {
	queue.fails = true;
	rematching.after('p1');
	await vi.advanceTimersByTimeAsync(2000);
	expect(rematching.people.has('p1')).toBe(false);
});

it('draws the mark on her page with the sentence', async () => {
	const host = document.createElement('div');
	document.body.append(host);
	mount(WaitingForYou, { target: host, props: { personId: 'p1', name: 'Marisol Vane' } });
	await vi.waitFor(() => {
		flushSync();
		expect(host.querySelector('.counts')).not.toBeNull();
	});
	expect(host.querySelector('.working')).toBeNull();

	rematching.after('p1');
	flushSync();

	expect(host.querySelector('.working [role="status"]')?.getAttribute('aria-label')).toBe(
		"Sift is matching the rest of the library against Marisol's face"
	);
});
