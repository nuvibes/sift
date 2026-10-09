/* Settings > Sites and Tunnels > Swap tunnels: the tunnel a join dials through, said by name and
   opened on the swap screen. */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { SettingSection } from '$lib/settings-ui/settings';
import { words } from '$lib/design/testing.svelte';
import SwapTunnels from './SwapTunnels.svelte';
import { COPY } from './Sites.search';
import { Tunnels, type Tunnel } from './tunnels-state.svelte';

const fetchSettings = vi.fn<() => Promise<SettingSection[]>>();
vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: () => fetchSettings()
}));

const went = vi.hoisted(() => ({ to: [] as string[] }));
vi.mock('$app/navigation', () => ({
	goto: async (to: string) => {
		went.to.push(to);
	}
}));

function guestIs(value: string) {
	fetchSettings.mockResolvedValue([
		{
			name: 'Privacy',
			settings: [{ key: 'swap.guest_tunnel', value }]
		} as unknown as SettingSection
	]);
}

const tunnel = (id: string, name: string, can_host: boolean | null) =>
	({ id, name, can_host }) as unknown as Tunnel;

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | null = null;

beforeEach(() => {
	went.to = [];
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
});

async function draw(items: Tunnel[]) {
	const tunnels = new Tunnels();
	tunnels.items = items;
	drawn = mount(SwapTunnels, { target: host, props: { tunnels } });
	flushSync();
	await vi.waitFor(() => expect(fetchSettings).toHaveBeenCalled());
	await Promise.resolve();
	flushSync();
}

it('lists no tunnel a second time: whether one can host is said on its row in Tunnels', async () => {
	guestIs('');
	await draw([tunnel('t1', 'Harbor exit', true), tunnel('t2', 'Old config', false)]);
	expect(host.querySelector('[id="sites.swap_tunnels"]')).not.toBeNull();
	const text = words(host);
	expect(text).toContain(COPY.swapTunnels.name);
	expect(text).not.toContain(COPY.tunnels.hosting.can);
	expect(text).not.toContain(COPY.tunnels.hosting.cannot);
	expect(text).not.toContain('Which tunnels can host a swap');
});

it('names the tunnel a join goes through, and opens the swap screen to change it', async () => {
	guestIs('t1');
	await draw([tunnel('t1', 'Harbor exit', true)]);
	await vi.waitFor(() =>
		expect(words(host)).toContain(COPY.swapTunnels.join.chosen('Harbor exit'))
	);
	const open = host.querySelector<HTMLButtonElement>(
		`button[aria-label="${COPY.swapTunnels.join.openLabel}"]`
	);
	expect(open).not.toBeNull();
	open?.click();
	expect(went.to).toEqual(['/swap']);
});

it('says when the join has no tunnel yet, and when its tunnel was deleted', async () => {
	guestIs('');
	await draw([tunnel('t1', 'Harbor exit', true)]);
	expect(words(host)).toContain(COPY.swapTunnels.join.unchosen);
	if (drawn) unmount(drawn);
	host.innerHTML = '';
	guestIs('gone-id');
	await draw([tunnel('t1', 'Harbor exit', true)]);
	await vi.waitFor(() => expect(words(host)).toContain(COPY.swapTunnels.join.gone));
});
