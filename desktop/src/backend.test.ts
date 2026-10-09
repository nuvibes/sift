/* The port check, which is the one part of starting a backend that can be tested without one. */

import * as net from 'node:net';

import { afterEach, describe, expect, it } from 'vitest';

import {
	assertPortIsFree,
	BackendStartError,
	exitCodeWords,
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

	/* Two addresses, and only one of them is ever the default. */
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

	/* The Windows-only case, and the reason this function checks twice. */
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

/* How the interpreter is started, which is a shipping property rather than a preference. */
describe('the flags the backend is started with', () => {
	it('isolates the interpreter from any other Python on the machine', () => {
		expect(INTERPRETER_ARGS).toContain('-I');
	});

	/* Not a second preference, a consequence of the first. */
	it('asks for unbuffered output on the command line, because isolation ignores the variable', () => {
		if (!INTERPRETER_ARGS.includes('-I')) return;
		expect(INTERPRETER_ARGS).toContain('-u');
	});
});

describe('the exit code, in words', () => {
	it("reads Windows' own codes in hex, and says when there was none", () => {
		expect(exitCodeWords(3221225477)).toBe('It stopped with exit code 3221225477 (0xC0000005).');
		expect(exitCodeWords(-1073741819)).toContain('(0xC0000005)');
		expect(exitCodeWords(1)).toBe('It stopped with exit code 1.');
		expect(exitCodeWords(null)).toBe('It ended without an exit code.');
	});

	it('marks a start error as a crash only when it is told', () => {
		expect(new BackendStartError('m', 'd').crashed).toBe(false);
		expect(new BackendStartError('m', 'd', true).crashed).toBe(true);
	});
});
