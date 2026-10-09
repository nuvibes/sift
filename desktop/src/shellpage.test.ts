/* The scheme that serves the connect screen, and above all what it will NOT serve. */

import * as path from 'node:path';

import { beforeEach, describe, expect, it } from 'vitest';

import {
	fetched,
	protocolHandlers,
	protocolsHandled,
	resetElectronStub,
	schemesDeclared
} from '../test/electron-stub';
import {
	SHELL_ORIGIN,
	SHELL_SCHEME,
	declareShellScheme,
	resolveWithin,
	serveShellPages
} from './shellpage';

const ROOT = path.resolve('C:', 'sift', 'web');

describe('resolveWithin', () => {
	it('serves a real file inside the client folder', () => {
		expect(resolveWithin(ROOT, '/_app/immutable/entry/app.js')).toBe(
			path.join(ROOT, '_app', 'immutable', 'entry', 'app.js')
		);
	});

	/* The client routes itself, so `/connect` is not a file. */
	it('falls back to the page for an address with no file extension', () => {
		for (const route of ['/', '/connect', '/browse/deep/link']) {
			expect(resolveWithin(ROOT, route)).toBe(path.join(ROOT, 'index.html'));
		}
	});

	it('refuses a path that climbs out of the folder', () => {
		for (const escape of ['/../secret.txt', '/../../Windows/System32/config/SAM', '/a/../../x.js']) {
			expect(resolveWithin(ROOT, escape)).toBeNull();
		}
	});

	/* Encoded, because the check is on the RESOLVED path and not on the text: `%2e%2e` is the same
	 * climb wearing a different spelling, and a check that read the text would let it through. */
	it('refuses a climb hidden behind an escape sequence', () => {
		expect(resolveWithin(ROOT, '/%2e%2e/secret.txt')).toBeNull();
		expect(resolveWithin(ROOT, '/%2e%2e%2fsecret.txt')).toBeNull();
	});

	it('refuses a path carrying a NUL', () => {
		expect(resolveWithin(ROOT, '/index.html%00.png')).toBeNull();
	});

	it('refuses an address it cannot decode', () => {
		expect(resolveWithin(ROOT, '/%E0%A4%A')).toBeNull();
	});

	/* A folder whose name merely STARTS with the root's is a different folder. */
	it('refuses a sibling folder whose name starts the same way', () => {
		expect(resolveWithin(ROOT, '/../web-old/index.html')).toBeNull();
	});
});

describe('the scheme', () => {
	it('has one host, so there is only one shape of address to reason about', () => {
		expect(SHELL_ORIGIN).toBe(`${SHELL_SCHEME}://app`);
	});
});

describe('serving the connect screen', () => {
	beforeEach(() => {
		resetElectronStub();
	});

	it('is declared as a real, secure origin before anything is served', () => {
		/* `standard` makes it a real origin rather than an opaque one, which is what lets the
		   client's own `default-src 'self'` policy match its own files; `secure` puts it in the
		   same class as https, so the parts of the platform that refuse to work in an insecure
		   context work here. */
		declareShellScheme();

		expect(schemesDeclared).toEqual([
			{
				scheme: SHELL_SCHEME,
				privileges: { standard: true, secure: true, supportFetchAPI: true }
			}
		]);
	});

	it('serves a file inside the client folder and nothing outside it', async () => {
		serveShellPages();
		expect(protocolsHandled).toEqual([SHELL_SCHEME]);
		const handle = protocolHandlers.get(SHELL_SCHEME)!;

		await handle(new Request(`${SHELL_ORIGIN}/_app/immutable/entry/app.js`));

		expect(fetched).toHaveLength(1);
		expect(fetched[0].startsWith('file:')).toBe(true);
		expect(fetched[0].endsWith('/_app/immutable/entry/app.js')).toBe(true);
	});

	it('refuses a host that is not the one host', async () => {
		/* Without this, `sift-shell://anything/../../secret` is a URL somebody could put in
		   front of this handler, and the path check below it is written against one root. */
		serveShellPages();
		const handle = protocolHandlers.get(SHELL_SCHEME)!;

		const answer = await handle(new Request(`${SHELL_SCHEME}://anything/app.js`));

		expect(answer.status).toBe(404);
		expect(fetched).toEqual([]);
	});

	it('refuses a path that climbs out of the client folder', async () => {
		/* The escape, asked of the HANDLER rather than only of the pure function it calls: a
		   check that is written and not wired is the shape this whole file exists to refuse. */
		serveShellPages();
		const handle = protocolHandlers.get(SHELL_SCHEME)!;

		const answer = await handle(new Request(`${SHELL_ORIGIN}/..%2f..%2fsecret.txt`));

		expect(answer.status).toBe(404);
		expect(fetched).toEqual([]);
	});
});
