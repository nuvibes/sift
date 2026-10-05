/* Starting, watching and stopping the Python backend.
 *
 * Only standalone does this. In client mode somebody else is running the backend and this file is
 * never touched.
 *
 * This module is the backend's supervisor: it restarts it after a crash, keeps its output
 * somewhere findable, and refuses to start on a port that is already held.
 */

import { spawn, type ChildProcess } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { openStart } from './backendlog';
import { writeFacts } from './facts';
import { log as shellLog } from './log';
import { backendLogFile, bundlePaths } from './paths';
import type { DataLocations } from './paths';

/* Fixed, and not negotiable by the application. A shell
 * that quietly picks a free port produces a different address every launch, which breaks a saved
 * bookmark, breaks a second machine pointed at this one, and makes "it is on 5171" (the one thing
 * the documentation can say) untrue. If the port is held, Sift says so and does not move. */
export const PORT = 5171;
export const HOST = '127.0.0.1';
export const ORIGIN = `http://${HOST}:${PORT}`;

/* The address the backend listens on when the library is offered to the network.
 *
 * `0.0.0.0` is "every address this machine has", which is what makes it reachable from a second
 * computer. The window itself still loads ORIGIN either way: going through the loopback address is
 * correct even while sharing, and it keeps the page a secure context, which is what the browser
 * requires before it will hand over the clipboard.
 */
export const SHARED_HOST = '0.0.0.0';

/* Enough to cover a first start that has to create a database and run migrations, and little enough
 * that a backend which is never going to answer does not hang the window indefinitely. */
const HEALTH_TIMEOUT_MS = 90_000;
const HEALTH_INTERVAL_MS = 250;

/* How long a clean shutdown is given before it is taken instead.
 *
 * At least the backend's own grace for a running job (thirty seconds, `SHUTDOWN_GRACE_SECONDS`
 * in the Python tree, which a gate there pins this number above) plus the database's close.
 * Anything shorter takes the process in the middle of the wait that lets a job finish. Almost
 * every stop is immediate; this is spent only when a job is mid-flight.
 */
const STOP_TIMEOUT_MS = 40_000;

/* Three restarts inside five minutes and it stops and says so, as a service manager would. */
const MAX_RESTARTS = 3;
const RESTART_WINDOW_MS = 5 * 60_000;

/** How long a dead backend's last output is waited for: a native crash's record comes last. */
export const DRAIN_MS = 2_000;

/** The exit code as a person can look it up: Windows' own codes read in hex. */
export function exitCodeWords(code: number | null): string {
	if (code === null) return 'It ended without an exit code.';
	const unsigned = code >>> 0;
	const hex = unsigned >= 0x80000000 ? ` (0x${unsigned.toString(16).toUpperCase()})` : '';
	return `It stopped with exit code ${unsigned}${hex}.`;
}

/* What the backend exits with when the stop was ASKED FOR: somebody pressed Restart in Settings,
 * which is how the graphics-card runtime is made to take effect.
 *
 * It has to be told apart from a crash, and not as a nicety: a deliberate restart counted against
 * the crash budget above means four of them in five minutes leaves Sift refusing to start its own
 * backend, with a message about it having stopped four times. Kept in step with
 * `RESTART_EXIT_CODE` in sift/kernel/lifecycle.py, and there is a test on each side naming the
 * number, because the two halves are in different languages and nothing else would notice a
 * change. */
export const ASKED_TO_RESTART = 86;

/*
 * How the interpreter is run, and why it is not simply `python -m sift.main`.
 *
 * `-I` is isolated mode. A normal interpreter reads two places before its own packages: the
 * per-user folder at `%APPDATA%\\Python\\Python313\\site-packages`, and whatever `PYTHONPATH`
 * names. Anybody who has run `pip install --user` on a Python 3.13 of their own has packages there
 * that would override the ones Sift ships: an older `typing_extensions`, for one, fails FastAPI's
 * import with `cannot import name 'sentinel'`, the backend dies before it opens a socket, and four
 * such crashes stop Sift for good. Reinstalling does not help, because the folder belongs to the
 * person.
 *
 * `-I` also implies `-E`, which ignores every `PYTHON*` variable, so `-u` does PYTHONUNBUFFERED's
 * job: a log written as it happens is what makes a failed start readable.
 *
 * One flag rather than a list of variables to strip: a deny-list is only as good as its last edit.
 */
export const INTERPRETER_ARGS = ['-I', '-u'] as const;

