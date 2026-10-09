<script lang="ts">
	import { onMount } from 'svelte';
	import { Problem } from '$lib/components/common';
	import CookiesSheet from '$lib/components/downloads/CookiesSheet.svelte';
	import { session } from '$lib/shell/session.svelte';
	import ActionRow from './ActionRow.svelte';
	import Routing from './Routing.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import SupportedSitesSection from './SupportedSites.svelte';
	import SwapTunnels from './SwapTunnels.svelte';
	import { Connections } from './connections-state.svelte';
	import TunnelsSection from './Tunnels.svelte';
	import { Tunnels } from './tunnels-state.svelte';
	import UnlockSecrets from './UnlockSecrets.svelte';
	import { COPY } from './Sites.search';

	/* Settings > Sites and Tunnels: how Sift reaches a Site, and whose cookies it holds for one. */

	/* Read here too, so the pane says how many Sites have cookies without opening anything. */
	const connections = new Connections();
	connections.follow();
	/* One reader for the tunnel list, shared with the section below. */
	const tunnels = new Tunnels();

	let sheetOpen = $state(false);

	onMount(() => connections.load());

	/** How many Sites have cookies, in a sentence. A count with no noun is a number nobody reads. */
	const saved = $derived(connections.items.length);
	const summary = $derived(saved === 0 ? COPY.cookies.none : COPY.cookies.saved(saved));
</script>

<!-- The sheet below seals what it is given with the master key: asked once, above it. -->
<UnlockSecrets onunlocked={() => void tunnels.load()} />

<!-- The count is the row's sentence and Edit its press, on the right where every press is. -->
<SettingGroup id="sites.cookies" heading={COPY.cookies.name} help={COPY.cookies.lede}>
	<Problem message={connections.problem} />
	<ActionRow
		id="sites.cookies-saved"
		label={COPY.cookies.row}
		help={summary}
		action={COPY.cookies.edit}
		icon="edit"
		disabled={session.secretsLocked}
		onclick={() => (sheetOpen = true)}
	/>
</SettingGroup>

<TunnelsSection {tunnels} />

<!-- Which Site goes out through which tunnel, directly under the tunnels it names. -->
<Routing />

<!-- The tunnels a swap goes through: which can host one, and the one a join dials through. -->
<SwapTunnels {tunnels} />

<!-- What each Site supports, needs and is known to refuse. -->
<SupportedSitesSection />

<!-- The one sheet, opened here with nobody named: this door is a list of every Site. -->
<CookiesSheet bind:open={sheetOpen} site={null} onsaved={() => void connections.load()} />
