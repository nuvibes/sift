/* One file per start of the backend, numbered, capped, and the last few kept. */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { KEEP, lastStart, MOST_BYTES, openStart, roll, StartLog } from './backendlog';
import { backendLogFile } from './paths';

let folder = '';
let at = '';

beforeEach(() => {
	folder = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-backendlog-'));
	at = path.join(folder, 'backend.log');
});

afterEach(() => {
	fs.rmSync(folder, { recursive: true, force: true });
});

const firstLine = (file: string) =>
	JSON.parse(fs.readFileSync(file, 'utf8').split('\n')[0] ?? '') as Record<string, unknown>;

describe('a start', () => {
	it('opens with its version and a number one past the start before it', () => {
		openStart(at, '0.2.1').close();
		openStart(at, '0.2.1').close();

		expect(firstLine(at)).toMatchObject({ event: 'backend.start', version: '0.2.1', start: 2 });
		expect(firstLine(`${at}.1`)).toMatchObject({ start: 1 });
	});

	it('keeps the last few starts and drops the oldest', () => {
		for (let each = 0; each < KEEP + 2; each += 1) openStart(at, '0.2.1').close();

		const kept = fs.readdirSync(folder).sort();
		expect(kept).toEqual(['backend.log', 'backend.log.1', 'backend.log.2', 'backend.log.3']);
		expect(firstLine(`${at}.3`)).toMatchObject({ start: KEEP + 2 - 3 });
	});

	it('writes what the backend says, after its first line', () => {
		const start = openStart(at, '0.2.1');
		start.write(Buffer.from('Uvicorn running\n'));
		start.close();
		start.close();
		start.write(Buffer.from('after the end\n'));

		expect(fs.readFileSync(at, 'utf8').split('\n').slice(1)).toEqual(['Uvicorn running', '']);
	});

	it('goes on in a new file at the cap, under the same number', () => {
		const start = openStart(at, '0.2.1');
		start.write(Buffer.alloc(MOST_BYTES - 10, 0x61));
		start.write(Buffer.from('the line that did not fit\n'));
		start.close();

		expect(firstLine(at)).toMatchObject({ event: 'backend.start_continued', start: 1 });
		expect(fs.readFileSync(at, 'utf8')).toContain('the line that did not fit');
		expect(fs.statSync(`${at}.1`).size).toBeLessThanOrEqual(MOST_BYTES);
	});

	it('stops at the cap once a later start has begun, rather than rolling its file', () => {
		const ended = openStart(at, '0.2.1');
		const running = openStart(at, '0.2.1');
		ended.write(Buffer.alloc(MOST_BYTES, 0x61));
		running.close();
		ended.close();

		expect(firstLine(at)).toMatchObject({ event: 'backend.start', start: 2 });
		expect(fs.statSync(`${at}.1`).size).toBeLessThan(1000);
	});

	it('lands in the app folder by default, under the app version', () => {
		fs.rmSync(backendLogFile(), { force: true });
		const start = openStart();
		start.close();

		expect(firstLine(backendLogFile())).toMatchObject({ start: 1 });
	});

	it('writes nothing, and throws nothing, where the folder cannot be made', () => {
		fs.writeFileSync(path.join(folder, 'a-file'), '');
		const start = new StartLog(path.join(folder, 'a-file', 'backend.log'), 1, '0.2.1');

		expect(() => start.write(Buffer.from('lost\n'))).not.toThrow();
	});

	it('throws nothing when its file is taken away under it', () => {
		const start = openStart(at, '0.2.1');
		fs.closeSync((start as unknown as { fd: number }).fd);

		expect(() => start.write(Buffer.from('lost\n'))).not.toThrow();
		expect(() => start.close()).not.toThrow();
	});
});

describe('the number of the start before', () => {
	it('is read off the first line, and is 0 for no file or no number', () => {
		expect(lastStart(at)).toBe(0);
		fs.writeFileSync(at, 'a line from before starts were numbered\n');
		expect(lastStart(at)).toBe(0);
		fs.writeFileSync(at, '{"start":41,"event":"backend.start"}\nmore\n');
		expect(lastStart(at)).toBe(41);
	});
});

describe('rolling a file that was never capped', () => {
	it('keeps only its end, from a whole line', () => {
		const line = 'x'.repeat(99) + '\n';
		fs.writeFileSync(at, line.repeat(30_000) + 'the last words\n');

		roll(at);

		expect(fs.existsSync(at)).toBe(false);
		const kept = fs.readFileSync(`${at}.1`, 'utf8');
		expect(kept.length).toBeLessThanOrEqual(MOST_BYTES);
		expect(kept.endsWith('the last words\n')).toBe(true);
		expect(kept.startsWith('x'.repeat(99) + '\n')).toBe(true);
	});
});
