/* The port check, which is the one part of starting a backend that can be tested without one.
 *
 * The state it refuses (another process, such as an older Sift, still publishing 5171 while a
 * backend starts beside it) shows up as an empty library that looks like a lost collection. A
 * check that stopped working would not look broken; it would look like a successful start.
 *
 * Every case below uses a port the test itself is holding, never 5171. A test that reached for
 * the real port would pass or fail depending on whether Sift happened to be running on the
 * machine.
 */

import * as net from 'node:net';

import { afterEach, describe, expect, it } from 'vitest';

import {
	assertPortIsFree,
	BackendStartError,
	HOST,
	INTERPRETER_ARGS,
	ORIGIN,
	PORT,
	SHARED_HOST
} from './backend';

const holders: net.Server[] = [];

function hold(address: string): Promise<number> {
	return new Promise((resolve, reject) => {
		const server = net.createServer();
		holders.push(server);
		server.once('error', reject);
		server.listen(0, address, () => {
			const found = server.address();
			if (found === null || typeof found === 'string') {
				reject(new Error('no port'));
				return;
			}
			resolve(found.port);
		});
	});
}

afterEach(async () => {
	await Promise.all(holders.splice(0).map((s) => new Promise((done) => s.close(done))));
});

describe('the address the shell serves on', () => {
	/* Fixed by decision, not by convenience. A shell that quietly picked a free
	 * port would hand out a different address every launch. */
	it('is 5171 on loopback, and the origin is built from both', () => {
		expect(PORT).toBe(5171);
		expect(HOST).toBe('127.0.0.1');
		expect(ORIGIN).toBe('http://127.0.0.1:5171');
	});

	/* Two addresses, and only one of them is ever the default.
	 *
	 * `HOST` answers to this computer alone; `SHARED_HOST` answers to every address the machine has,
	 * which is what makes a second computer able to reach the library. Which one is used is decided
	 * from a setting that defaults to off (see settings.test.ts, which is where that is guarded).
	 *
	 * The WINDOW still loads `ORIGIN` either way, and that is not an oversight. Going through the
	 * loopback address keeps the page a secure context, which is what the browser requires before it
	 * will hand over the clipboard, so sharing must not quietly cost the application its own
	 * Paste button.
	 */
	it('has a separate address for sharing, and it is not the one the window loads', () => {
		expect(SHARED_HOST).toBe('0.0.0.0');
		expect(SHARED_HOST).not.toBe(HOST);
		expect(ORIGIN).toContain(HOST);
		expect(ORIGIN).not.toContain(SHARED_HOST);
	});
});

describe('assertPortIsFree', () => {
	it('says nothing about a port nobody is using', async () => {
		const free = await hold('127.0.0.1');
		await new Promise((done) => holders.pop()?.close(done));
		await expect(assertPortIsFree(free)).resolves.toBeUndefined();
	});

	it('refuses a port already held on loopback, and names it', async () => {
		const taken = await hold('127.0.0.1');
		await expect(assertPortIsFree(taken)).rejects.toThrow(BackendStartError);
		await expect(assertPortIsFree(taken)).rejects.toThrow(String(taken));
	});

	/* The Windows-only case, and the reason this function checks twice.
	 *
	 * Windows lets a socket bind 127.0.0.1:N while another process holds 0.0.0.0:N, where Linux refuses
	 * it. So on Windows the first probe SUCCEEDS and only the second one catches it; on Linux the
	 * first probe already fails. Both are a refusal, which is all this asserts: pinning which of the
	 * two messages appears would make the test platform-specific for no gain. */
	it('refuses a port held on every address, whichever probe catches it', async () => {
		const taken = await hold('0.0.0.0');
		await expect(assertPortIsFree(taken)).rejects.toThrow(BackendStartError);
	});

	it('says what to close when a wildcard listener is what it found', async () => {
		const taken = await hold('0.0.0.0');
		try {
			await assertPortIsFree(taken);
			expect.unreachable('a held port must be refused');
		} catch (err) {
			expect(err).toBeInstanceOf(BackendStartError);
			const detail = (err as BackendStartError).detail;
			// Whichever probe fired, the person is told what to actually do about it.
			expect(detail.length).toBeGreaterThan(40);
			expect(detail).toMatch(/Sift/);
		}
	});
});

/*
 * How the interpreter is started, which is a shipping property rather than a preference.
 *
 * An older `typing_extensions` in a person's own per-user package folder is imported in preference
 * to Sift's, and the backend dies before opening a socket. A development machine never has that
 * folder, so only these tests notice.
 *
 * The behavioural proof runs the real interpreter against a staged foreign package folder and lives
 * in scripts/release.py (`prove_the_runtime_ignores_other_pythons`), which reads this constant, so
 * the two cannot drift apart. What is checked here is the reasoning, which is what an edit would
 * break first.
 */
describe('the flags the backend is started with', () => {
	it('isolates the interpreter from any other Python on the machine', () => {
		expect(INTERPRETER_ARGS).toContain('-I');
	});

	/* Not a second preference, a consequence of the first. `-I` implies `-E`, which ignores every
	 * PYTHON* variable, so PYTHONUNBUFFERED set in the child's environment is read by nothing,
	 * and without `-u` a crash on startup loses its last words.
	 *
	 * Written as an implication rather than as two assertions so it fails for the right reason:
	 * dropping isolation is a decision somebody can make; keeping it while dropping `-u` is the
	 * silent half.
	 */
	it('asks for unbuffered output on the command line, because isolation ignores the variable', () => {
		if (!INTERPRETER_ARGS.includes('-I')) return;
		expect(INTERPRETER_ARGS).toContain('-u');
	});
});
