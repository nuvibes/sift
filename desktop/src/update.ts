/* Updating the desktop application: download, verify, launch. On one click, and never on its own.
 *
 * THE PAGE CANNOT NAME ANYTHING HERE. `applyUpdate` takes no arguments from the page at all: no
 * address, no version, no file. The shell reads the release feed itself and launches only an
 * installer described by a manifest signed with Sift's own key: the version, the installer's name
 * and its SHA-256, all under the one signature. A verb that accepted a URL would be a verb for
 * "download this and run it", which is remote code execution with a friendly name on it.
 *
 * Nothing here restarts Sift, replaces a file, or runs in the background. The installer is LAUNCHED:
 * the person sees it, agrees to it, and it does the rest. An application quietly replacing
 * itself is indistinguishable, from the outside, from one that has been replaced by somebody else.
 */

import { app, net, shell } from 'electron';
import { createHash, verify as verifySignature, createPublicKey } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { closed } from './transfer';

/* Sift's release-signing key, written out here rather than fetched.
 *
 * A key that arrives with the thing it is meant to authenticate proves nothing at all: whoever
 * replaced the download replaces the key beside it. So it is compiled into the application, and the
 * only way to change it is to ship a new application, which itself had to be signed by the key
 * before this one.
 *
 * The private half is never in this repository and cannot be. Losing it means no later release can
 * be verified against this one, and there is no way back, which is the failure worth having,
 * because the alternative is a key that can be replaced from outside, and a key that can be
 * replaced from outside is not a key.
 */
export const PUBLIC_KEY_BASE64 = 'RWQm+3AM2DtyzCk6rdK/3lnoHwbSpaaIvkUnzyyx5eioE8OlcHsjM1RP';

/* Where releases are published: THE one address, for the shell and for the backend it starts,
 * which is handed it (see backend.ts) rather than keeping a copy that could disagree.
 *
 * `feedUrl` in the shell's settings file overrides it, for two real cases rather than as a knob:
 * proving the whole chain against a release served locally, and a self-hoster who mirrors. It
 * changes nothing about safety: a release from any address still has to carry a manifest signed
 * with the key above. */
export const DEFAULT_FEED_URL = 'https://api.github.com/repos/nuvibes/sift/releases/latest';

/** The feed this copy reads: the one in the settings file, or Sift's own. */
export function feedAddress(chosen: string | null): string {
	return chosen !== null && chosen.trim() !== '' ? chosen.trim() : DEFAULT_FEED_URL;
}

/** How long the feed read may take. Nobody is waiting on it; a failure reads as "nothing known". */
const FEED_TIMEOUT_MS = 10_000;

/** The most of a feed that is read. A release document is small; anything larger is not one. */
const MAX_FEED_BYTES = 256 * 1024;

/** What an update turned out to be. Every failure is named, because every one is actionable. */
export type UpdateOutcome =
	| { ok: true; version: string }
	| {
			ok: false;
			reason: 'none' | 'unreachable' | 'incomplete' | 'unverified' | 'failed';
	  };

/** The signed description of one release. Every field is covered by the signature. */
export interface Manifest {
	version: string;
	installer: string;
	sha256: string;
}

interface FeedAsset {
	name: string;
	browser_download_url: string;
}

interface Feed {
	tag_name?: string;
	assets?: FeedAsset[];
}

/* --- Verifying --------------------------------------------------------------------------- */

/**
 * A minisign public key, as Node's crypto can use it.
 *
 * The published form is base64 of: two bytes naming the algorithm ("Ed"), eight bytes of key id,
 * then the 32-byte Ed25519 key. Node will not take a bare key, so it is wrapped in the DER prefix
 * that makes it a SubjectPublicKeyInfo: twelve fixed bytes that say "this is Ed25519".
 */
function publicKey(keyBase64: string): ReturnType<typeof createPublicKey> {
	const raw = Buffer.from(keyBase64, 'base64');
	const DER_PREFIX = Buffer.from('302a300506032b6570032100', 'hex');
	return createPublicKey({
		key: Buffer.concat([DER_PREFIX, raw.subarray(10)]),
		format: 'der',
		type: 'spki'
	});
}

/**
 * Whether `signature` really is this key's signature over `content`.
 *
 * A minisign signature file is two or four lines; the second is base64 of the same ten-byte header
 * followed by the 64-byte Ed25519 signature. Only the plain form is accepted: a prehashed
 * signature (algorithm "ED" rather than "Ed") signs a BLAKE2b digest rather than the bytes, and
 * verifying it as though it signed the bytes would pass a file nobody signed.
 */
