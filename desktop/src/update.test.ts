/* The update chain, and above all the check that makes it safe to run anything at all. */

import { generateKeyPairSync, sign as signWith } from 'node:crypto';

import { describe, expect, it } from 'vitest';

import {
	PUBLIC_KEY_BASE64,
	compareVersions,
	feedAddress,
	DEFAULT_FEED_URL,
	manifestFrom,
	signatureIsGood
} from './update';

/* A minisign key, in the published form: two bytes naming the algorithm, eight of key id, then
 * the raw Ed25519 key. */
function aKeyPair() {
	const { publicKey, privateKey } = generateKeyPairSync('ed25519');
	const raw = publicKey.export({ format: 'der', type: 'spki' }).subarray(12);
	const keyId = Buffer.from('0123456789abcdef', 'hex');
	return {
		privateKey,
		keyId,
		published: Buffer.concat([Buffer.from('Ed'), keyId, raw]).toString('base64')
	};
}

function signatureFile(
	content: Buffer,
	pair: ReturnType<typeof aKeyPair>,
	{ algorithm = 'Ed', keyId = pair.keyId }: { algorithm?: string; keyId?: Buffer } = {}
): string {
	const header = Buffer.concat([Buffer.from(algorithm), keyId]);
	const raw = Buffer.concat([header, signWith(null, content, pair.privateKey)]);
	return `untrusted comment: signature\n${raw.toString('base64')}\ntrusted comment: x\nrubbish\n`;
}

const CONTENT = Buffer.from('deadbeef  Sift-Setup-1.2.3.exe\n');

describe('signatureIsGood', () => {
	it('accepts a real signature over the real bytes', () => {
		const pair = aKeyPair();
		expect(signatureIsGood(CONTENT, signatureFile(CONTENT, pair), pair.published)).toBe(true);
	});

	/* The one that matters most: the file was changed after it was signed. */
	it('refuses when a single byte of the content differs', () => {
		const pair = aKeyPair();
		const signature = signatureFile(CONTENT, pair);
		const tampered = Buffer.from(CONTENT);
		tampered[0] = (tampered[0] as number) ^ 0x01;

		expect(signatureIsGood(tampered, signature, pair.published)).toBe(false);
	});

	it('refuses a signature made by a different key', () => {
		const mine = aKeyPair();
		const theirs = aKeyPair();

		expect(signatureIsGood(CONTENT, signatureFile(CONTENT, theirs), mine.published)).toBe(false);
	});

	/* A PREHASHED minisign signature (algorithm "ED") signs a BLAKE2b digest of the file, not
	 * the file. */
	it('refuses a prehashed signature', () => {
		const pair = aKeyPair();
		const prehashed = signatureFile(CONTENT, pair, { algorithm: 'ED' });

		expect(signatureIsGood(CONTENT, prehashed, pair.published)).toBe(false);
	});

	it('refuses a signature carrying somebody else key id', () => {
		const pair = aKeyPair();
		const wrongId = signatureFile(CONTENT, pair, {
			keyId: Buffer.alloc(8, 0xff)
		});

		expect(signatureIsGood(CONTENT, wrongId, pair.published)).toBe(false);
	});

	/* Malformed input is ordinary, not exceptional: a truncated download, an HTML error page saved
	 * as a .minisig, an empty file. None of them may throw out of here. */
	it('refuses rubbish without throwing', () => {
		const pair = aKeyPair();
		for (const rubbish of ['', 'untrusted comment: only\n', 'a\nnot base64 !!!\n', 'a\nAAAA\n']) {
			expect(signatureIsGood(CONTENT, rubbish, pair.published)).toBe(false);
		}
	});

	/* A key with the right id and a damaged body is a refusal too, never a throw out of here. */
	it('refuses when the key itself cannot be read', () => {
		const pair = aKeyPair();
		const damaged = Buffer.concat([Buffer.from('Ed'), pair.keyId, Buffer.alloc(3)]).toString(
			'base64'
		);

		expect(signatureIsGood(CONTENT, signatureFile(CONTENT, pair), damaged)).toBe(false);
	});

	/* The default is the shipped key. A refactor that made the parameter required, or defaulted it
	 * to something else, would leave every call site passing nothing and verifying against nothing. */
	it('defaults to the key compiled into the application', () => {
		/* That the constant is a minisign public key, and not that it is a particular one. */
		expect(PUBLIC_KEY_BASE64.startsWith('RW')).toBe(true);
		expect(Buffer.from(PUBLIC_KEY_BASE64, 'base64')).toHaveLength(42);

		const pair = aKeyPair();
		// Signed by a key that is NOT Sift's, checked with the default: must be refused.
		expect(signatureIsGood(CONTENT, signatureFile(CONTENT, pair))).toBe(false);
	});
});

