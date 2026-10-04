/* The shell's security boundary, tested as a boundary.
 *
 * Every case here is a way somebody could end up with the preload attached to a page that is not
 * Sift. That is the only thing this module can get wrong, so it is the only thing these assert.
 */

import { describe, expect, it } from 'vitest';

import { ORIGIN as LOCAL_ORIGIN } from './backend';
import {
	isHomeNetworkAddress,
	isTrusted,
	looksLikeSift,
	normaliseOrigin,
	plainHttpRefusal,
	reachOf,
	shareAddress,
	trustedOrigins
} from './origins';
import { fresh, type DesktopSettings } from './settings';

function settings(...origins: string[]): DesktopSettings {
	return {
		...fresh(),
		mode: 'client',
		servers: origins.map((origin, i) => ({ label: `server ${i}`, origin }))
	};
}

describe('normaliseOrigin', () => {
	it('assumes http for a bare address, because that is what somebody types', () => {
		expect(normaliseOrigin('192.168.1.20:5171')).toBe('http://192.168.1.20:5171');
	});

	it('reduces a URL to scheme, host and port', () => {
		expect(normaliseOrigin('http://sift.local:5171/browse?q=1#x')).toBe('http://sift.local:5171');
	});

	it('reads a trailing slash and no trailing slash as one thing', () => {
		expect(normaliseOrigin('http://x:5171/')).toBe(normaliseOrigin('http://x:5171'));
	});

	it('keeps https', () => {
		expect(normaliseOrigin('https://sift.example.com')).toBe('https://sift.example.com');
	});

	/* file: is the one that matters. A file:// page granted the preload would be reading the disk
	 * with the bridge attached, which is the whole boundary gone in one step. */
	it.each(['file:///C:/Windows/System32/config', 'javascript:alert(1)', 'data:text/html,x'])(
		'refuses %s',
		(hostile) => {
			expect(normaliseOrigin(hostile)).toBeNull();
		}
	);

	it('refuses something that is not an address at all', () => {
		expect(normaliseOrigin('   ')).toBeNull();
	});
});

describe('looksLikeSift', () => {
	const marked = new Headers({ 'x-sift': '1', 'content-type': 'application/json' });

	it('accepts a Sift that answered its health line', () => {
		expect(looksLikeSift(200, marked, '{"status":"ok"}')).toBeNull();
	});

	it('accepts a Sift that wants a sign-in', () => {
		expect(looksLikeSift(401, marked, '{"detail":"sign in"}')).toBeNull();
	});

	/* The case that matters: some other program on the network answering 200. Without the mark
	 * it would become a trusted origin with the whole bridge attached. */
	it('refuses a host that answers 200 without the mark', () => {
		expect(looksLikeSift(200, new Headers(), '{"status":"ok"}')).toMatch(/was not Sift/);
	});

	it('refuses a 401 without the mark', () => {
		expect(looksLikeSift(401, new Headers(), '')).toMatch(/was not Sift/);
	});

	it('refuses a marked answer whose body is not a health line', () => {
		expect(looksLikeSift(200, marked, '<html>')).toMatch(/did not answer the way Sift does/);
	});

	it('names the status when a marked host answers something else', () => {
		expect(looksLikeSift(503, marked, '')).toContain('503');
	});
});

describe('trustedOrigins', () => {
	it('always holds the local backend, even with nothing saved', () => {
		expect(trustedOrigins(settings())).toEqual(new Set([LOCAL_ORIGIN]));
	});

	it('holds a saved server as its normalised origin', () => {
		expect(trustedOrigins(settings('http://10.0.0.5:5171/browse'))).toContain('http://10.0.0.5:5171');
	});

	/* A hand-edited settings file can hold anything. A row that does not normalise is dropped and
	 * the rest still work. The alternative is one bad line locking somebody out of every server
	 * they saved. */
	it('drops a saved row that is not an address, and keeps the others', () => {
		const trusted = trustedOrigins(settings('file:///etc/passwd', 'http://good:5171'));
		expect(trusted).toContain('http://good:5171');
		expect(trusted.size).toBe(2);
	});
});

