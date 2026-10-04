<script lang="ts">
	/*
	 * Which way the next thing pasted goes out, as one line under the paste box.
	 *
	 * The switch and Download folder are rows of the page's Options menu
	 * (`DownloadOptions`), with the page's doors: the line that stays is the one fact worth reading
	 * while pasting, not a choice.
	 *
	 * ## Which way out: one line, for the Site the paste is from
	 *
	 * What is worth knowing while pasting is whether THIS link goes out through a tunnel, which
	 * one, and whether it is up. It is not a second copy of Settings, Sites. So a paste from a Site
	 * Sift recognizes gets one line for that Site. With nothing pasted, the line is the glance at
	 * what is being tunnelled: each tunnel in use, whether it is connected, and the Sites on it,
	 * or, where nothing is tunnelled, that every Site uses your own connection. The rule for which
	 * way a Site goes is `Tunnels`', the reader Settings uses, so the two cannot disagree.
	 */
	import { onMount } from 'svelte';
	import { Badge, Button } from '$lib/components/common';
	import { session } from '$lib/shell/session.svelte';
	import { openSettings } from '$lib/settings-ui/settings-view';
	import ExitAddress from '$lib/settings-ui/ExitAddress.svelte';
	import {
		Tunnels,
		addressOf,
		addressPhrase,
		type Tunnel
	} from '$lib/settings-ui/tunnels-state.svelte';

	interface Props {
		/** The Sites the paste in the box is from, by the supported list's key, in paste order. */
		pastedSites?: readonly { key: string; name: string }[];
	}

	let { pastedSites = [] }: Props = $props();

	const tunnels = new Tunnels();

	onMount(() => {
		void tunnels.load();
		return tunnels.watch();
	});

	/** Which way one Site goes, by the rule Settings uses: its own route, else the default. */
	interface Way {
		tunnel: Tunnel | null;
		/** Pointed at a tunnel that has since been deleted. Its downloads refuse until it moves. */
		gone: boolean;
	}

	function wayOf(key: string): Way {
		return {
			tunnel: tunnels.tunnelFor(key),
			gone: key in tunnels.siteRoutes ? tunnels.isOrphaned(key) : tunnels.defaultIsOrphaned
		};
	}

	/** The glance with nothing pasted: each tunnel carrying Sites, and who is on it. */
	interface InUse {
		tunnel: Tunnel;
		/** "Every Site", "Every other Site", or the names of the Sites routed to it. */
		sites: string;
	}

	const inUse = $derived.by((): InUse[] => {
		const own = tunnels.sites.filter((site) => site.key in tunnels.siteRoutes);
		return tunnels.items.flatMap((tunnel) => {
			const named = own
				.filter((site) => tunnels.siteRoutes[site.key] === tunnel.id)
				.map((site) => site.name)
				.sort((one, other) => one.localeCompare(other));
			const isDefault = tunnels.defaultRoute === tunnel.id;
			if (isDefault) {
				return [{ tunnel, sites: own.length > 0 ? 'Every other Site' : 'Every Site' }];
			}
			return named.length > 0 ? [{ tunnel, sites: named.join(', ') }] : [];
		});
	});

	/** Sites pointed at a tunnel that no longer exists, said because they refuse to download: the
	 *  ones routed there by name, and everything else when it is the DEFAULT that was deleted. */
	const stranded = $derived.by((): string | null => {
		const own = tunnels.sites.filter((site) => site.key in tunnels.siteRoutes);
		const named = own
			.filter((site) => tunnels.isOrphaned(site.key))
			.map((site) => site.name)
			.sort((one, other) => one.localeCompare(other));
		if (tunnels.defaultIsOrphaned) named.push(own.length > 0 ? 'Every other Site' : 'Every Site');
		return named.length > 0 ? named.join(', ') : null;
	});
</script>

{#snippet through(tunnel: Tunnel)}
	{@const address = addressOf(tunnel)}
	<!-- The server beside the name, covered as Settings covers it: the same rule and the same
	     control, so the two screens cannot disagree about one tunnel. LABELLED "Tunnel server",
	     because that is what it is: the address the tunnel connects to, which a provider may send
	     out of a different machine entirely. -->
	<span class="tunnel">
		{tunnel.name}
		{#if address}
			<span class="server">
				Tunnel server
				<ExitAddress {address} what={addressPhrase(tunnel.name)} />
			</span>
		{/if}
		{#if tunnel.up}
			<Badge state="done" label="Connected" />
		{:else}
			<Badge state="blocked" label="Not connected" />
		{/if}
	</span>
{/snippet}

<!-- Which way the paste goes out, as one status line under the choices: a fact about the
     connection with the way to change it at its end, not a fourth control. -->
<div class="way">
	<span class="named">Connection</span>
	{#if session.secretsLocked}
		<p class="said">
			Tunnels are locked until you enter your password under Sites. Until then, every download uses
			your own connection.
		</p>
	{:else}
		<ul class="lines">
			{#if pastedSites.length > 0}
				{#each pastedSites as site (site.key)}
					{@const way = wayOf(site.key)}
					<li>
						<span class="site">{site.name}</span>
						{#if way.gone}
							<Badge state="failed" label="Its tunnel was deleted" />
						{:else if way.tunnel === null}
							<span class="said">uses your own connection</span>
						{:else}
							<span class="said">uses</span>
							{@render through(way.tunnel)}
						{/if}
					</li>
				{/each}
			{:else}
				{#each inUse as one (one.tunnel.id)}
					<li>
						{@render through(one.tunnel)}
						<span class="said">for {one.sites}</span>
					</li>
				{:else}
					{#if stranded === null}
						<li><span class="said">Every Site uses your own connection.</span></li>
					{/if}
				{/each}
				{#if stranded !== null}
					<li>
						<Badge state="failed" label="Its tunnel was deleted" />
						<span class="said">for {stranded}</span>
					</li>
				{/if}
			{/if}
		</ul>
	{/if}
	<Button tone="link" size="small" onclick={() => openSettings('sites', 'sites.routing')}>
		Edit
	</Button>
</div>

<style>
	.named {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.said {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* The label, the line (or lines) and the way to change it, on one baseline where they fit,
	   under the paste box and in line with its start. */
	.way {
		display: flex;
		align-items: baseline;
		flex-wrap: wrap;
		gap: var(--space-2);
		margin-block-start: var(--space-3);
		font: var(--text-body-sm);
	}

	.lines {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.lines li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	.site {
		color: var(--sift-ink);
	}

	.tunnel {
		display: inline-flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		color: var(--sift-ink-2);
	}

	/* The label and the address it names stay on one line, so a wrap never leaves "Tunnel server"
	   at the end of one line and the number it labels at the start of the next. */
	.server {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
		white-space: nowrap;
	}
</style>