describe('manifestFrom', () => {
	const GOOD = {
		version: '0.2.1',
		installer: 'Sift-0.2.1-x64-setup.exe',
		sha256: 'a'.repeat(64)
	};

	it('reads the three fields a release carries', () => {
		expect(manifestFrom(JSON.stringify(GOOD))).toEqual(GOOD);
	});

	it.each([
		['not JSON', 'nope'],
		['an array', '[]'],
		['a version it cannot order', JSON.stringify({ ...GOOD, version: 'latest' })],
		['an installer that is a path', JSON.stringify({ ...GOOD, installer: '..\\\\evil.exe' })],
		['an installer that is not one', JSON.stringify({ ...GOOD, installer: 'notes.txt' })],
		['a digest that is not SHA-256', JSON.stringify({ ...GOOD, sha256: 'A'.repeat(64) })],
		['a missing field', JSON.stringify({ version: GOOD.version, installer: GOOD.installer })],
		['a field it does not know', JSON.stringify({ ...GOOD, url: 'https://x' })]
	])('refuses %s', (_why, body) => {
		expect(manifestFrom(body)).toBeNull();
	});
});

describe('compareVersions', () => {
	it.each([
		['0.2.0', '0.1.197', 1],
		['0.1.10', '0.1.9', 1],
		['0.2.0', '0.2.0', 0],
		['v0.2.0', '0.2.0', 0],
		['0.1.197', '0.2.0', -1],
		['0.2.0-rc1', '0.2.0', -1],
		['0.2.0', '0.2.0-rc1', 1],
		['1.0.0', '0.99.99', 1],
		['0.2.0-rc2', '0.2.0-rc1', 1],
		['0.2.0-rc1', '0.2.0-rc2', -1]
	])('orders %s against %s', (a, b, sign) => {
		expect(Math.sign(compareVersions(a, b) as number)).toBe(sign);
	});

	it('claims no order for something that is not a version', () => {
		expect(compareVersions('latest', '0.2.0')).toBeNull();
		expect(compareVersions('0.2.0', '')).toBeNull();
	});
});

describe('the feed address', () => {
	/* One address, the same on every installation, with nothing appended: the request the shell
	   and the backend make says nothing about the machine making it. */
	it('is fixed, https and carries nothing about this installation', () => {
		const url = new URL(DEFAULT_FEED_URL);
		expect(url.protocol).toBe('https:');
		expect(url.search).toBe('');
		expect(url.hash).toBe('');
		expect(DEFAULT_FEED_URL).not.toContain('{');
	});

	it('is Sift own unless the settings file names another', () => {
		expect(feedAddress(null)).toBe(DEFAULT_FEED_URL);
		expect(feedAddress('  ')).toBe(DEFAULT_FEED_URL);
		expect(feedAddress('https://mirror.example/latest.json')).toBe(
			'https://mirror.example/latest.json'
		);
	});
});

/* --- the whole chain ------------------------------------------------------------------------- */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as nodePath from 'node:path';

import { afterEach, beforeEach } from 'vitest';

import { fetchAnswers, openedPaths, paths, reply, resetElectronStub } from '../test/electron-stub';
import { applyUpdate } from './update';

const INSTALLER = Buffer.from('this stands in for a 300 MB installer');
const RUNNING = '0.2.0';

function releaseFeed(tag = 'v0.2.1') {
	return {
		tag_name: tag,
		assets: [
			{
				name: 'Sift-0.2.1-x64-setup.exe',
				browser_download_url: 'https://x/setup.exe'
			},
			{
				name: 'Sift-0.2.1-x64-setup.exe.sha256',
				browser_download_url: 'https://x/setup.sha256'
			},
			{
				name: 'Sift-0.2.1-x64-setup.exe.manifest.json',
				browser_download_url: 'https://x/manifest'
			},
			{
				name: 'Sift-0.2.1-x64-setup.exe.manifest.json.minisig',
				browser_download_url: 'https://x/manifest.minisig'
			}
		]
	};
}

function manifest(bytes: Buffer, version = '0.2.1'): Buffer {
	// eslint-disable-next-line @typescript-eslint/no-require-imports
	const { createHash } = require('node:crypto') as typeof import('node:crypto');
	return Buffer.from(
		JSON.stringify({
			version,
			installer: 'Sift-0.2.1-x64-setup.exe',
			sha256: createHash('sha256').update(bytes).digest('hex')
		})
	);
}