describe('isTrusted', () => {
	it('trusts any page on a saved origin, because a page is not an origin', () => {
		expect(isTrusted(settings('http://10.0.0.5:5171'), 'http://10.0.0.5:5171/people/42')).toBe(true);
	});

	it('trusts the local backend', () => {
		expect(isTrusted(settings(), `${LOCAL_ORIGIN}/browse`)).toBe(true);
	});

	/* The redirect case: same host, different port, and it is somebody else's server. */
	it('refuses a different port on a trusted host', () => {
		expect(isTrusted(settings('http://10.0.0.5:5171'), 'http://10.0.0.5:9000/')).toBe(false);
	});

	it('refuses http where https was saved', () => {
		expect(isTrusted(settings('https://sift.example.com'), 'http://sift.example.com/')).toBe(false);
	});

	it('refuses an origin nobody saved', () => {
		expect(isTrusted(settings('http://10.0.0.5:5171'), 'https://example.com/login')).toBe(false);
	});
});

describe('the address a share is announced at', () => {
	it('is the machine and the port, as another machine would type them', () => {
		expect(shareAddress('10.0.0.5', 5171)).toBe('http://10.0.0.5:5171');
	});

	it('is nothing at all on a machine that is on no network', () => {
		expect(shareAddress(null, 5171)).toBeNull();
	});
});

/* Plain http stays on the local network. Anywhere else, anyone on the way could change the page the
   shell then gives part of its bridge to, so a server there needs https. */
describe('plain http', () => {
	it.each([
		'http://10.0.0.5:5171',
		'http://172.16.0.9:5171',
		'http://172.31.255.1:5171',
		'http://192.168.1.20:5171',
		'http://127.0.0.1:8080',
		'http://[::1]:5171',
		'http://nas:5171',
		'http://localhost:5171',
		'http://sift.local:5171',
		'https://sift.example.com',
		'https://203.0.113.7:5171'
	])('is accepted for %s', (origin) => {
		expect(plainHttpRefusal(origin)).toBeNull();
	});

	it.each([
		'http://sift.example.com',
		'http://203.0.113.7:5171',
		'http://172.15.0.1:5171',
		'http://172.32.0.1:5171',
		'http://100.64.1.2:5171',
		'http://[2001:db8::1]:5171',
		'http://nas.example.org:5171',
		'ftp://nas:21'
	])('is refused for %s', (origin) => {
		expect(plainHttpRefusal(origin)).not.toBeNull();
	});

	/* The URL parser writes an address given as a number or in hex as four decimals, so a spelling
	   cannot slip a public address past the private ranges. */
	it('reads an address the way the network will, whatever its spelling', () => {
		expect(plainHttpRefusal('http://3405803783:5171')).not.toBeNull();
		expect(plainHttpRefusal('http://0xc0.0xa8.1.1:5171')).toBeNull();
	});

	it('says what to do instead', () => {
		expect(plainHttpRefusal('http://sift.example.com')).toMatch(/https:\/\//);
	});

	it('refuses something that is not an address at all, in words', () => {
		expect(plainHttpRefusal('not an address')).toBe('That does not look like an address.');
	});

	/* An address saved before the rule existed stops being trusted rather than lingering. */
	it('drops a saved plain-http server outside the local network from the trusted set', () => {
		const trusted = trustedOrigins(settings('http://sift.example.com', 'https://sift.example.com'));

		expect(trusted.has('http://sift.example.com')).toBe(false);
		expect(trusted.has('https://sift.example.com')).toBe(true);
	});
});

describe('reachOf', () => {
	it('is local for this machine own backend and remote for a saved server', () => {
		const saved = settings('http://192.168.1.20:5171');

		expect(reachOf(saved, `${LOCAL_ORIGIN}/browse`)).toBe('local');
		expect(reachOf(saved, 'http://192.168.1.20:5171/people/1')).toBe('remote');
		expect(reachOf(saved, 'https://example.com/')).toBeNull();
		expect(reachOf(saved, 'not an address')).toBeNull();
	});
});

describe('isHomeNetworkAddress', () => {
	it.each(['10.0.2.3', '172.16.0.1', '172.31.0.1', '192.168.0.1'])('holds %s', (address) => {
		expect(isHomeNetworkAddress(address)).toBe(true);
	});

	it.each(['172.15.0.1', '172.32.0.1', '192.169.0.1', '100.64.0.1', '8.8.8.8', '10.1.2', 'nas', '10.a.2.3'])(
		'refuses %s',
		(address) => {
			expect(isHomeNetworkAddress(address)).toBe(false);
		}
	);
});
