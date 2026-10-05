/* The facts of this device a report needs, written once per start of the backend. */

import { app } from 'electron';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { log, type Fields } from './log';

/** The bundled packages that carry native code, by the name their dist-info folder starts with. */
export const NATIVE = [
	'onnxruntime',
	'numpy',
	'pillow',
	'pillow_heif',
	'sentencepiece',
	'sqlite_vec',
	'resvg',
	'blake3'
] as const;

/** A Windows file's version, from its fixed version block; null where it has none. */
export function fileVersion(file: string): string | null {
	try {
		const data = fs.readFileSync(file);
		const at = data.indexOf(Buffer.from([0xbd, 0x04, 0xef, 0xfe]));
		if (at < 0 || data.length < at + 16) return null;
		const high = data.readUInt32LE(at + 8);
		const low = data.readUInt32LE(at + 12);
		return `${high >>> 16}.${high & 0xffff}.${low >>> 16}.${low & 0xffff}`;
	} catch {
		return null;
	}
}

/** Each native package's version, read off the dist-info folders beside the interpreter. */
export function nativeVersions(packages: string): Fields {
	let names: string[] = [];
	try {
		names = fs.readdirSync(packages);
	} catch {
		/* no packages: every one reads as missing */
	}
	const found: Fields = {};
	for (const one of NATIVE) {
		const folder = names.find((name) => name.toLowerCase().startsWith(`${one}-`));
		found[one] = folder?.slice(one.length + 1).replace(/\.dist-info$/, '') ?? 'missing';
	}
	return found;
}

/** The facts of this start: the interpreter is the bundled one, or a checkout's. */
export function startFacts(python: string, held: boolean): Fields {
	const home = path.dirname(python);
	const root = path.basename(home).toLowerCase() === 'scripts' ? path.dirname(home) : home;
	const system = path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'msvcp140.dll');
	const cpu = os.cpus();
	return {
		sift: app.getVersion(),
		windows: os.release(),
		cpu: cpu[0]?.model.trim() ?? 'unknown',
		threads: cpu.length,
		memory_gb: Math.round(os.totalmem() / 2 ** 30),
		cpp_runtime: fileVersion(path.join(home, 'msvcp140.dll')) ?? 'none beside Sift',
		cpp_runtime_windows: fileVersion(system) ?? 'none',
		...nativeVersions(path.join(root, 'Lib', 'site-packages')),
		optional_features: held ? 'held off for this start' : 'as set in Settings'
	};
}

/** Write this start's facts to the app's log, and hand them back. */
export function writeFacts(python: string, held: boolean): Fields {
	const facts = startFacts(python, held);
	log.info('backend.facts', facts);
	return facts;
}
