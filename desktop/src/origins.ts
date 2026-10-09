/* Which pages are allowed to be Sift, and which are just the internet. */

import { URL } from 'node:url';

import { ORIGIN as LOCAL_ORIGIN } from './backend';
import type { DesktopSettings } from './settings';

/** Normalised to scheme + host + port, so "http://x:5171/" and "http://x:5171" are one thing. */
export function normaliseOrigin(input: string): string | null {
	try {
		const url = new URL(input.includes('://') ? input : `http://${input}`);
		if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
		return url.origin;
	} catch {
		return null;
	}
}

/** Whether an IPv4 address is one a home network hands out: 10.x, 172.16-31.x or 192.168.x. */
export function isHomeNetworkAddress(address: string): boolean {
	const parts = address.split('.');
	if (parts.length !== 4 || parts.some((part) => !/^\d{1,3}$/.test(part))) return false;
	const [a, b] = parts.map(Number) as [number, number, number, number];
	if (a === 10) return true;
	if (a === 192) return b === 168;
	// 172.16 through 172.31, and NOT 172.15 or 172.32, the boundary this is always got wrong at.
	return a === 172 && b >= 16 && b <= 31;
}

/** Null when a server may be reached at this origin, else the sentence saying why not. */
export function plainHttpRefusal(origin: string): string | null {
	let url: URL;
	try {
		url = new URL(origin);
	} catch {
		return 'That does not look like an address.';
	}
	if (url.protocol === 'https:') return null;
	if (url.protocol !== 'http:') return 'That does not look like an address.';
	/* The URL parser has already put the host in one canonical form: lower case, and an IPv4
	   address written as a number or in hex spelled out as four decimals. */
	const host = url.hostname;
	const local =
		host === '[::1]' ||
		/^127\.\d+\.\d+\.\d+$/.test(host) ||
		isHomeNetworkAddress(host) ||
		(!host.startsWith('[') && !host.includes('.')) ||
		host.endsWith('.local');
	return local
		? null
		: 'That address is outside your local network, so it has to start with https://. Plain http is only used between devices on the same network.';
}

/** Which kind of trusted page this is: this machine's own Sift, a saved server, or neither. */
export type Reach = 'local' | 'remote';

/** How far a page at this URL may reach into the shell. */
export function reachOf(settings: DesktopSettings, url: string): Reach | null {
	const origin = normaliseOrigin(url);
	if (origin === null) return null;
	if (origin === LOCAL_ORIGIN) return 'local';
	return trustedOrigins(settings).has(origin) ? 'remote' : null;
}

/** Every origin this shell will treat as Sift: the local backend plus the saved servers. */
export function trustedOrigins(settings: DesktopSettings): Set<string> {
	const trusted = new Set<string>([LOCAL_ORIGIN]);
	for (const server of settings.servers) {
		const origin = normaliseOrigin(server.origin);
		if (origin !== null && plainHttpRefusal(origin) === null) trusted.add(origin);
	}
	return trusted;
}

/** The header every response from a Sift carries, and the value it carries. */
export const SIFT_MARK_HEADER = 'x-sift';
export const SIFT_MARK_VALUE = '1';

/** The address a server announces on the network, or null when it is on no network: the machine's
 * address and the port the backend listens on, as a browser on another machine would type them. */
export function shareAddress(host: string | null, port: number): string | null {
	return host === null ? null : `http://${host}:${port}`;
}

/** Whether what answered at an address is a Sift, judged from one response to `/health`. */
export function looksLikeSift(status: number, headers: Headers, body: string): string | null {
	if (headers.get(SIFT_MARK_HEADER) !== SIFT_MARK_VALUE) {
		return 'Something answered at that address, but it was not Sift.';
	}
	if (status === 401) return null;
	if (!(status >= 200 && status < 300)) {
		return `Something answered at that address, but it was not Sift (it said ${status}).`;
	}
	try {
		const parsed: unknown = JSON.parse(body);
		if (typeof parsed === 'object' && parsed !== null && 'status' in parsed) return null;
	} catch {
		/* not JSON: not Sift's health answer */
	}
	return 'Something answered at that address, but it did not answer the way Sift does.';
}

/* Asked for every navigation and every window the page tries to open, so a redirect chain cannot
 * walk out of a trusted origin while keeping the bridge attached. */
export function isTrusted(settings: DesktopSettings, url: string): boolean {
	return reachOf(settings, url) !== null;
}
