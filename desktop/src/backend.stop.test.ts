/* Stopping and restarting the backend, with `node:child_process` mocked: `child.kill()` on Windows
 * is TerminateProcess, so closing stdin is how a stop is asked for. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const spawned = {
	stdin: { end: vi.fn() },
	stdout: { on: vi.fn() },
	stderr: { on: vi.fn() },
	kill: vi.fn(),
	once: vi.fn(),
	on: vi.fn(),
	exited: null as ((code?: number) => void) | null
};

vi.mock('node:child_process', () => ({
	spawn: vi.fn(() => spawned)
}));

/* Whether the bundled interpreter is "there". Answered here rather than read off the disk: these
   tests spawn nothing real, and a suite that passed only on a machine with a development venv in
   place would be a statement about that machine. */
const interpreter = vi.hoisted(() => ({ present: true }));

vi.mock('node:fs', async (original) => {
	const real = await original<typeof import('node:fs')>();
	return {
		...real,
		existsSync: () => interpreter.present,
		openSync: () => 3,
		closeSync: () => {},
		mkdirSync: () => undefined
	};
});

/* What the shell's own log was told, level and event and fields, in order. */
const said = vi.hoisted(() => [] as [string, string, Record<string, unknown>][]);
vi.mock('./log', () => ({
	log: Object.fromEntries(
		['debug', 'info', 'warning', 'error'].map((level) => [
			level,
			(event: string, fields: Record<string, unknown> = {}) => said.push([level, event, fields])
		])
	)
}));

const starts = vi.hoisted(() => ({ written: [] as string[], closed: 0, facts: [] as unknown[][] }));
vi.mock('./backendlog', () => ({
	openStart: () => ({
		start: 7,
		write: (chunk: Buffer) => starts.written.push(chunk.toString()),
		close: () => {
			starts.closed += 1;
		}
	})
}));
vi.mock('./facts', () => ({
	writeFacts: (...args: unknown[]) => starts.facts.push(args)
}));

const { ASKED_TO_RESTART, Backend, DRAIN_MS } = await import('./backend');
const { spawn } = await import('node:child_process');
const { backendLogFile } = await import('./paths');
const path = await import('node:path');

function started(): InstanceType<typeof Backend> {
	const backend = new Backend({ dataDir: 'D:\\data', cacheDir: 'D:\\cache' }, () => {});
	// The private field is filled by `start`, which also waits for health. Set directly: what is
	// under test is `stop`, and a health wait would be a second thing to stand up.
	(backend as unknown as { child: unknown }).child = spawned;
	return backend;
}

/* Whether the fake child answers a stop by exiting, or sits there. Both are real: a backend that
   goes when asked, and one that has to be taken. Default off, so a test that wants the timeout gets
   it by doing nothing. */
let goesWhenAsked = false;

beforeEach(() => {
	spawned.stdin.end.mockClear();
	spawned.kill.mockClear();
	spawned.once.mockImplementation((event: string, run: (code?: number) => void) => {
		if (event !== 'exit') return;
		spawned.exited = run;
		if (goesWhenAsked) queueMicrotask(run);
	});
	spawned.exited = null;
	goesWhenAsked = false;
	interpreter.present = true;
});

afterEach(() => {
	vi.useRealTimers();
});

describe('stopping', () => {
	it('closes stdin, which is how the backend is ASKED rather than taken', async () => {
		const backend = started();

		const stopping = backend.stop();
		spawned.exited?.();
		await stopping;

		expect(spawned.stdin.end).toHaveBeenCalledOnce();
		expect(spawned.kill).not.toHaveBeenCalled();
	});

	/* The fallback, and the only case a hard stop is right: a backend that has stopped answering.
	 * A shell that hung on quit would be a worse outcome than a log that replays on the next start. */
	it('takes it after the timeout when it will not go on its own', async () => {
		vi.useFakeTimers();
		const backend = started();

		const stopping = backend.stop();
		/* Not before the backend's own grace for a running job has passed: taken sooner, the wait
		 * that lets a job finish is the wait that gets cut. */
		await vi.advanceTimersByTimeAsync(31_000);
		expect(spawned.kill).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(10_000);
		await stopping;

		expect(spawned.kill).toHaveBeenCalledOnce();
	});

	it('is content when there is nothing running', async () => {
		const backend = new Backend({ dataDir: 'D:\\data', cacheDir: 'D:\\cache' }, () => {});

		await expect(backend.stop()).resolves.toBeUndefined();
		expect(spawned.kill).not.toHaveBeenCalled();
	});
});

