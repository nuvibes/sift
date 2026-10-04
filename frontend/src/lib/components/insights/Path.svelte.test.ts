/* Get to know Sift's learning paths: each path's name, sentence and progress, a goal reached drawn
 * with its date, a goal to do with its help, and nothing that counts what was missed. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { dayOf } from '$lib/shell/when';
import Path from './Path.svelte';
import { forgetPathSession, type PathAnswer } from './your-path';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), show: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	return { ...real, api: { ...(real.api as object), get: mocks.get, post: mocks.post } };
});
vi.mock('$lib/shell/toasts.svelte', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	return { ...real, toasts: { show: mocks.show } };
});

const NAMED = 1_758_100_000;

function step(id: string, done: boolean, over: Record<string, unknown> = {}) {
	return {
		id,
		title: `Step ${id}`,
		help: [
			{ text: `Help for ${id}.`, kind: null, id: null, href: null, gone: false, rest: [], lead: '' }
		],
		done,
		done_at: done ? NAMED : null,
		href: `/somewhere/${id}`,
		...over
	};
}

function piece(text: string) {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '' };
}

function learning(id: string, goals: ReturnType<typeof step>[]) {
	return { id, title: `Path ${id}`, sentence: [piece(`What ${id} teaches.`)], goals };
}

function path(over: Partial<PathAnswer> = {}): PathAnswer {
	return {
		paths: [
			learning('watch', [step('view_something', true), step('keep_filter', false)]),
			learning('organize', [step('name_face', false)])
		],
		hints: [],
		...over
	} as PathAnswer;
}

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | null = null;

async function settle() {
	for (let i = 0; i < 6; i += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

async function render() {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Path, { target: host });
	await settle();
	return host;
}

beforeEach(() => {
	forgetPathSession();
	mocks.get.mockReset();
	mocks.post.mockReset();
	mocks.show.mockReset();
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

describe('Get to know Sift', () => {
	it('draws every path with its name, its sentence and how far along it is', async () => {
		mocks.get.mockResolvedValue(path());
		const page = await render();
		expect(mocks.get).toHaveBeenCalledWith('/insights/path');
		const paths = [...page.querySelectorAll('section.path')];
		expect(paths).toHaveLength(2);
		expect(paths[0].querySelector('h2, h3, [role="heading"]')?.textContent?.trim()).toBe(
			'Path watch'
		);
		expect(paths[0].querySelector('.sentence')?.textContent).toContain('What watch teaches.');
		expect(paths[0].querySelector('.count')?.textContent).toBe('1 of 2 done');
		expect(paths[1].querySelector('.count')?.textContent).toBe('0 of 1 done');
	});

	it('draws a goal reached with the day the record says, and a goal to do with its help', async () => {
		mocks.get.mockResolvedValue(path());
		const page = await render();
		const rows = [...page.querySelectorAll('section.path')[0].querySelectorAll('.steps li')];
		expect(rows).toHaveLength(2);
		expect(rows[0].textContent).toContain('Step view_something');
		expect(rows[0].querySelector('.when')?.textContent).toBe(dayOf(NAMED));
		expect(rows[0].textContent).not.toContain('Help for view_something');
		expect(rows[1].querySelector('.when')).toBeNull();
		expect(rows[1].textContent).toContain('Help for keep_filter.');
		expect(rows[1].querySelector('a')?.getAttribute('href')).toBe('/somewhere/keep_filter');
	});

	it('says nothing that shames: no streak, no weekly chore, nothing missed', async () => {
		mocks.get.mockResolvedValue(path());
		const page = await render();
		expect(page.querySelector('.streak, .quests')).toBeNull();
		expect(page.textContent ?? '').not.toMatch(
			/\b(lose|losing|lost|miss|missed|break|streak|this week|don't|keep it)\b/i
		);
	});

	it('says one quiet line when the paths cannot be read', async () => {
		mocks.get.mockRejectedValue(new Error('down'));
		const page = await render();
		expect(page.textContent).toContain("Get to know Sift couldn't be loaded.");
		expect(page.querySelector('section.path')).toBeNull();
	});
});