export function signatureIsGood(
	content: Buffer,
	signature: string,
	/* The key, defaulting to Sift's own. The parameter exists so a test can prove the PARSING with a
	 * key it made itself. The private half of the real one is deliberately not on any machine that
	 * builds. Nothing in the shipped path passes it: `applyUpdate` takes no key and offers no way to
	 * supply one, so there is no call site a wrong key could arrive through. */
	keyBase64: string = PUBLIC_KEY_BASE64
): boolean {
	const lines = signature.split('\n').filter((line) => line.trim() !== '');
	const encoded = lines[1];
	if (encoded === undefined) return false;
	const raw = Buffer.from(encoded.trim(), 'base64');
	if (raw.length !== 74) return false;
	/* 0x45 0x64 is "Ed": the untrusted-comment form that signs the file itself. */
	if (raw[0] !== 0x45 || raw[1] !== 0x64) return false;
	/* The key id has to be THIS key's, or a signature by some other minisign key would be tested
	 * against ours and simply fail, which is the same answer for a different reason, and worth
	 * telling apart when reading a log. */
	const keyId = Buffer.from(keyBase64, 'base64').subarray(2, 10);
	if (!raw.subarray(2, 10).equals(keyId)) return false;
	try {
		return verifySignature(null, content, publicKey(keyBase64), raw.subarray(10));
	} catch {
		return false;
	}
}

const VERSION_SHAPE = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/;

/**
 * A release manifest, read strictly, or null.
 *
 * Exactly three fields of exactly the right shapes: a version the comparison below can order, an
 * installer that is a bare file name, and a SHA-256 in lower-case hex. Anything else is not a
 * manifest `scripts/release.py` wrote.
 */
export function manifestFrom(body: string): Manifest | null {
	let parsed: unknown;
	try {
		parsed = JSON.parse(body);
	} catch {
		return null;
	}
	if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null;
	const { version, installer, sha256, ...rest } = parsed as Record<string, unknown>;
	if (Object.keys(rest).length > 0) return null;
	if (typeof version !== 'string' || !VERSION_SHAPE.test(version)) return null;
	if (typeof installer !== 'string' || !/^[\w.-]+\.exe$/.test(installer)) return null;
	if (typeof sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(sha256)) return null;
	return { version, installer, sha256 };
}

/**
 * Order two versions: negative when `a` is older, zero when they are the same, positive when newer.
 *
 * The three numbers compare as numbers (0.1.10 is newer than 0.1.9), and a pre-release sorts
 * below the release it leads to. A version this cannot read is null: no ordering is claimed.
 */
export function compareVersions(a: string, b: string): number | null {
	const left = VERSION_SHAPE.exec(a.replace(/^v/, ''));
	const right = VERSION_SHAPE.exec(b.replace(/^v/, ''));
	if (left === null || right === null) return null;
	for (let i = 1; i <= 3; i += 1) {
		const difference = Number(left[i]) - Number(right[i]);
		if (difference !== 0) return difference;
	}
	const [pa, pb] = [left[4], right[4]];
	if (pa === pb) return 0;
	if (pa === undefined) return 1;
	if (pb === undefined) return -1;
	return pa < pb ? -1 : 1;
}

/* --- Fetching -------------------------------------------------------------------------- */

async function read(url: string, limit: number): Promise<Buffer | null> {
	try {
		const response = await net.fetch(url, {
			signal: AbortSignal.timeout(FEED_TIMEOUT_MS),
			headers: { accept: 'application/json, text/plain, */*' }
		});
		if (!response.ok) return null;
		const body = Buffer.from(await response.arrayBuffer());
		return body.byteLength > limit ? null : body;
	} catch {
		return null;
	}
}

/** Stream one file to disk. Separate from `read` because an installer is far too big to hold. */
async function download(url: string, target: string): Promise<boolean> {
	let opened: fs.WriteStream | null = null;
	try {
		const response = await net.fetch(url);
		if (!response.ok || response.body === null) return false;
		const out = fs.createWriteStream(target);
		opened = out;
		out.on('error', () => {});
		const reader = response.body.getReader();
		for (;;) {
			const { done, value } = await reader.read();
			if (done) break;
			if (value !== undefined) {
				await new Promise<void>((resolve, reject) =>
					out.write(value, (error) => (error ? reject(error) : resolve()))
				);
			}
		}
		await new Promise<void>((resolve) => out.end(resolve));
		return true;
	} catch {
		if (opened !== null) await closed(opened);
		fs.rmSync(target, { force: true });
		return false;
	}
}