/*
 * Changing whether other computers can reach this library while it is running.
 *
 * The address a server listens on is chosen when its socket is opened, so the switch stops the
 * backend and starts it again. Listening on every address always and refusing unwanted requests
 * would put an application-level check where a closed socket is, and a check can have a bug. What
 * is proved here is the sequence.
 *
 * `start` is replaced rather than run: it binds a real port and polls a real `/health`, and neither
 * is what this is about. What is under test is what surrounds it.
 */
describe('changing the listening address while running', () => {
	function backendListeningOn(share: boolean): InstanceType<typeof Backend> {
		const backend = started();
		(backend as unknown as { shareOnNetwork: boolean }).shareOnNetwork = share;
		return backend;
	}

	// Every test here stops the backend at least once, and one of them stops it twice.
	beforeEach(() => {
		goesWhenAsked = true;
	});

	it('does nothing at all when it is already listening on what was asked for', async () => {
		const backend = backendListeningOn(true);
		const start = vi.spyOn(backend, 'start').mockResolvedValue(undefined);

		await expect(backend.listenOnNetwork(true)).resolves.toBe(true);

		expect(start).not.toHaveBeenCalled();
		expect(spawned.stdin.end).not.toHaveBeenCalled();
	});

	it('stops, and starts again on the other address', async () => {
		const backend = backendListeningOn(false);
		const start = vi.spyOn(backend, 'start').mockResolvedValue(undefined);

		await expect(backend.listenOnNetwork(true)).resolves.toBe(true);

		expect(spawned.stdin.end).toHaveBeenCalledOnce();
		expect(start).toHaveBeenCalledOnce();
		expect((backend as unknown as { shareOnNetwork: boolean }).shareOnNetwork).toBe(true);
	});

	it('LEAVES THE OLD ADDRESS RUNNING when the new one will not come up', async () => {
		/* The window must not be left with no backend behind it. `false` is how the screen learns
		   to say the change did not take, instead of showing a state that is not true. */
		const backend = backendListeningOn(false);
		const start = vi
			.spyOn(backend, 'start')
			.mockRejectedValueOnce(new Error('that address is spoken for'))
			.mockResolvedValueOnce(undefined);

		await expect(backend.listenOnNetwork(true)).resolves.toBe(false);

		expect(start).toHaveBeenCalledTimes(2);
		expect((backend as unknown as { shareOnNetwork: boolean }).shareOnNetwork).toBe(false);
	});

	it('gives up out loud when NEITHER address will come up', async () => {
		const gaveUp = vi.fn();
		const backend = started();
		(backend as unknown as { onGaveUp: (reason: string) => void }).onGaveUp = gaveUp;
		vi.spyOn(backend, 'start').mockRejectedValue(new Error('nothing will start'));

		await expect(backend.listenOnNetwork(true)).resolves.toBe(false);

		expect(gaveUp).toHaveBeenCalledOnce();
		expect(gaveUp.mock.calls[0]?.[0]).toContain('sharing');
	});

	it('leaves a LATER death still counting as a crash', async () => {
		/* `stop` sets the flag that tells the exit handler a death was deliberate, and it is never
		   cleared anywhere else. Forget to clear it here and the backend is supervised for the rest
		   of the session by a handler that treats every crash as an intended shutdown, so it dies
		   once and never comes back, silently. */
		const backend = backendListeningOn(false);
		vi.spyOn(backend, 'start').mockResolvedValue(undefined);

		await backend.listenOnNetwork(true);

		expect((backend as unknown as { stopping: boolean }).stopping).toBe(false);
	});
});

/* The exit code that says the stop was ASKED FOR.
 *
 * It is the whole contract between this shell and the backend, and the two are in different
 * languages: a Python test names the same number from the other side. What it buys is the
 * difference between "somebody pressed Restart" and "the backend has crashed again": counted as a
 * crash, four deliberate restarts in five minutes leave Sift refusing to start its own backend and
 * saying so in a message about repeated failure.
 */