export class BackendStartError extends Error {
	constructor(
		message: string,
		/** Shown under the message. The backend's own last words, when it had any. */
		readonly detail: string,
		/** Whether the backend ran and died, rather than never starting. */
		readonly crashed = false,
		readonly code: number | null = null
	) {
		super(message);
		this.name = 'BackendStartError';
	}
}

/* Checked before spawning rather than after failing, because uvicorn's own bind error is a
 * traceback ending in WinError 10048. The usual cause is Sift already running.
 *
 * Both addresses are checked because Windows lets a socket bind 127.0.0.1:5171 while a different
 * process holds 0.0.0.0:5171 (Linux refuses this). Traffic to localhost then goes to the more
 * specific binding, so the start looks successful while another server (an older Sift, say)
 * goes on answering on the machine's network address. The symptom is an empty library that looks
 * like a lost collection, so this refuses to start and names the likely cause.
 */
export async function assertPortIsFree(port: number): Promise<void> {
	const net = await import('node:net');

	const probe = (address: string): Promise<boolean> =>
		new Promise((resolve) => {
			const server = net.createServer();
			server.once('error', () => resolve(false));
			server.once('listening', () => server.close(() => resolve(true)));
			server.listen(port, address);
		});

	if (!(await probe(HOST))) {
		throw new BackendStartError(
			`Something is already using port ${port} on this device.`,
			'Sift needs that port. It is most often Sift itself, already running \u2014 check the ' +
				'taskbar and the notification area. If it is another program, close it and start ' +
				'Sift again.'
		);
	}

	if (!(await probe('0.0.0.0'))) {
		throw new BackendStartError(
			`Something else is already using port ${port}.`,
			'Sift can start, but it would be sharing the port, and there would be no way to tell ' +
				'which one you were looking at. You could end up staring at an empty library here ' +
				'while your real one is still being served by something else.\n\n' +
				'This is usually another copy of Sift sharing its library on your network. Close ' +
				'it, then start Sift again.'
		);
	}
}

export class Backend {
	private child: ChildProcess | null = null;
	private restarts: number[] = [];
	private stopping = false;
	private lastExit: number | null = null;
	private startNumber: number | null = null;
	/** Settles once the last child's output is all written, or DRAIN_MS after it died. */
	private drained: Promise<void> = Promise.resolve();

	constructor(
		private readonly locations: DataLocations,
		/** Called when the backend has died for good, so the window can say so; `crashed` when it
		 *  kept dying rather than failing to start. */
		private readonly onGaveUp: (reason: string, crashed: boolean, code?: number | null) => void,
		/** Whether to listen on every address rather than only on this computer's own. */
		private shareOnNetwork = false,
		/** Whether the caller takes a requested restart over (a library switch starts a different
		 *  library); false means the backend starts itself again on the same one. */
		private readonly takesOverRestart: () => boolean = () => false,
		/** The release feed the backend's update check reads. Null starts it with no feed at all. */
		private readonly releaseFeed: string | null = null,
		/** Where the backend asks this shell for the acts only it can do (see `shelllink.ts`). Null
		 *  starts it with no shell to ask, as a backend run by hand has. */
		private readonly shellLink: { url: string; token: string } | null = null,
		/** Face recognition, Smart Search and watermark reading held off for this launch. */
		private readonly holdOptional = false
	) {}

	/** Start it and do not return until `/health` answers, or until we know it never will. */
	async start(): Promise<void> {
		await assertPortIsFree(PORT);
		this.spawnChild();
		await this.waitForHealth();
	}

	/**
	 * Change whether other computers can reach this library now: a socket's address is fixed when
	 * it opens, so it stops and starts again, and saved keys are sealed as at a launch. False means
	 * the old address was put back and the switch did not take.
	 */
	async listenOnNetwork(share: boolean): Promise<boolean> {
		if (share === this.shareOnNetwork) return true;
		const previous = this.shareOnNetwork;
		if (await this.relisten(share)) return true;
		/* The new address would not come up. Put the old one back rather than leave the window with
		 * no backend behind it. */
		if (await this.relisten(previous)) return false;
		this.onGaveUp(
			'Sift changed the sharing setting, and then could not start its backend again on ' +
				`either address. The reason is at the end of ${backendLogFile()}.`,
			false
		);
		return false;
	}

	/** Stop, and start again on the given address. False if it did not come up. */
	private async relisten(share: boolean): Promise<boolean> {
		await this.stop();
		this.shareOnNetwork = share;
		/* `stop` sets the flag that tells the exit handler this death was deliberate. Cleared here
		 * and not in `stop`, so a backend that dies on its own between the two is still a crash. */
		this.stopping = false;
		try {
			await this.start();
			return true;
		} catch {
			/* It may have spawned and then never answered, in which case it is still holding the
			 * port, and the retry on the other address would fail on that rather than on whatever
			 * actually went wrong. */
			await this.stop();
			return false;
		}
	}

