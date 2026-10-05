/* What the benchmark Sift runs by itself on the first library folder says, and the listener that
   says it from the jobs bell in every admin window. */
import { describe, expect, it, vi } from 'vitest';

const opened = vi.hoisted(() => ({ calls: [] as unknown[][] }));
vi.mock('$lib/settings-ui/settings-view', () => ({
	openSettings: (...args: unknown[]) => opened.calls.push(args)
}));

import {
	BenchmarkToasts,
	benchmarkToasts,
	type BenchmarkToast,
	type FirstBenchmarkRead
} from './toasts-benchmark.svelte';
import { jobChanges } from '$lib/library/changes.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

const RUNNING =
	'Benchmarking this device so Sift can make the best use of it. It takes up to 5 minutes.';
const SET = 'Sift set 2 settings from the benchmark.';
const FAILED =
	"Sift couldn't benchmark this device because the video encoder couldn't be run. You can run it from Settings > Performance.";

const GAVE_WAY =
	"Sift stopped the full benchmark because this device is in use. It runs again by itself once Sift has nothing else to do and nobody's using this device.";

const HELD = 'Your folder is added, and its files appear once this device has been benchmarked.';

const run = (state: string, said: string | null, measured = false): FirstBenchmarkRead => ({
	state,
	job_id: 'job-1',
	said,
	measured,
	held: state === 'waiting' || state === 'running' ? HELD : null
});

describe('the rule', () => {
	it('says a run going once, with Open, and keeps it up while it runs', () => {
		expect(benchmarkToasts(run('running', RUNNING), new Set())).toEqual([
			{ key: 'job-1:start', message: RUNNING, tone: 'info', press: 'open', going: true }
		]);
		expect(benchmarkToasts(run('running', RUNNING), new Set(['job-1:start']))).toEqual([]);
	});

	it('says what it set with Review, and why it could not run with Open', () => {
		expect(benchmarkToasts(run('set', SET), new Set(['job-1:start']))).toEqual([
			{ key: 'job-1:end', message: SET, tone: 'success', press: 'review', going: false }
		]);
		expect(benchmarkToasts(run('failed', FAILED), new Set())).toEqual([
			{ key: 'job-1:end', message: FAILED, tone: 'error', press: 'open', going: false }
		]);
	});

	it('says a run Sift stopped for other work as a notice, with nothing to review', () => {
		expect(benchmarkToasts(run('gave_way', GAVE_WAY), new Set(['job-1:start']))).toEqual([
			{ key: 'job-1:end', message: GAVE_WAY, tone: 'info', press: null, going: false }
		]);
	});

	it('says nothing where no run was queued, or a pressed run got there first', () => {
		expect(
			benchmarkToasts(
				{ state: 'none', job_id: null, said: null, measured: false, held: null },
				new Set()
			)
		).toEqual([]);
		expect(benchmarkToasts(run('already', 'benchmarked already'), new Set())).toEqual([]);
	});
});

describe('the listener', () => {
	function listening(reads: FirstBenchmarkRead[]) {
		const said: BenchmarkToast[] = [];
		const unsaid: number[] = [];
		let at = 0;
		const listener = new BenchmarkToasts(
			async () => reads[Math.min(at++, reads.length - 1)],
			(toast) => {
				said.push(toast);
				return said.length;
			},
			(id) => unsaid.push(id)
		);
		return { listener, said, unsaid, reads: () => at };
	}

	it('remembers on the first read, then says the start and the end from the bell', async () => {
		const { listener, said, unsaid } = listening([
			{ state: 'none', job_id: null, said: null, measured: false, held: null },
			run('running', RUNNING),
			run('running', RUNNING),
			run('set', SET, true)
		]);
		await listener.look();
		expect(said).toEqual([]);
		await listener.look();
		await listener.look();
		expect(said.map((one) => one.message)).toEqual([RUNNING]);
		await listener.look();
		expect(said.map((one) => one.message)).toEqual([RUNNING, SET]);
		// The end takes the place of the running toast.
		expect(unsaid).toEqual([1]);
	});

	it('says nothing about a run that ended before the tab opened', async () => {
		const { listener, said } = listening([run('set', SET, true)]);
		await listener.look();
		await listener.look();
		expect(said).toEqual([]);
	});

	it('stops asking once the device is measured and nothing is going', async () => {
		const { listener, reads } = listening([
			{ state: 'none', job_id: null, said: null, measured: true, held: null }
		]);
		await listener.look();
		await listener.look();
		await listener.look();
		expect(reads()).toBe(1);
	});

	it('follows the jobs bell, and the press lands on the benchmark row', async () => {
		const reads: FirstBenchmarkRead[] = [
			{ state: 'none', job_id: null, said: null, measured: false, held: null },
			run('failed', FAILED)
		];
		let at = 0;
		const listener = new BenchmarkToasts(async () => reads[Math.min(at++, reads.length - 1)]);
		listener.follow(true);
		await vi.waitFor(() => expect(at).toBe(1));
		jobChanges.changed();
		await vi.waitFor(() => expect(toasts.items.some((one) => one.message === FAILED)).toBe(true));
		const shown = toasts.items.find((one) => one.message === FAILED);
		expect(shown?.tone).toBe('error');
		expect(shown?.action?.label).toBe('Open');
		shown?.action?.run();
		expect(opened.calls).toEqual([['performance', 'performance.benchmark']]);
	});

	it('shows a run Sift stopped for other work without a press', async () => {
		const reads = [run('running', RUNNING), run('gave_way', GAVE_WAY)];
		let at = 0;
		const listener = new BenchmarkToasts(async () => reads[Math.min(at++, reads.length - 1)]);
		await listener.look();
		await listener.look();
		const shown = toasts.items.find((one) => one.message === GAVE_WAY);
		expect(shown?.tone).toBe('info');
		expect(shown?.action).toBeUndefined();
	});

	it('holds the folder sentence while the run goes, the first read included, and drops it after', async () => {
		const { listener } = listening([run('running', RUNNING), run('set', SET, true)]);
		await listener.look();
		expect(listener.held).toBe(HELD);
		await listener.look();
		expect(listener.held).toBeNull();
	});
});