describe('an exit the backend asked for', () => {
	function spawnedOnce(gaveUp: (why: string) => void = () => {}) {
		vi.mocked(spawn).mockClear();
		const backend = new Backend({ dataDir: 'D:\\data', cacheDir: 'D:\\cache' }, gaveUp);
		(backend as unknown as { spawnChild(): void }).spawnChild();
		return backend;
	}

	it('is started again, and is not held against it', () => {
		const gaveUp = vi.fn();
		spawnedOnce(gaveUp);

		// Four in a row, which is one more than the crash budget allows.
		for (let each = 0; each < 4; each += 1) spawned.exited?.(ASKED_TO_RESTART);

		expect(vi.mocked(spawn)).toHaveBeenCalledTimes(5);
		expect(gaveUp).not.toHaveBeenCalled();
	});

	/* And the budget still applies to everything else, or a backend failing at startup would be
	   restarted for ever. */
	it('leaves a crash counted as a crash', async () => {
		vi.useFakeTimers();
		const gaveUp = vi.fn();
		spawnedOnce(gaveUp);

		for (let each = 0; each < 4; each += 1) spawned.exited?.(3221225477);
		/* The give-up waits for the dead backend's last output first. */
		await vi.advanceTimersByTimeAsync(DRAIN_MS - 1);
		expect(gaveUp).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(1);

		expect(gaveUp).toHaveBeenCalledOnce();
		expect(gaveUp.mock.calls[0]?.[0]).toContain('stopped 4 times');
		expect(gaveUp.mock.calls[0]?.[0]).toContain('exit code 3221225477 (0xC0000005)');
		expect(gaveUp.mock.calls[0]?.[0]).toContain('The end of start 7:');
		expect(gaveUp.mock.calls[0]?.[1]).toBe(true);
		expect(gaveUp.mock.calls[0]?.[2]).toBe(3221225477);
	});

	/* The shell's own log says the backend went away and came back, and why, or a bug report
	   about a Sift that paused carries nothing of it. */
	it('writes each going away and coming back to the shell log', () => {
		said.length = 0;
		spawnedOnce();

		spawned.exited?.(ASKED_TO_RESTART);
		spawned.exited?.(1);

		expect(said.map(([level, event]) => `${level} ${event}`)).toEqual([
			'info backend.spawned',
			'info backend.restart_asked',
			'info backend.spawned',
			'info backend.started_again',
			'warning backend.exited',
			'info backend.spawned',
			'info backend.started_again'
		]);
		expect(said[4]?.[2]).toEqual({ code: 1 });
		expect(said[6]?.[2]).toEqual({ why: 'stopped' });
	});

	/* A death that `stop` asked for is the end of it: started again, it would be a backend nobody
	   wanted holding the port the next one needs. */
	it('is not started again when the stop was this side asking', () => {
		const backend = spawnedOnce();
		(backend as unknown as { stopping: boolean }).stopping = true;

		spawned.exited?.(1);

		expect(vi.mocked(spawn)).toHaveBeenCalledTimes(1);
	});

	/* A library switch: the server asked to restart, and the shell will start a DIFFERENT library.
	   The backend must not start itself again on the old one underneath that. */
	it('is left to the caller when the caller takes the restart over', () => {
		const gaveUp = vi.fn();
		vi.mocked(spawn).mockClear();
		const backend = new Backend(
			{ dataDir: 'D:\\data', cacheDir: 'D:\\cache' },
			gaveUp,
			false,
			() => true
		);
		(backend as unknown as { spawnChild(): void }).spawnChild();

		spawned.exited?.(ASKED_TO_RESTART);

		expect(vi.mocked(spawn)).toHaveBeenCalledTimes(1);
		expect(gaveUp).not.toHaveBeenCalled();
	});

	/* The restart runs the same checks a launch does, and one that fails is given up on out loud
	   rather than thrown into an exit handler nothing is listening to. */
	it('gives up out loud when the restart finds the interpreter gone', () => {
		const gaveUp = vi.fn();
		spawnedOnce(gaveUp);
		interpreter.present = false;

		spawned.exited?.(ASKED_TO_RESTART);

		expect(gaveUp).toHaveBeenCalledOnce();
		expect(gaveUp.mock.calls[0]?.[0]).toBe('Sift cannot find the Python it runs on.');
		expect(vi.mocked(spawn)).toHaveBeenCalledTimes(1);
	});

	/* Zero is an ordinary shutdown and means the opposite: do not start me again. */
	it('does not treat an ordinary exit as a request to come back', () => {
		const gaveUp = vi.fn();
		spawnedOnce(gaveUp);
		vi.mocked(spawn).mockClear();

		spawned.exited?.(0);

		// Restarted once, as a fault, rather than silently as an asked-for restart.
		expect(vi.mocked(spawn)).toHaveBeenCalledTimes(1);
		expect(gaveUp).not.toHaveBeenCalled();
	});
});

