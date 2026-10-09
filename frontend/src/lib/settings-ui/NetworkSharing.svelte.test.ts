/* Whether this library answers the rest of the network. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import NetworkSharing from './NetworkSharing.svelte';
import { NETWORK_SHARING } from './NetworkSharing.search';
import type { Sharing } from '$lib/bridge';

const canShareOnNetwork = vi.hoisted(() => vi.fn(() => true));
const sharing = vi.hoisted(() => vi.fn());
const setSharing = vi.hoisted(() => vi.fn());
const firewall = vi.hoisted(() => vi.fn());
const openFirewall = vi.hoisted(() => vi.fn());
const show = vi.hoisted(() => vi.fn());
const copyText = vi.hoisted(() => vi.fn(async () => true));
const load = vi.hoisted(() => vi.fn(async () => {}));

vi.mock('$lib/bridge', () => ({
	bridge: { canShareOnNetwork, sharing, setSharing, firewall, openFirewall }
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show } }));
vi.mock('$lib/shell/clipboard', () => ({ copyText }));
vi.mock('$lib/shell/session.svelte', () => ({ session: { load } }));

function state(overrides: Partial<Sharing> = {}): Sharing {
	return {
		mode: 'standalone',
		enabled: true,
		live: true,
		address: 'http://10.0.0.4:5171',
		port: 5171,
		...overrides
	} as Sharing;
}

let host: HTMLDivElement;

beforeEach(() => {
	canShareOnNetwork.mockReturnValue(true);
	sharing.mockResolvedValue(state());
	firewall.mockResolvedValue({ state: 'open', networks: ['Private'], scope: 'private' });
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	host.remove();
	vi.clearAllMocks();
});

/** What the pane says, with the markup's own line breaks taken out. */
function said(): string {
	return (host.textContent ?? '').replace(/\s+/g, ' ').trim();
}

async function draw() {
	mount(NetworkSharing, { target: host });
	for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
	flushSync();
	return host;
}

it('draws nothing in a browser, and does not ask the shell either', async () => {
	canShareOnNetwork.mockReturnValue(false);

	await draw();

	expect(sharing).not.toHaveBeenCalled();
	expect(host.textContent).not.toContain('Share this library on my network');
});

it('draws nothing on a client pointed at somebody else s library', async () => {
	sharing.mockResolvedValue(state({ mode: 'client' as Sharing['mode'] }));

	await draw();

	expect(host.textContent).not.toContain('Share this library on my network');
});

/* What turning it on sets off is said BEFORE the press: the restart, and Windows' own prompt for
   the port. */
it('says what Windows will ask before sharing is turned on, and only then', async () => {
	sharing.mockResolvedValue(state({ enabled: false, live: false, address: null }));
	await draw();

	expect(said()).toContain(NETWORK_SHARING.prompts);
	expect(said()).toContain('Windows shows its own prompt asking for your permission');
	host.remove();

	host = document.createElement('div');
	document.body.append(host);
	sharing.mockResolvedValue(state());
	await draw();
	expect(said()).not.toContain(NETWORK_SHARING.prompts);
});

it('shows the address to type on the other computer once it is being shared', async () => {
	await draw();

	expect(host.textContent).toContain('http://10.0.0.4:5171');
	expect(said()).toContain('choose to connect to this library');
});

it('covers the machine part of it until somebody asks, and shows the scheme either way', async () => {
	/* The address of the machine holding the library, on a pane that stays open. */
	await draw();

	const reveal = host.querySelector('button[aria-pressed]');

	expect(reveal?.getAttribute('aria-pressed')).toBe('false');
	expect(reveal?.getAttribute('aria-label')).toBe("Show the whole of this device's address");
	expect(reveal?.textContent, 'the scheme is never the secret').toContain('http://10');
});

it('says so when sharing is ON and the server did not actually start', async () => {
	sharing.mockResolvedValue(state({ enabled: true, live: false }));

	await draw();

	expect(said()).toContain('nothing is shared yet');
});

it('says so when sharing is OFF and the library is still answering, which is the dangerous way round', async () => {
	sharing.mockResolvedValue(state({ enabled: false, live: true }));

	await draw();

	expect(said()).toContain('This library is still shared.');
	expect(host.querySelector('.warn')).not.toBeNull();
});

it('writes the firewall command out literally, with only the port coming from the shell', async () => {
	sharing.mockResolvedValue(state({ port: 5399 }));
	firewall.mockResolvedValue({ state: 'unknown', networks: null, scope: null });

	await draw();

	expect(host.querySelector('pre.command')?.textContent).toBe(
		'New-NetFirewallRule -DisplayName "Sift" -Direction Inbound -LocalPort 5399 ' +
			'-Protocol TCP -Action Allow -Profile Private -RemoteAddress LocalSubnet'
	);
});

it('does not ask Windows about the firewall while nothing is being shared', async () => {
	sharing.mockResolvedValue(state({ enabled: false, live: false }));

	await draw();

	expect(firewall).not.toHaveBeenCalled();
});

/* Windows files a new network as Public unless somebody says otherwise, and a rule on private
 * networks only opens nothing there. */
it('says the network is public when the rule does not reach it, and offers the wider rule', async () => {
	firewall.mockResolvedValue({ state: 'open', networks: ['Public'], scope: 'private' });
	openFirewall.mockResolvedValue({ state: 'open', networks: ['Public'], scope: 'any' });

	await draw();

	expect(said()).toContain("treats this device's network as public");
	expect(said()).not.toContain('lets your other devices reach Sift');
	const allow = [...host.querySelectorAll('button')].find((each) =>
		each.textContent?.includes('Open port on public networks')
	);
	allow?.click();
	for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();

	expect(openFirewall).toHaveBeenCalledWith('any');
	flushSync();
	expect(said()).toContain('lets your other devices reach Sift');
});

it('says the port is open when the rule reaches the network the machine is on', async () => {
	firewall.mockResolvedValue({ state: 'open', networks: ['Public'], scope: 'any' });

	await draw();

	expect(said()).toContain('lets your other devices reach Sift');
});

it('offers to open the port only when Windows says it is shut', async () => {
	firewall.mockResolvedValue({ state: 'closed', networks: ['Private'], scope: null });

	await draw();

	expect(said()).toContain('Open the firewall port');
	expect(said()).toContain('looks like a wrong address');
});

it('copies the address and says whether it went', async () => {
	await draw();

	const copy = [...host.querySelectorAll('button')].find((each) =>
		each.textContent?.includes('Copy')
	);
	copy?.click();
	for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();

	expect(copyText).toHaveBeenCalledWith('http://10.0.0.4:5171');
	expect(show).toHaveBeenCalledWith('Address copied', { tone: 'success' });
});
