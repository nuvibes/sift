/* The facts of this device a report needs, written once per start of the backend. */

import { app } from 'electron';
import * as fs from 'node:fs';
import { readFile } from 'node:fs/promises';
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

/** The version in a Windows file's fixed version block; null where it has none. */
function versionIn(data: Buffer): string | null {
	const at = data.indexOf(Buffer.from([0xbd, 0x04, 0xef, 0xfe]));
	if (at < 0 || data.length < at + 16) return null;
	const high = data.readUInt32LE(at + 8);
	const low = data.readUInt32LE(at + 12);
	return `${high >>> 16}.${high & 0xffff}.${low >>> 16}.${low & 0xffff}`;
}

/** A Windows file's version, from its fixed version block; null where it has none. */
export function fileVersion(file: string): string | null {
	try {
		return versionIn(fs.readFileSync(file));
	} catch {
		return null;
	}
}

/** `fileVersion` off the main thread: a runtime library is read whole, which took half a second on
 *  a slow disk while the window waited. */
export async function fileVersionSoon(file: string): Promise<string | null> {
	try {
		return versionIn(await readFile(file));
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

function runtimeHome(python: string): string {
	return path.dirname(python);
}

function systemRuntime(): string {
	return path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'msvcp140.dll');
}

/** The facts of this start: the interpreter is the bundled one, or a checkout's. `versions` are
 *  the two runtime libraries' versions when already read. */
export function startFacts(
	python: string,
	held: boolean,
	versions: { beside: string | null; system: string | null } | null = null
): Fields {
	const home = runtimeHome(python);
	const root = path.basename(home).toLowerCase() === 'scripts' ? path.dirname(home) : home;
	const system = systemRuntime();
	const cpu = os.cpus();
	return {
		sift: app.getVersion(),
		windows: os.release(),
		cpu: cpu[0]?.model.trim() ?? 'unknown',
		threads: cpu.length,
		memory_gb: Math.round(os.totalmem() / 2 ** 30),
		cpp_runtime:
			(versions === null ? fileVersion(path.join(home, 'msvcp140.dll')) : versions.beside) ??
			'none beside Sift',
		cpp_runtime_windows: (versions === null ? fileVersion(system) : versions.system) ?? 'none',
		...nativeVersions(path.join(root, 'Lib', 'site-packages')),
		optional_features: held ? 'held off for this start' : 'as set in Settings'
	};
}

/** Write this start's facts to the app's log, and hand them back. */
export async function writeFacts(python: string, held: boolean): Promise<Fields> {
	const [beside, system] = await Promise.all([
		fileVersionSoon(path.join(runtimeHome(python), 'msvcp140.dll')),
		fileVersionSoon(systemRuntime())
	]);
	const facts = startFacts(python, held, { beside, system });
	log.info('backend.facts', facts);
	return facts;
}