function sha256Of(file: string): string {
	return createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

/* --- The whole of it --------------------------------------------------------------------- */

/** Where a downloaded installer waits. Its own folder, cleared each time, so nothing accumulates. */
function stagingDirectory(): string {
	return path.join(app.getPath('temp'), 'sift-update');
}

/**
 * Download the newest release, prove it is Sift's and newer, and launch it. One click.
 *
 * THE ORDER IS THE SECURITY PROPERTY. The signature is checked over the manifest, the manifest's
 * version against this copy's, its hash against the installer, and only then is anything launched,
 * so nothing that was not signed by Sift's key, and nothing older than what is running, ever
 * reaches a point where Windows would run it. A feed that lies, a mirror that has been tampered
 * with, an old release served as new and a truncated download all end in a refusal.
 */
/** What the shell does around the launch. */
export interface LaunchHooks {
	/** The version this copy is. A release has to be NEWER to be installed; null (a checkout) means
	 *  there is nothing a release could be an update of, and nothing is installed. */
	running?: string | null;
	/** Run once the installer is verified and before it is launched. The shell stops the backend
	 * here, so the installer never finds a database open and never has to take the process. Not
	 * called for a release that does not verify: nothing is stopped for an installer that will not
	 * run. Told the version about to be installed, which an ask from another computer answers with
	 * before the backend it came through stops. */
	beforeLaunch?: (version: string) => Promise<void>;
	/** After the installer is opened: this copy leaves, so the installer never finds it running.
	 *  The shell's own quit by default; a test hands in a fake. */
	afterLaunch?: () => void;
}

/** Through `before-quit`, which lets a close-to-tray window close, so the installer never finds
 *  this copy running and stops it by force; a test hands in its own. */
function leaveForTheInstaller(hooks: LaunchHooks): void {
	(hooks.afterLaunch ?? (() => app.quit()))();
}

export async function applyUpdate(
	feedUrl: string = DEFAULT_FEED_URL,
	/* The key, defaulting to Sift's own: the same argument as on `signatureIsGood`, and for the
	 * same reason: the private half of the real key is deliberately not on any machine that builds,
	 * so a test that could not supply its own could only prove the parts before the signature. The
	 * verb passes a feed address and nothing else, so there is no call site a key can arrive
	 * through. */
	keyBase64: string = PUBLIC_KEY_BASE64,
	hooks: LaunchHooks = {}
): Promise<UpdateOutcome> {
	const raw = await read(feedUrl, MAX_FEED_BYTES);
	if (raw === null) return { ok: false, reason: 'unreachable' };

	let feed: Feed;
	try {
		feed = JSON.parse(raw.toString('utf8')) as Feed;
	} catch {
		return { ok: false, reason: 'unreachable' };
	}

	const assets = Array.isArray(feed.assets) ? feed.assets : [];
	const named = (suffix: string) =>
		assets.find((asset) => typeof asset?.name === 'string' && asset.name.endsWith(suffix));
	const manifestAsset = named('.exe.manifest.json');
	const signature = named('.exe.manifest.json.minisig');
	if (manifestAsset === undefined || signature === undefined) {
		/* A release with no signed manifest is not an error to shout about (a source-only tag, or
		 * a build made without the signing key, looks exactly like this), but it is not something
		 * to apply either. */
		return { ok: false, reason: 'incomplete' };
	}

	const manifestBody = await read(manifestAsset.browser_download_url, 4096);
	const signatureBody = await read(signature.browser_download_url, 4096);
	if (manifestBody === null || signatureBody === null) return { ok: false, reason: 'unreachable' };

	if (!signatureIsGood(manifestBody, signatureBody.toString('utf8'), keyBase64)) {
		return { ok: false, reason: 'unverified' };
	}
	const manifest = manifestFrom(manifestBody.toString('utf8'));
	if (manifest === null) return { ok: false, reason: 'unverified' };
	/* The feed's tag is not signed; the manifest is. They have to agree, so the version a person is
	 * shown is the version that installs. */
	if (typeof feed.tag_name !== 'string' || compareVersions(feed.tag_name, manifest.version) !== 0) {
		return { ok: false, reason: 'unverified' };
	}
	/* NEWER, NOT MERELY SIGNED. Every old release carries a good signature too, so a feed serving
	 * one would otherwise take this copy back to a version with its faults still in it. */
	const running = hooks.running ?? null;
	const order = running === null ? null : compareVersions(manifest.version, running);
	if (order === null || order <= 0) return { ok: false, reason: 'none' };

	const installer = assets.find((asset) => asset?.name === manifest.installer);
	if (installer === undefined || typeof installer.browser_download_url !== 'string') {
		return { ok: false, reason: 'incomplete' };
	}
	const expected = manifest.sha256;

	const staging = stagingDirectory();
	fs.rmSync(staging, { recursive: true, force: true });
	fs.mkdirSync(staging, { recursive: true });
	/* The name is the manifest's, already held to a bare file name; sanitised again anyway, because
	 * this is about to be written to and then RUN. */
	const target = path.join(staging, path.basename(installer.name).replace(/[^\w.-]+/g, '_'));

	if (!(await download(installer.browser_download_url, target))) {
		return { ok: false, reason: 'unreachable' };
	}
	if (sha256Of(target) !== expected) {
		/* Removed rather than left in place. A file that failed verification sitting in a temp
		 * folder is a file somebody may eventually double-click. */
		fs.rmSync(target, { force: true });
		return { ok: false, reason: 'unverified' };
	}

	/* Verified, and only now: the backend is stopped for an installer that is going to run. The
	 * installer closes whatever is still open, and it does that by taking the process after a
	 * moment: a backend mid-job, taken, is a job re-run at the next start and a log left
	 * unfolded. Stopped here it is asked, and it finishes what it was doing. */
	await hooks.beforeLaunch?.(manifest.version);
	const failure = await shell.openPath(target);
	if (failure !== '') return { ok: false, reason: 'failed' };
	leaveForTheInstaller(hooks);
	return { ok: true, version: manifest.version };
}
