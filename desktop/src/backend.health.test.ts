/* Waiting for the backend to be ready by polling `/health`, and a start that never comes good
 * ending with why: the exit code and the end of its own log. `fetch` and the log read are stood in
 * for here, so the port check beside it keeps real sockets. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/* Read rather than started. Nothing here spawns anything (`waitForHealth` is handed a child that
   is already there, or none at all), but the module reaches for `spawn` at import. */
vi.mock('node:child_process', () => ({
	spawn: vi.fn()
}));

/* Only the log read is stood in for. The rest of `fs` is real, and `backendLogFile` still decides
   which file is meant: what is under test is what the shell SAYS when a start fails, and that
   sentence is the end of that file. */
const logText = { value: null as string | null };
vi.mock('node:fs', async (original) => {
	const real = await original<typeof import('node:fs')>();
	return {
		...real,
		readFileSync: (...args: unknown[]) => {
			if (logText.value === null) throw new Error('no such file');
			return logText.value;
		}
	};
});

import { Backend, BackendStartError } from './backend';
import { backendLogFile } from './paths';

/** The failure a wait ended in. It throws rather than answering nothing when the wait SUCCEEDED,
 *  so a test that expected a failure says that, instead of reading a property off nothing. */
function failed(waiting: Promise<void>): Promise<InstanceType<typeof BackendStartError>> {
	return waiting.then(
		() => {
			throw new Error('this wait was expected to fail, and it did not');
		},
		(err: unknown) => err as InstanceType<typeof BackendStartError>
	);
}

/** A backend with a child already under it, which is the state `start` leaves before it waits. */
function waiting(): { backend: InstanceType<typeof Backend>; wait: () => Promise<void> } {
	const backend = new Backend({ dataDir: 'D:\\data', cacheDir: 'D:\\cache' }, () => {});
	(backend as unknown as { child: unknown }).child = { pid: 1 };
	return {
		backend,
		wait: () => (backend as unknown as { waitForHealth(): Promise<void> }).waitForHealth()
	};
}

const asked: string[] = [];
let answers: (Response | Error)[] = [];

beforeEach(() => {
	asked.length = 0;
	answers = [];
	logText.value = null;
	vi.stubGlobal('fetch', async (url: string) => {
		asked.push(url);
		/* The last answer repeats rather than running out: a test says what the backend keeps
		   answering, and a queue that emptied would put a failure of the test's own making at the
		   end of the shell's sentence. */
		const next = answers.length > 1 ? answers.shift() : answers[0];
		if (next instanceof Error) throw next;
		if (next === undefined) throw new Error('nothing is listening yet');
		return next;
	});
});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

/** An answer from `/health`, as the shell reads it: the status and nothing else. */
function answered(status: number): Response {
	return { ok: status >= 200 && status < 300, status } as Response;
}

describe('waiting for the backend to be ready', () => {
	it('returns the moment /health answers, and asks the loopback address', async () => {
		const { wait } = waiting();
		answers = [answered(200)];

		await expect(wait()).resolves.toBeUndefined();

		expect(asked).toEqual(['http://127.0.0.1:5171/health']);
	});

	/* The whole reason it polls. A refused connection while the interpreter is still importing, and
	   a 503 from a backend that is up but not ready, are both ordinary states on the way to ready,
	   and neither is a failure to report. */
	it('keeps asking through a refusal and a not-ready answer', async () => {
		vi.useFakeTimers();
		const { wait } = waiting();
		answers = [new Error('connection refused'), answered(503), answered(200)];

		const waited = wait();
		await vi.advanceTimersByTimeAsync(1_000);

		await expect(waited).resolves.toBeUndefined();
		expect(asked).toHaveLength(3);
	});

	/* A backend that died while starting is the case where waiting for the deadline would be ninety
	   seconds of a window with nothing in it, for an answer that is already known. */
	it('stops waiting immediately when the backend has gone, and says its last words', async () => {
		logText.value = 'sift.main: the port was taken\n';
		const { backend, wait } = waiting();
		(backend as unknown as { child: unknown }).child = null;

		await expect(wait()).rejects.toThrow(BackendStartError);
		expect(asked).toEqual([]);
	});

	it('gives up after the deadline, and carries both the last failure and the log', async () => {
		vi.useFakeTimers();
		logText.value = 'sift.main: something went wrong at the end\n';
		const { wait } = waiting();
		answers = [answered(500)];

		const waited = failed(wait());
		await vi.advanceTimersByTimeAsync(95_000);

		const failure = await waited;
		expect(failure).toBeInstanceOf(BackendStartError);
		expect(failure.message).toContain('never became ready');
		// What went wrong last, so a wrong status is not reported as silence.
		expect(failure.detail).toContain('500');
		expect(failure.detail).toContain('something went wrong at the end');
	});
});

describe('the end of the backend own log', () => {
	it('is the last few lines rather than the whole file', async () => {
		logText.value = Array.from({ length: 200 }, (_, each) => `line ${each}`).join('\n');
		const { backend, wait } = waiting();
		(backend as unknown as { child: unknown }).child = null;

		const failure = await failed(wait());

		expect(failure.detail).toContain('line 199');
		expect(failure.detail).not.toContain('line 174');
		expect(failure.detail.split('\n\n')[1]?.split('\n')).toHaveLength(25);
	});

	it('opens with the exit code of the start that died', async () => {
		logText.value = 'the last line\n';
		const { backend, wait } = waiting();
		(backend as unknown as { child: unknown }).child = null;
		(backend as unknown as { lastExit: number }).lastExit = 1;

		const failure = await failed(wait());

		expect(failure.crashed).toBe(true);
		expect(failure.code).toBe(1);
		expect(failure.detail.split('\n')[0]).toBe('It stopped with exit code 1.');
		expect(failure.detail).toContain('the last line');
	});

	/* A start that failed before the backend wrote anything at all. Naming the file is the whole of
	   what can be said, and it is worth saying: the alternative is a failure with no detail under
	   it, which reads as the shell having nothing to report rather than the log being empty. */
	it('names the file it looked in when there is nothing in it', async () => {
		logText.value = null;
		const { backend, wait } = waiting();
		(backend as unknown as { child: unknown }).child = null;

		const failure = await failed(wait());

		expect(failure.detail).toContain(backendLogFile());
		expect(failure.detail).toContain('nothing was captured');
	});
});