	private spawnChild(): void {
		const { python, vendorBin } = bundlePaths();
		if (!fs.existsSync(python)) {
			throw new BackendStartError(
				'Sift cannot find the Python it runs on.',
				`Expected it at ${python}. If this is a development checkout, create the environment ` +
					'first; if this is an installed copy, the installation is incomplete and ' +
					'reinstalling is the fix.'
			);
		}

		writeFacts(python, this.holdOptional);
		const log = openStart();
		this.startNumber = log.start;

		/* Everything the backend needs arrives as environment variables. It already reads every
		 * setting from SIFT_-prefixed ones, so the shell can configure it without writing a second
		 * settings file that could then disagree with what the shell believes. */
		const childEnv: NodeJS.ProcessEnv = {
			...process.env,
			SIFT_DATA_DIR: this.locations.dataDir,
			SIFT_CACHE_DIR: this.locations.cacheDir,
			/* The one setting that decides whether this is a program on a computer or a service on a
			 * network. Read from the shell's settings at START, because that is the only moment a
			 * listening socket's address can still be chosen. */
			SIFT_HOST: this.shareOnNetwork ? SHARED_HOST : HOST,
			SIFT_PORT: String(PORT),
			SIFT_FFMPEG_PATH: path.join(vendorBin, 'ffmpeg.exe'),
			SIFT_FFPROBE_PATH: path.join(vendorBin, 'ffprobe.exe'),
			SIFT_WEBPINFO_PATH: path.join(vendorBin, 'webpinfo.exe'),
			SIFT_ANIM_DUMP_PATH: path.join(vendorBin, 'anim_dump.exe'),
			/* The folder itself is not passed. The tunnel client is started deep inside the
			 * downloader, which has no Settings in hand, so the backend finds it by walking up
			 * from its own location (`vendored_tool` in sift/kernel/config.py). An unrecognised
			 * `SIFT_` variable is a hard error in the backend, so there is one mechanism rather
			 * than two.
			 */
			/* Unbuffered output is asked for on the command line, not here: `-I` ignores every
			 * `PYTHON*` variable, so `PYTHONUNBUFFERED` set here would be read by nothing. See
			 * INTERPRETER_ARGS.
			 */
			/* Turns on the backend's clean-shutdown watch. Opt-in, and it has to be: the behaviour it
			 * enables is "stop when stdin reaches EOF", and stdin is at EOF from the first instant in
			 * any container started without `-i`. See stop_when_the_parent_lets_go in sift/main.py. */
			SIFT_STOP_ON_STDIN_EOF: 'true',
			...(this.holdOptional ? { SIFT_HOLD_OPTIONAL_FEATURES: 'true' } : {}),
			/* The feed this shell installs updates from, so the backend's "a new version is out" and
			 * the shell's Install button read one address and cannot disagree. */
			...(this.releaseFeed === null ? {} : { SIFT_RELEASE_FEED_URL: this.releaseFeed }),
			/* The shell this backend may ask, and this launch's secret for asking it. Handed down
			 * here because only the process that spawns the backend can set its environment, so
			 * nothing else on the machine learns the secret without already being this user. */
			...(this.shellLink === null
				? {}
				: { SIFT_SHELL_URL: this.shellLink.url, SIFT_SHELL_TOKEN: this.shellLink.token })
		};

		this.child = spawn(python, [...INTERPRETER_ARGS, '-m', 'sift.main'], {
			env: childEnv,
			/* A pipe for stdin, and nothing is ever written down it. Closing it is how this shell
			 * asks the backend to shut down cleanly, and only the process that spawned it holds
			 * the other end, so there is no port to reach and no token to leak. 'ignore' would
			 * hand the child a null device that is at EOF immediately.
			 */
			stdio: ['pipe', 'pipe', 'pipe'],
			windowsHide: true
		});
		/* Through this side, so each start has a file of its own and a cap. */
		this.child.stdout?.on('data', (chunk: Buffer) => log.write(chunk));
		this.child.stderr?.on('data', (chunk: Buffer) => log.write(chunk));
		const closed = new Promise<void>((done) => this.child?.once('close', () => done()));
		void closed.then(() => log.close());

		this.child.once('exit', (code) => {
			this.lastExit = code;
			this.drained = Promise.race([closed, new Promise<void>((r) => setTimeout(r, DRAIN_MS))]);
			this.child = null;
			if (this.stopping) {
				shellLog.info('backend.stopped', { code });
				return;
			}
			if (code === ASKED_TO_RESTART) {
				/* Asked for, so it is started again and NOT counted as a fault. The clean shutdown has
				 * already run on the other side: the database is closed and its log is folded. */
				shellLog.info('backend.restart_asked', {});
				if (this.takesOverRestart()) return;
				this.startAgain('asked');
				return;
			}
			shellLog.warning('backend.exited', { code });
			this.handleUnexpectedExit();
		});
	}