/* ONE release feed. The backend's update check reads the address the shell installs from, handed
   over at start, rather than keeping a copy of its own that could disagree. */
describe('the release feed', () => {
	function variablesOfOneStart(feed?: string | null): Record<string, string | undefined> {
		vi.mocked(spawn).mockClear();
		const backend =
			feed === undefined
				? new Backend({ dataDir: 'D:\\data', cacheDir: 'D:\\cache' }, () => {})
				: new Backend(
						{ dataDir: 'D:\\data', cacheDir: 'D:\\cache' },
						() => {},
						false,
						() => false,
						feed
					);
		(backend as unknown as { spawnChild(): void }).spawnChild();
		const options = vi.mocked(spawn).mock.calls[0]?.[2] as {
			env: Record<string, string | undefined>;
		};
		return options.env;
	}

	it('is handed to the backend it starts', () => {
		expect(variablesOfOneStart('https://updates.example/latest.json').SIFT_RELEASE_FEED_URL).toBe(
			'https://updates.example/latest.json'
		);
	});

	it('is not set at all where the shell names none', () => {
		expect('SIFT_RELEASE_FEED_URL' in variablesOfOneStart()).toBe(false);
		expect('SIFT_RELEASE_FEED_URL' in variablesOfOneStart(null)).toBe(false);
	});
});

/* The shell link: where the backend asks this shell for what only it can do, and this launch's
   secret for asking. Handed down at start, and absent where there is no link. */
describe('the shell link', () => {
	function variablesWith(
		link: { url: string; token: string } | null
	): Record<string, string | undefined> {
		vi.mocked(spawn).mockClear();
		const backend = new Backend(
			{ dataDir: 'D:\\data', cacheDir: 'D:\\cache' },
			() => {},
			false,
			() => false,
			null,
			link
		);
		(backend as unknown as { spawnChild(): void }).spawnChild();
		const { env: variables } = vi.mocked(spawn).mock.calls[0]?.[2] as {
			env: Record<string, string | undefined>;
		};
		return variables;
	}

	it('is handed to the backend it starts', () => {
		const variables = variablesWith({ url: 'http://127.0.0.1:50123', token: 'launch-secret' });
		expect(variables['SIFT_SHELL_URL']).toBe('http://127.0.0.1:50123');
		expect(variables['SIFT_SHELL_TOKEN']).toBe('launch-secret');
	});

	it('is not set at all without a link', () => {
		const variables = variablesWith(null);
		expect('SIFT_SHELL_URL' in variables).toBe(false);
		expect('SIFT_SHELL_TOKEN' in variables).toBe(false);
	});
});

/* Held only for a launch the crash dialog asked for. */
describe('the optional features held off', () => {
	function variablesOf(held?: boolean): Record<string, string | undefined> {
		vi.mocked(spawn).mockClear();
		starts.facts.length = 0;
		const backend = new Backend(
			{ dataDir: 'D:\\data', cacheDir: 'D:\\cache' },
			() => {},
			false,
			() => false,
			null,
			null,
			...(held === undefined ? [] : [held])
		);
		(backend as unknown as { spawnChild(): void }).spawnChild();
		const { env: variables } = vi.mocked(spawn).mock.calls[0]?.[2] as {
			env: Record<string, string | undefined>;
		};
		return variables;
	}

	it('are asked for by a variable only when held', () => {
		expect(variablesOf(true)['SIFT_HOLD_OPTIONAL_FEATURES']).toBe('true');
		expect(starts.facts[0]?.[1]).toBe(true);
		expect('SIFT_HOLD_OPTIONAL_FEATURES' in variablesOf(false)).toBe(false);
		expect('SIFT_HOLD_OPTIONAL_FEATURES' in variablesOf()).toBe(false);
		expect(starts.facts[0]?.[1]).toBe(false);
	});
});