let temp: string;

beforeEach(() => {
	resetElectronStub();
	temp = fs.mkdtempSync(nodePath.join(os.tmpdir(), 'sift-update-'));
	paths.temp = temp;
});

afterEach(() => {
	fs.rmSync(temp, { recursive: true, force: true });
});

describe('applyUpdate', () => {
	function queue(
		bytes: Buffer,
		pair: ReturnType<typeof aKeyPair>,
		{ described = manifest(bytes), tag = 'v0.2.1' }: { described?: Buffer; tag?: string } = {}
	) {
		fetchAnswers.push(reply(releaseFeed(tag)));
		fetchAnswers.push(reply(described.toString('utf8')));
		fetchAnswers.push(reply(signatureFile(described, pair)));
		fetchAnswers.push(reply(bytes.toString('utf8')));
	}

	it('downloads, verifies and launches a real release', async () => {
		const pair = aKeyPair();
		queue(INSTALLER, pair);

		const outcome = await applyUpdate('https://feed', pair.published, {
			running: RUNNING
		});

		expect(outcome).toEqual({ ok: true, version: '0.2.1' });
		expect(openedPaths).toHaveLength(1);
		expect(fs.readFileSync(openedPaths[0] as string, 'utf8')).toBe(INSTALLER.toString('utf8'));
	});

	it('stops what it is told to after verifying and before launching', async () => {
		const pair = aKeyPair();
		queue(INSTALLER, pair);
		const launchedWhenStopped: number[] = [];

		const outcome = await applyUpdate('https://feed', pair.published, {
			running: RUNNING,
			beforeLaunch: async () => {
				launchedWhenStopped.push(openedPaths.length);
			}
		});

		expect(outcome).toEqual({ ok: true, version: '0.2.1' });
		expect(launchedWhenStopped).toEqual([0]);
		expect(openedPaths).toHaveLength(1);
	});

	it('leaves once the installer is open, and only then', async () => {
		const pair = aKeyPair();
		queue(INSTALLER, pair);
		const leftWhenOpened: number[] = [];

		const outcome = await applyUpdate('https://feed', pair.published, {
			running: RUNNING,
			afterLaunch: () => {
				leftWhenOpened.push(openedPaths.length);
			}
		});

		expect(outcome).toEqual({ ok: true, version: '0.2.1' });
		expect(leftWhenOpened).toEqual([1]);
	});

	it('stays for a release that does not verify', async () => {
		queue(INSTALLER, aKeyPair());
		let left = 0;

		await applyUpdate('https://feed', aKeyPair().published, {
			running: RUNNING,
			afterLaunch: () => {
				left += 1;
			}
		});

		expect(left).toBe(0);
	});

	it('stops nothing for a release that does not verify', async () => {
		queue(INSTALLER, aKeyPair());
		let stopped = 0;

		await applyUpdate('https://feed', aKeyPair().published, {
			running: RUNNING,
			beforeLaunch: async () => {
				stopped += 1;
			}
		});

		expect(stopped).toBe(0);
		expect(openedPaths).toEqual([]);
	});

	/* THE ONE THAT MATTERS. A manifest signed by somebody else must never reach a launch. */
	it('launches nothing when the signature is not this key', async () => {
		queue(INSTALLER, aKeyPair());

		const outcome = await applyUpdate('https://feed', aKeyPair().published, {
			running: RUNNING
		});

		expect(outcome).toEqual({ ok: false, reason: 'unverified' });
		expect(openedPaths).toEqual([]);
	});

	/* NO ROLLING BACK. Every old release carries a genuine signature, so a feed serving one is
	   refused on its version: at or below the running one is nothing to install. */
	it.each([
		['the same version', '0.2.1', '0.2.1'],
		['an older version', '0.2.0', '0.2.1']
	])('installs nothing for %s', async (_why, version, running) => {
		const pair = aKeyPair();
		queue(INSTALLER, pair, {
			described: manifest(INSTALLER, version),
			tag: `v${version}`
		});
		let stopped = 0;

		const outcome = await applyUpdate('https://feed', pair.published, {
			running,
			beforeLaunch: async () => {
				stopped += 1;
			}
		});

		expect(outcome).toEqual({ ok: false, reason: 'none' });
		expect(openedPaths).toEqual([]);
		expect(stopped).toBe(0);
	});

	it('installs a newer version over the one running', async () => {
		const pair = aKeyPair();
		queue(INSTALLER, pair);

		expect(await applyUpdate('https://feed', pair.published, { running: '0.2.0' })).toEqual({
			ok: true,
			version: '0.2.1'
		});
	});

	/* A checkout has no version, and a release installed over one is not an update of anything. */
	it('installs nothing where the running version is not known', async () => {
		const pair = aKeyPair();
		queue(INSTALLER, pair);

		expect(await applyUpdate('https://feed', pair.published)).toEqual({
			ok: false,
			reason: 'none'
		});
		expect(openedPaths).toEqual([]);
	});

	/* The feed's tag is not signed. It has to say what the signed manifest says, or the version
	   a person was shown is not the version that would install. */
	it('launches nothing when the tag and the signed version disagree', async () => {
		const pair = aKeyPair();
		queue(INSTALLER, pair, { tag: 'v9.9.9' });

		expect(await applyUpdate('https://feed', pair.published, { running: RUNNING })).toEqual({
			ok: false,
			reason: 'unverified'
		});
		expect(openedPaths).toEqual([]);
	});

	/* A signature that is genuine over a manifest naming a DIFFERENT installer's hash. */
	it('launches nothing, and keeps nothing, when the bytes do not match the hash', async () => {
		const pair = aKeyPair();
		queue(Buffer.from('something else entirely'), pair, {
			described: manifest(INSTALLER)
		});

		const outcome = await applyUpdate('https://feed', pair.published, {
			running: RUNNING
		});

		expect(outcome).toEqual({ ok: false, reason: 'unverified' });
		expect(openedPaths).toEqual([]);
		// And it is gone, rather than sitting in a temp folder for somebody to double-click.
		const staged = nodePath.join(temp, 'sift-update');
		expect(fs.existsSync(staged) ? fs.readdirSync(staged) : []).toEqual([]);
	});

	/* The connection dropping half way through the installer: what arrived is not kept to be run. */
	it('launches nothing, and keeps nothing, when the installer stops arriving', async () => {
		const pair = aKeyPair();
		const described = manifest(INSTALLER);
		fetchAnswers.push(reply(releaseFeed()));
		fetchAnswers.push(reply(described.toString('utf8')));
		fetchAnswers.push(reply(signatureFile(described, pair)));
		fetchAnswers.push({
			ok: true,
			body: new ReadableStream<Uint8Array>({
				start(controller) {
					controller.enqueue(INSTALLER.subarray(0, 10));
					controller.error(new Error('the connection dropped'));
				}
			})
		} as unknown as Response);

		const outcome = await applyUpdate('https://feed', pair.published, {
			running: RUNNING
		});

		expect(outcome).toEqual({ ok: false, reason: 'unreachable' });
		expect(openedPaths).toEqual([]);
		/* Not only at the moment it gives up: an open still on its way must not put the file back. */
		await new Promise((resolve) => setTimeout(resolve, 150));
		const staged = nodePath.join(temp, 'sift-update');
		expect(fs.existsSync(staged) ? fs.readdirSync(staged) : []).toEqual([]);
	});

	/* A build made without the signing key carries no manifest, and is never installed. */
	it('says the release is incomplete when it carries no signed manifest', async () => {
		const feed = releaseFeed();
		feed.assets = feed.assets.filter((asset) => !asset.name.includes('manifest'));
		fetchAnswers.push(reply(feed));

		expect(
			await applyUpdate('https://feed', aKeyPair().published, {
				running: RUNNING
			})
		).toEqual({
			ok: false,
			reason: 'incomplete'
		});
	});

	it('says the release is incomplete when the installer it describes is not there', async () => {
		const pair = aKeyPair();
		const feed = releaseFeed();
		feed.assets = feed.assets.filter((asset) => !asset.name.endsWith('.exe'));
		const described = manifest(INSTALLER);
		fetchAnswers.push(reply(feed));
		fetchAnswers.push(reply(described.toString('utf8')));
		fetchAnswers.push(reply(signatureFile(described, pair)));

		expect(await applyUpdate('https://feed', pair.published, { running: RUNNING })).toEqual({
			ok: false,
			reason: 'incomplete'
		});
	});

	it('says so when the feed cannot be read at all', async () => {
		fetchAnswers.push(new Error('ENOTFOUND'));
		expect(
			await applyUpdate('https://feed', aKeyPair().published, {
				running: RUNNING
			})
		).toEqual({
			ok: false,
			reason: 'unreachable'
		});
	});

	it('says so when the feed is not the shape a feed is', async () => {
		fetchAnswers.push(reply('<html>a proxy sign-in page</html>'));
		expect(
			await applyUpdate('https://feed', aKeyPair().published, {
				running: RUNNING
			})
		).toEqual({
			ok: false,
			reason: 'unreachable'
		});
	});
});
