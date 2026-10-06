/* The facts a report needs, read off this device and the bundled runtime. */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { fileVersion, NATIVE, nativeVersions, startFacts, writeFacts } from './facts';
import { logTo } from './log';

let folder = '';

beforeEach(() => {
	folder = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-facts-'));
});

afterEach(() => {
	logTo(null);
	fs.rmSync(folder, { recursive: true, force: true });
});

function dll(file: string, version: number[] | null): string {
	const block =
		version === null
			? Buffer.alloc(0)
			: Buffer.concat([
					Buffer.from([0xbd, 0x04, 0xef, 0xfe]),
					Buffer.from(
						new Uint32Array([
							0x10000,
							((version[0] ?? 0) << 16) | (version[1] ?? 0),
							((version[2] ?? 0) << 16) | (version[3] ?? 0)
						]).buffer
					)
				]);
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.writeFileSync(file, Buffer.concat([Buffer.from('MZ'), Buffer.alloc(60), block]));
	return file;
}

function packages(root: string, names: string[]): void {
	for (const name of names) {
		fs.mkdirSync(path.join(root, 'Lib', 'site-packages', name), { recursive: true });
	}
}

describe('a file version', () => {
	it('is read from the version block, and is null without one', () => {
		expect(fileVersion(dll(path.join(folder, 'a.dll'), [14, 44, 35211, 0]))).toBe('14.44.35211.0');
		expect(fileVersion(dll(path.join(folder, 'b.dll'), null))).toBeNull();
		expect(fileVersion(path.join(folder, 'missing.dll'))).toBeNull();
		fs.writeFileSync(path.join(folder, 'c.dll'), Buffer.from([0xbd, 0x04, 0xef, 0xfe]));
		expect(fileVersion(path.join(folder, 'c.dll'))).toBeNull();
	});
});

describe('the native libraries', () => {
	it('are named with the version their dist-info folder carries, or missing', () => {
		packages(folder, ['onnxruntime-1.30.0.dist-info', 'numpy-2.5.3.dist-info', 'numpy']);

		const found = nativeVersions(path.join(folder, 'Lib', 'site-packages'));

		expect(found['onnxruntime']).toBe('1.30.0');
		expect(found['numpy']).toBe('2.5.3');
		expect(found['blake3']).toBe('missing');
		expect(Object.keys(found)).toEqual([...NATIVE]);
	});

	it('all read missing where there are no packages', () => {
		expect(Object.values(nativeVersions(path.join(folder, 'nowhere')))).toEqual(
			NATIVE.map(() => 'missing')
		);
	});
});

describe('the facts of a start', () => {
	it('read the bundled runtime beside the installed interpreter', () => {
		const python = path.join(folder, 'runtime', 'python.exe');
		dll(path.join(folder, 'runtime', 'msvcp140.dll'), [14, 44, 35211, 0]);
		packages(path.join(folder, 'runtime'), ['onnxruntime-1.30.0.dist-info']);

		const facts = startFacts(python, false);

		expect(facts['cpp_runtime']).toBe('14.44.35211.0');
		expect(facts['onnxruntime']).toBe('1.30.0');
		expect(facts['optional_features']).toBe('as set in Settings');
		expect(facts['windows']).toBe(os.release());
		expect(facts['threads']).toBe(os.cpus().length);
		expect(typeof facts['cpu']).toBe('string');
		expect(typeof facts['memory_gb']).toBe('number');
		expect(typeof facts['cpp_runtime_windows']).toBe('string');
	});

	it("read a checkout's packages from beside its Scripts folder", () => {
		packages(path.join(folder, '.venv'), ['numpy-2.5.3.dist-info']);

		const facts = startFacts(path.join(folder, '.venv', 'Scripts', 'python.exe'), true);

		expect(facts['numpy']).toBe('2.5.3');
		expect(facts['cpp_runtime']).toBe('none beside Sift');
		expect(facts['optional_features']).toBe('held off for this start');
	});

	it('are written to the app log as one line', async () => {
		const file = path.join(folder, 'shell.log');
		logTo(file);

		const facts = await writeFacts(path.join(folder, 'python.exe'), false);

		const line = JSON.parse(fs.readFileSync(file, 'utf8').trim()) as Record<string, unknown>;
		expect(line['event']).toBe('backend.facts');
		expect(line['sift']).toBe(facts['sift']);
	});
});
