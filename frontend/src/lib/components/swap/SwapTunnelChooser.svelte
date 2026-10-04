<script lang="ts">
	/* NOT ON THE GALLERY: it reads this install's real tunnels and remembers a choice in its settings. */
	/*
	 * Which of your tunnels a swap goes through, chosen in the open beside the press that uses it.
	 *
	 * One chooser for both sides of a swap. Starting one, it is the tunnel that hosts: each is marked
	 * with whether it can, and the first that can is offered. Joining one, it is the tunnel this
	 * side dials out through: the one chosen last time is offered, and a choice is remembered for the
	 * next (`swap.guest_tunnel`). Never the downloads' default route: a guest's dial through
	 * whatever route downloads use would go out through a tunnel nobody had chosen for it.
	 *
	 * Under it, the server the chosen tunnel is connected to, the way Sites and Tunnels shows it:
	 * the first part plain and the rest covered until asked for (`ExitAddress`).
	 *
	 * Read on arrival and again whenever a setting moves: a tunnel imported, started or removed on
	 * the Sites pane while this is open rings the settings bell.
	 */
	import { onMount } from 'svelte';
	import { Button, Select } from '$lib/components/common';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { fetchSettingValues, saveSettings } from '$lib/settings-ui/settings';
	import { openSettings } from '$lib/settings-ui/settings-view';
	import ExitAddress from '$lib/settings-ui/ExitAddress.svelte';
	import { addressPhrase } from '$lib/settings-ui/tunnels-state.svelte';
	import { canHostWords, GUEST_TUNNEL_KEY, swapTunnels, type SwapTunnel } from './swap';

	interface Props {
		/** `host` to start a swap on it, `guest` to join one through it. */
		side: 'host' | 'guest';
		/** The chosen tunnel's id, or '' while there is none. */
		value: string;
		/** The tunnels as read, for the caller to say "there are none". Null until read. */
		tunnels?: SwapTunnel[] | null;
		/** Said once when they could not be read. */
		onproblem?: (words: string) => void;
		/** A refusal about the tunnel, said under the chooser: the fix is choosing another. */
		error?: string;
	}

	let {
		side,
		value = $bindable(''),
		tunnels = $bindable(null),
		onproblem = undefined,
		error = undefined
	}: Props = $props();

	const options = $derived(
		(tunnels ?? []).map((one) => ({
			value: one.id,
			label: one.name,
			detail: side === 'host' ? canHostWords(one.can_host) : undefined
		}))
	);
	const chosen = $derived((tunnels ?? []).find((one) => one.id === value) ?? null);

	async function read(): Promise<void> {
		try {
			const [read, stored] = await Promise.all([
				swapTunnels(),
				side === 'guest' ? fetchSettingValues() : Promise.resolve(new Map<string, unknown>())
			]);
			tunnels = read;
			if (read.some((one) => one.id === value)) return;
			const remembered = String(stored.get(GUEST_TUNNEL_KEY) ?? '');
			const first =
				(side === 'guest' ? read.find((one) => one.id === remembered) : undefined) ??
				(side === 'host' ? read.find((one) => one.can_host === true) : undefined) ??
				read[0];
			value = first ? first.id : '';
		} catch {
			tunnels = [];
			onproblem?.("Your tunnels couldn't be loaded. Reload the page to try again.");
		}
	}
	onMount(() => void read());
	whenChanged(settingChanges, () => void read());

	/* The guest's choice is this library's from now on: the next join offers it first. */
	function chose(next: string): void {
		value = next;
		if (side === 'guest' && next) void saveSettings({ [GUEST_TUNNEL_KEY]: next }).catch(() => {});
	}
</script>

<div class="chooser">
	<!-- Named on screen: the sentence a join without one is refused with points at it by this word. -->
	<label class="named" for="swap-tunnel-{side}">Tunnel</label>
	<Select
		id="swap-tunnel-{side}"
		{value}
		onValueChange={chose}
		{options}
		label="Tunnel"
		placeholder="Choose a tunnel"
		invalid={Boolean(error)}
		describedBy={error ? `swap-tunnel-${side}-error` : undefined}
	/>
	<!-- Under the box, as a field's error is (`Field`), and announced when it appears. -->
	{#if error}
		<p class="error" id="swap-tunnel-{side}-error" role="alert">{error}</p>
	{/if}
	<div class="under">
		{#if chosen?.endpoint}
			<!-- The same words and the same covering as Sites and Tunnels, so the server a swap goes
			     through reads as the one that screen shows. -->
			<span class="server">
				Tunnel server
				<ExitAddress address={chosen.endpoint} what={addressPhrase(chosen.name)} />
			</span>
		{/if}
		<!-- Where each tunnel says whether it can host a swap: its own row under Tunnels. -->
		<span class="link">
			<Button
				size="small"
				tone="ghost"
				aria-label="Open the tunnels in Sites and Tunnels"
				onclick={() => openSettings('sites', 'sites.tunnels')}>Open tunnel settings</Button
			>
		</span>
	</div>
</div>

<style>
	.chooser {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.named {
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	/* A field's error, as `Field` draws one. */
	.error {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-bad-text);
	}

	.under {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
	}

	/* The link stays on the right when there is no server to name beside it. */
	.link {
		margin-inline-start: auto;
	}

	.server {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