/* The backend's output goes through this side into the start's own file, both streams. */
describe('the output of a start', () => {
	it('is written to its start log, which is closed when the streams are', async () => {
		starts.written.length = 0;
		starts.closed = 0;
		let closeIt: () => void = () => {};
		spawned.once.mockImplementation((event: string, run: (code?: number) => void) => {
			if (event === 'close') closeIt = run as () => void;
		});
		const backend = new Backend({ dataDir: 'D:\\data', cacheDir: 'D:\\cache' }, () => {});
		(backend as unknown as { spawnChild(): void }).spawnChild();
		const options = vi.mocked(spawn).mock.calls.at(-1)?.[2] as { stdio: unknown[] };

		for (const stream of [spawned.stdout, spawned.stderr]) {
			const [event, take] = stream.on.mock.calls.at(-1) as [string, (chunk: Buffer) => void];
			expect(event).toBe('data');
			take(Buffer.from('a line\n'));
		}
		closeIt();
		await Promise.resolve();

		expect(options.stdio).toEqual(['pipe', 'pipe', 'pipe']);
		expect(starts.written).toEqual(['a line\n', 'a line\n']);
		expect(starts.closed).toBe(1);
	});
});

/* Ready is the backend's own line on stdout, said once its socket listens, rather than the next
   poll of /health: a line split across two reads still counts, and a line that only contains the
   words does not. */
describe('the backend saying it is listening', () => {
	function heard(...chunks: string[]): boolean {
		const backend = new Backend({ dataDir: 'D:\\data', cacheDir: 'D:\\cache' }, () => {});
		(backend as unknown as { spawnChild(): void }).spawnChild();
		const [, take] = spawned.stdout.on.mock.calls.at(-1) as [string, (chunk: Buffer) => void];
		for (const chunk of chunks) take(Buffer.from(chunk));
		return (backend as unknown as { listening: boolean }).listening;
	}

	it('is heard on its own line, whole or split', () => {
		expect(heard('{"event":"boot.ready"}\n', 'sift.listening\n')).toBe(true);
		expect(heard('{"event":"boot.ready"}\nsift.lis', 'tening\r\n')).toBe(true);
	});

	it('is not heard inside another line, or before its line ends', () => {
		expect(heard('{"said":"sift.listening"}\n')).toBe(false);
		expect(heard('sift.listening')).toBe(false);
	});

	it('ends the wait for ready without another poll', async () => {
		vi.useFakeTimers();
		const backend = new Backend({ dataDir: 'D:\\data', cacheDir: 'D:\\cache' }, () => {});
		(backend as unknown as { spawnChild(): void }).spawnChild();
		const [, take] = spawned.stdout.on.mock.calls.at(-1) as [string, (chunk: Buffer) => void];
		let asked = 0;
		vi.stubGlobal('fetch', async () => {
			asked += 1;
			throw new Error('nothing is listening yet');
		});
		const waited = (backend as unknown as { waitForHealth(): Promise<void> }).waitForHealth();
		await vi.advanceTimersByTimeAsync(10);
		take(Buffer.from('sift.listening\n'));
		await vi.advanceTimersByTimeAsync(0);

		await expect(waited).resolves.toBeUndefined();
		expect(asked).toBe(1);
		vi.unstubAllGlobals();
		vi.useRealTimers();
	});
});

/* Where the shell's own logs are, so the backend's Download log carries them by the folder the
   shell writes to, never by a guess at the name it is installed under. */
describe('the shell log folder', () => {
	it('is the folder beside the backend log, handed to the backend it starts', () => {
		vi.mocked(spawn).mockClear();
		const backend = new Backend({ dataDir: 'D:\\data', cacheDir: 'D:\\cache' }, () => {});
		(backend as unknown as { spawnChild(): void }).spawnChild();
		const { env } = vi.mocked(spawn).mock.calls[0]?.[2] as {
			env: Record<string, string | undefined>;
		};
		expect(env['SIFT_APP_LOG_DIR']).toBe(path.dirname(backendLogFile()));
	});
});