	/** Start it again without holding the last stop against it. */
	private startAgain(why: 'asked' | 'stopped'): void {
		try {
			this.spawnChild();
			shellLog.info('backend.started_again', { why });
		} catch (err) {
			const reason = err instanceof Error ? err.message : String(err);
			shellLog.error('backend.gave_up', { reason });
			this.onGaveUp(reason, false);
		}
	}

	private handleUnexpectedExit(): void {
		const now = Date.now();
		this.restarts = this.restarts.filter((t) => now - t < RESTART_WINDOW_MS);
		if (this.restarts.length >= MAX_RESTARTS) {
			shellLog.error('backend.gave_up', {
				restarts: this.restarts.length + 1,
				code: this.lastExit
			});
			void this.drained.then(() =>
				this.onGaveUp(
					`Sift's backend stopped ${MAX_RESTARTS + 1} times in a few minutes, so it hasn't been ` +
						`started again. ${this.lastWords()}`,
					true,
					this.lastExit
				)
			);
			return;
		}
		this.restarts.push(now);
		this.startAgain('stopped');
	}

	/* Polling /health, not sleeping for a guessed interval. A first start creates the database and
	 * checks what SQLite can do, which takes as long as it takes, and on a warm start it is ready
	 * almost at once. Either way the window opens the moment it is actually true. */
	private async waitForHealth(): Promise<void> {
		const deadline = Date.now() + HEALTH_TIMEOUT_MS;
		let lastError = '';
		while (Date.now() < deadline) {
			if (this.child === null) {
				await this.drained;
				throw new BackendStartError(
					'Sift stopped while it was starting up.',
					this.lastWords(),
					true,
					this.lastExit
				);
			}
			try {
				const res = await fetch(`${ORIGIN}/health`, { signal: AbortSignal.timeout(2_000) });
				if (res.ok) return;
				lastError = `/health answered ${res.status}`;
			} catch (err) {
				lastError = err instanceof Error ? err.message : String(err);
			}
			await new Promise((r) => setTimeout(r, HEALTH_INTERVAL_MS));
		}
		throw new BackendStartError(
			'Sift started but never became ready.',
			`${lastError}\n\n${this.tailOfLog()}`
		);
	}

	/** The exit code, then the end of the start that failed. */
	private lastWords(): string {
		const which = this.startNumber === null ? '' : `The end of start ${this.startNumber}:\n`;
		return `${exitCodeWords(this.lastExit)}\n\n${which}${this.tailOfLog()}`;
	}

	/** The last lines of this start's output (the file holds no other start's). */
	private tailOfLog(): string {
		try {
			const text = fs.readFileSync(backendLogFile(), 'utf8');
			return text.split('\n').slice(-25).join('\n').trim();
		} catch {
			return `(nothing was captured in ${backendLogFile()})`;
		}
	}

	/* Asked to stop, then killed if it will not go. A backend killed outright can leave the
	 * database's write-ahead log unfolded: recoverable, but it makes the next start slower and
	 * noisier for no reason. */
	/**
	 * Ask the backend to stop, and wait for it.
	 *
	 * `child.kill()` is not a polite request on Windows: Node turns every signal name into
	 * TerminateProcess, which stops the process where it stands and leaves the write-ahead log
	 * unfolded.
	 *
	 * Closing stdin is the request instead. The backend watches that pipe and stops the way it
	 * would for Ctrl-C, running its lifespan's shutdown on the way out. The kill below is the
	 * fallback for a backend that has stopped answering, which is exactly when a hard stop is
	 * right.
	 */
	async stop(): Promise<void> {
		this.stopping = true;
		const child = this.child;
		if (child === null) return;
		try {
			child.stdin?.end();
		} catch {
			/* Already gone. The wait below then ends on the exit that is already coming. */
		}
		await new Promise<void>((resolve) => {
			const timer = setTimeout(() => {
				/* It did not go on its own. Take it, and accept the unfolded log: a shell that hangs
				 * on quit is a worse outcome than a database that replays its log on the next start. */
				child.kill();
				resolve();
			}, STOP_TIMEOUT_MS);
			child.once('exit', () => {
				clearTimeout(timer);
				resolve();
			});
		});
	}
}
