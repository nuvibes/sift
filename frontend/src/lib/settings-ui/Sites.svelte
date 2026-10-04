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

	/*
	 * Settings > Sites and Tunnels: how Sift reaches a Site, and whose cookies it holds for one.
	 *
	 * ## One question
	 *
	 * Whether the library is offered to the rest of the network is General's, which browser links
	 * open in is General's too, and the stash-box rules are a section of their own. What is left here
	 * is how Sift reaches a Site.
	 *
	 * The name is Sift's own noun and the vocabulary gate insists on it: `site` and `sites` both
	 * map to Site.
	 *
	 * ## Why the routing and the supported list are here
	 *
	 * Which tunnel a Site uses is a fact about the Site, so it is here with the tunnel it names,
	 * not under Downloads, which is about Sift's own behaviour. The supported list is here too,
	 * last, since it is reference material rather than anything to set.
	 *
	 * ## Why there is no cookie form here
	 *
	 * The download that actually stops for want of cookies is on another screen entirely, so a form
	 * here would send somebody to Settings, to a form that knows nothing about the download they
	 * had come from. The paste box, the read-back and the forget dialog are `CookiesSheet`, opened
	 * from three places including this one, and this pane keeps the one thing that is genuinely its
	 * own: saying that the section exists and opening it.
	 *
	 * The word is cookies, and never the vocabulary of accounts. Nobody gives Sift an account here:
	 * no username, no password, nothing that could sign in anywhere on their behalf.
	 */

	/* Read here as well as in the sheet, and it is not a duplicate read: this pane says how many
	   Sites have cookies saved without opening anything, which is the one question somebody
	   scrolling past this section is asking. */
	const connections = new Connections();
	connections.follow();
	/* One reader for the tunnel list, shared with the section below, so unlocking can refresh the
	   thing it just started. */
	const tunnels = new Tunnels();

	let sheetOpen = $state(false);

	onMount(() => connections.load());

	/** How many Sites have cookies, in a sentence. A count with no noun is a number nobody reads. */
	const saved = $derived(connections.items.length);
	const summary = $derived(saved === 0 ? COPY.cookies.none : COPY.cookies.saved(saved));
</script>

<!-- The sheet below seals what it is given with the master key, so it cannot be opened until the
     key is back. Asked once, above it. -->
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

<!-- Which Site goes out through which tunnel. Directly under the tunnels it names, which is the
     whole reason it is here and not under Downloads. -->
<Routing />

<!-- The tunnels a swap goes through: which can host one, and the one a join dials through. Under
     the Site routing, because both are about which tunnel carries what. -->
<SwapTunnels {tunnels} />

<!-- What each Site supports, needs and is known to refuse. Last: nothing on it can be changed, and
     it is what somebody reads when a paste from one of these did not do what they expected. -->
<SupportedSitesSection />

<!-- The one sheet, opened here with nobody named: this door is a list of every Site, where the
     download queue's door opens it on the Site that stopped. -->
<CookiesSheet bind:open={sheetOpen} site={null} onsaved={() => void connections.load()} />
