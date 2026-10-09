/* What a press of Run now tells somebody: where to follow the run it started. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { wordsOf, type ToastWords } from '$lib/components/common/toast-pieces';

const mocks = vi.hoisted(() => ({
	answer: {} as Record<string, unknown>,
	quiet: {} as Record<string, unknown>,
	byPath: {} as Record<string, Record<string, unknown>>,
	shown: [] as ToastWords[]
}));

vi.mock('$lib/api/client', () => ({
	api: {
		post: vi.fn(async (path: string) => mocks.byPath[path] ?? mocks.answer),
		get: vi.fn(async () => ({ quiet_hours: mocks.quiet, tasks: [], folders: [] }))
	},
	ApiError: class extends Error {}
}));

vi.mock('$lib/shell/toasts.svelte', () => ({
	toasts: { show: vi.fn((said: ToastWords) => mocks.shown.push(said)) }
}));

import { pressTask, pressTasks } from './tasks.svelte';
import { COPY } from '$lib/settings-ui/ScheduledTasks.search';

function answered(onActivity: boolean): Record<string, unknown> {
	return {
		job_ids: ['01HX0000000000000000000001'],
		starts_at: null,
		named: null,
		dry: false,
		on_activity: onActivity
	};
}

beforeEach(() => {
	mocks.shown = [];
	mocks.byPath = {};
});

describe('the toast after Run now', () => {
	it('sends somebody to Activity for work Activity draws', async () => {
		mocks.answer = answered(true);
		await pressTask('duplicates', 'now');
		expect(mocks.shown.map(wordsOf)).toEqual(['Started. Follow it in Activity.']);
		// Activity is the way to it, not a word.
		expect(mocks.shown[0]).toContainEqual(
			expect.objectContaining({ href: '/settings/tasks?show=now' })
		);
	});

	it("points at the task's own row for upkeep Activity does not list", async () => {
		mocks.answer = answered(false);
		await pressTask('backup', 'now');
		expect(mocks.shown).toEqual([COPY.when.startedHere]);
	});
});

describe('a press of several tasks', () => {
	it('says one sentence: the one that started, not the one that found nothing', async () => {
		mocks.answer = answered(true);
		mocks.byPath['/tasks/generate/run'] = { ...answered(true), job_ids: [] };
		await pressTasks([{ task: 'generate', parts: ['fingerprints'] }, { task: 'music' }], 'now');
		expect(mocks.shown.map(wordsOf)).toEqual(['Started. Follow it in Activity.']);
	});

	it('says nothing was to run once when none of them found any', async () => {
		mocks.answer = { ...answered(true), job_ids: [] };
		await pressTasks([{ task: 'generate' }, { task: 'music' }], 'now');
		expect(mocks.shown).toEqual([COPY.when.nothingToDo]);
	});
});

describe('the toast after Run during quiet hours', () => {
	it('says the start the way the quiet hours row writes it, when the run waits for the range', async () => {
		const { clock } = await import('$lib/shell/clock.svelte');
		const { taskList } = await import('./tasks.svelte');
		clock.take('12');
		const opens = Math.floor(Date.now() / 1000) + 3600;
		mocks.quiet = { starts: '06:00', ends: '06:30', open: false, opens_at: opens };
		await taskList.load();
		mocks.answer = { ...answered(true), starts_at: opens, waits: true };
		await pressTask('shoots', 'quiet');
		expect(mocks.shown.at(-1)).toBe(COPY.when.startsAt('6 AM'));
	});

	it('says the range is on now when the server says the run does not wait, whatever this clock reads', async () => {
		// A start a minute ahead of this browser's clock is two clocks apart, not a run that waits.
		mocks.answer = {
			...answered(true),
			starts_at: Math.floor(Date.now() / 1000) + 60,
			waits: false
		};
		await pressTask('suggestions', 'quiet');
		expect(mocks.shown.at(-1)).toBe(COPY.when.startsNow);
	});
});

describe('what a When is called', () => {
	const quiet = { starts: '23:00', ends: '07:00' };

	it('says During quiet hours with the range, on the twelve-hour clock the short way', async () => {
		const { clock } = await import('$lib/shell/clock.svelte');
		const { whenLabel } = await import('./tasks.svelte');
		clock.take('12');
		expect(whenLabel('quiet', 'During quiet hours', quiet)).toBe(
			'During quiet hours (11 PM to 7 AM)'
		);
		// Minutes that are not nought are kept.
		expect(whenLabel('quiet', 'During quiet hours', { starts: '22:30', ends: '06:00' })).toBe(
			'During quiet hours (10:30 PM to 6 AM)'
		);
	});

	it('says the range on the twenty-four-hour clock with its minutes', async () => {
		const { clock } = await import('$lib/shell/clock.svelte');
		const { whenLabel } = await import('./tasks.svelte');
		clock.take('24');
		try {
			expect(whenLabel('quiet', 'During quiet hours', quiet)).toBe(
				'During quiet hours (23:00 to 07:00)'
			);
		} finally {
			clock.take('12');
		}
	});

	it("leaves every other When, and a When with no quiet hours in hand, in the server's words", async () => {
		const { whenLabel } = await import('./tasks.svelte');
		expect(whenLabel('work', 'As files arrive', quiet)).toBe('As files arrive');
		expect(whenLabel('quiet', 'During quiet hours', null)).toBe('During quiet hours');
	});
});
