import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import Tunnels from './Tunnels.svelte';
import { Tunnels as TunnelsState, type Tunnel } from './tunnels-state.svelte';
import { COPY } from './Sites.search';

/* The address beside a tunnel in Settings is the SERVER it connects to, and it says so: the same
 * words the Downloads table and a download's details put on the same number. Beside the name alone
 * it would read as the address a site sees, which a provider may send out of a different machine.
 */

let host: HTMLElement;
let showing: Record<string, unknown> | null = null;

function tunnel(over: Partial<Tunnel> = {}): Tunnel {
	return {
		id: 't1',
		name: 'Sweden',
		enabled: true,
		running: true,
		up: true,
		draining: false,
		last_handshake_at: 1,
		problem: null,
		endpoint: '198.51.100.7',
		can_host: null,
		...over
	};
}

/** A reader already carrying its answers, which never asks the server. */
function reader(items: Tunnel[]): TunnelsState {
	const tunnels = new TunnelsState();
	tunnels.items = items;
	tunnels.loaded = true;
	tunnels.load = async () => {};
	tunnels.watch = () => () => {};
	return tunnels;
}

function show(items: Tunnel[]) {
	host = document.createElement('div');
	document.body.append(host);
	showing = mount(Tunnels, { target: host, props: { tunnels: reader(items) } });
	flushSync();
}

const words = (node: Element | null) => (node?.textContent ?? '').replace(/\s+/g, ' ').trim();

afterEach(() => {
	if (showing) unmount(showing);
	showing = null;
	host?.remove();
	document.body.innerHTML = '';
});

describe("a tunnel's address in Settings", () => {
	it('is labelled as the tunnel server, and its control names it that way', () => {
		show([tunnel()]);

		const server = host.querySelector('.server');
		expect(words(server)).toMatch(/^Tunnel server 198\./);
		expect(server?.querySelector('button')?.getAttribute('aria-label')).toBe(
			'Show the whole of the address of the server Sweden is connected to'
		);
	});

	it('is not drawn, label and all, for a tunnel that is not connected', () => {
		show([tunnel({ up: false, running: false })]);

		expect(host.querySelector('.server')).toBeNull();
		expect(words(host)).not.toContain('Tunnel server');
	});
});

/* Whether a tunnel can host a swap is the server's answer from the last try (`can_host`), and each
 * row says it in one of three ways. Null is "nobody has tried", which is not the same as "cannot":
 * reading it as false would tell somebody a tunnel they never tried is the wrong kind.
 */
describe('whether a tunnel can host a swap', () => {
	it('says each of the three answers on its own row', () => {
		show([
			tunnel({ id: 't1', name: 'Sweden', can_host: true }),
			tunnel({ id: 't2', name: 'Norway', can_host: false }),
			tunnel({ id: 't3', name: 'Finland', can_host: null })
		]);

		const said = [...host.querySelectorAll('.hosting')].map((node) => words(node));
		expect(said).toEqual([
			'Can host a swap',
			"This configuration can't host a swap because it wasn't created properly.",
			'Not tried for a swap yet'
		]);
	});

	it('is said while the tunnel is off, because it was measured and stays true', () => {
		show([tunnel({ enabled: false, running: false, up: false, can_host: false })]);

		expect(words(host.querySelector('.hosting'))).toBe(
			"This configuration can't host a swap because it wasn't created properly."
		);
	});

	it('explains under the list which tunnel can host, and that downloads pause', () => {
		show([tunnel()]);

		// The sentence, without the note's glyph (whose ligature is text too).
		const note = [...host.querySelectorAll('.note span')].find((node) =>
			words(node).startsWith('A swap needs')
		);
		expect(words(note ?? null)).toBe(
			'A swap needs a tunnel made on a P2P VPN server with port forwarding on. While a swap starts or ends, downloads through that tunnel pause for a moment.'
		);
		// Under the list, not above it: the list comes first in the page's order.
		const list = host.querySelector('.hosting');
		expect(
			list && note && list.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING
		).toBeTruthy();
	});

	it('explains it before any tunnel is imported, when it decides which one to import', () => {
		show([]);

		expect(words(host)).toContain('A swap needs a tunnel made on a P2P VPN server');
	});
});

describe("a tunnel's state pill", () => {
	it('is not drawn beside an off switch, which already says Off', () => {
		show([tunnel({ enabled: false, running: false, up: false })]);
		const row = host.querySelector('.name')?.closest('li');
		expect(words(row ?? null)).toContain('Sweden');
		expect(row?.querySelector('.badge'), 'an Off pill came back').toBeNull();
	});

	it('is drawn for a state the switch cannot say', () => {
		show([tunnel({ up: false, running: false })]);
		const row = host.querySelector('.name')?.closest('li');
		expect(words(row?.querySelector('.badge') ?? null)).toContain(
			COPY.tunnels.states.notConnecting
		);
	});
});
