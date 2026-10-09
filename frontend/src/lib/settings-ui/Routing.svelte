<script lang="ts">
	import { onMount } from 'svelte';
	import { Badge, Fold, Select } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { DEFAULT_SCOPE, DIRECT, Tunnels } from './tunnels-state.svelte';
	import { COPY as PANE } from './Sites.search';

	const COPY = PANE.routing;

	/* Which way out each site takes. */

	const tunnels = new Tunnels();
	tunnels.follow();

	onMount(() => void tunnels.load());

	const FOLLOWS_DEFAULT = '';

	/** Direct, plus every tunnel by name. The value stored is the tunnel's id, so renaming one does
	 *  not move any site off it. */
	const ways = $derived([
		{ value: DIRECT, label: COPY.direct },
		...tunnels.items.map((tunnel) => ({ value: tunnel.id, label: tunnel.name }))
	]);

	const perSite = $derived([{ value: FOLLOWS_DEFAULT, label: COPY.useDefault }, ...ways]);

	/* What a site pointed at a DELETED tunnel offers. */
	function choicesFor(key: string) {
		if (!tunnels.isOrphaned(key)) return perSite;
		return [{ value: tunnels.siteRoutes[key], label: COPY.deleted }, ...perSite];
	}

	async function report(work: Promise<string | undefined>): Promise<void> {
		const refusal = await work;
		if (refusal) toasts.show(refusal, { tone: 'error' });
	}

	async function setSite(key: string, chosen: string): Promise<void> {
		await report(chosen === FOLLOWS_DEFAULT ? tunnels.clearRoute(key) : tunnels.route(key, chosen));
	}

	/* The tunnel a site is going out through, and whether it is carrying. */

	/* By name, because the catalog's order is the order records were added to it, which is a
	   fact about the source file and about nothing a reader can see. */
	const byName = $derived(
		[...tunnels.sites].sort((first, second) => first.name.localeCompare(second.name))
	);
</script>

<!-- Rows in the pane's own column, not a hand-drawn list. -->
<SettingGroup
	id="sites.routing"
	heading={COPY.name}
	help={tunnels.items.length === 0 ? COPY.nothingYet : COPY.ownIp}
>
	<LabelledRow label={COPY.defaultLabel} help={COPY.defaultHelp}>
		<!-- The default can be orphaned exactly as a Site can, and then every Site that
		     follows it is going nowhere. Named for the same reason. -->
		<Select
			label={COPY.defaultLabel}
			value={tunnels.defaultRoute}
			options={tunnels.defaultIsOrphaned
				? [{ value: tunnels.defaultRoute, label: COPY.deleted }, ...ways]
				: ways}
			onValueChange={(chosen: string) => void report(tunnels.route(DEFAULT_SCOPE, chosen))}
		/>
	</LabelledRow>

	{#if byName.length > 0}
		<!-- The same fold as the supported Sites under it: one shape for one kind of thing. -->
		<Fold summary={COPY.perSite(byName.length)}>
			{#each byName as site (site.key)}
				{#snippet named()}
					<span class="name">
						{site.name}
						<!-- Three states, not two. -->
						{#if tunnels.isOrphaned(site.key)}
							<Badge state="blocked" label={COPY.itsDeleted} />
						{:else if tunnels.tunnelFor(site.key)?.up}
							<Badge state="done" label={COPY.using(tunnels.tunnelFor(site.key)?.name ?? '')} />
						{:else if tunnels.tunnelFor(site.key)}
							<Badge state="blocked" label={COPY.down(tunnels.tunnelFor(site.key)?.name ?? '')} />
						{/if}
					</span>
				{/snippet}
				<LabelledRow name={named}>
					<Select
						value={tunnels.siteRoutes[site.key] ?? FOLLOWS_DEFAULT}
						options={choicesFor(site.key)}
						label={COPY.connectionFor(site.name)}
						onValueChange={(chosen: string) => void setSite(site.key, chosen)}
					/>
				</LabelledRow>
			{/each}
		</Fold>
	{/if}
</SettingGroup>

<style>
	/* The Site's name and whichever badge says what its tunnel is doing, on one line. */
	.name {
		/* A GRID, not a flex row. */
		display: grid;
		grid-template-columns: minmax(0, 9rem) auto;
		align-items: center;
		gap: var(--space-2);
		justify-content: start;
	}
</style>
