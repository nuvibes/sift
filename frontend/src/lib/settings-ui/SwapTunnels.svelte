<script lang="ts">
	/* Settings > Sites and Tunnels > Swap tunnels: the tunnel this Sift dials through when it
	 * JOINS a swap (`swap.guest_tunnel`). */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { fetchSettings } from '$lib/settings-ui/settings';
	import ActionRow from './ActionRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { COPY as PANE } from './Sites.search';
	import { Tunnels } from './tunnels-state.svelte';

	const COPY = PANE.swapTunnels;

	/** The pane's one reader of the tunnel list, shared with the Tunnels block above. */
	let { tunnels = new Tunnels() }: { tunnels?: Tunnels } = $props();

	/** The setting that holds the join's tunnel, by the key the server declared it under. */
	const GUEST_TUNNEL_KEY = 'swap.guest_tunnel';

	/* The tunnel id stored for the join: "" is not chosen yet, and undefined is a server that
	   does not declare the setting at all, which reads the same way to a person. */
	let guest = $state<string | undefined>(undefined);

	async function readGuest(): Promise<void> {
		try {
			for (const section of await fetchSettings()) {
				const entry = section.settings?.find((one) => one.key === GUEST_TUNNEL_KEY);
				if (entry) guest = typeof entry.value === 'string' ? entry.value : '';
			}
		} catch {
			// The row says "not chosen yet", which is what an unread choice is to anybody here.
		}
	}

	onMount(() => void readGuest());
	whenChanged(settingChanges, () => void readGuest());

	/** What the join row says: the tunnel by name, not chosen, or chosen and since deleted. */
	const joinSays = $derived.by(() => {
		if (!guest) return COPY.join.unchosen;
		const named = tunnels.items.find((one) => one.id === guest);
		return named ? COPY.join.chosen(named.name) : COPY.join.gone;
	});
</script>

<SettingGroup id="sites.swap_tunnels" heading={COPY.name} help={COPY.lede}>
	<ActionRow
		id="sites.swap_join"
		label={COPY.join.name}
		help={joinSays}
		action={COPY.join.open}
		actionLabel={COPY.join.openLabel}
		onclick={() => void goto('/swap')}
	/>
</SettingGroup>
