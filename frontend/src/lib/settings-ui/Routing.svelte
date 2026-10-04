<script lang="ts">
	import { onMount } from 'svelte';
	import { Badge, Fold, Select } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { DEFAULT_SCOPE, DIRECT, Tunnels } from './tunnels-state.svelte';
	import { COPY as PANE } from './Sites.search';

	const COPY = PANE.routing;

	/* Which way out each site takes.
	 *
	 * Here rather than beside the importer, and the split is the point: importing a tunnel is
	 * something you do to a CONNECTION, and choosing which site uses it is something you decide
	 * about DOWNLOADING. The tunnels themselves are read here only to name the choices.
	 */

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

	/* What a site pointed at a DELETED tunnel offers.
	 *
	 * The stored id has to stay in the list or the control has no value to be on, and would draw
	 * the raw id as its own label, which is precisely the state to avoid. Naming it says what
	 * happened, and choosing anything else is what clears it.
	 *
	 * Nothing here moves a site off a gone tunnel on its own. That is deliberate and it is the
	 * server's rule: those downloads refuse and say the tunnel is gone, where quietly sending them
	 * out over this machine's own connection would use the one route they were taken off.
	 */
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

	/* The tunnel a site is going out through, and whether it is carrying.
	 *
	 * Two questions, and the chooser beside this only answers the first. A site pointed at a
	 * tunnel that is not connected does not download at all, so the two together are the whole
	 * state, and "set to Sweden" beside a Sweden that is off says nothing about what happens next.
	 * The rule for which tunnel that is lives on the reader, shared with the summary above the
	 * queue, because following the default counts and a second copy of that could forget it.
	 */

	/* By name, because the catalog's order is the order records were added to it, which is a fact
	   about the source file and about nothing a reader can see. A list somebody scans for one
	   Site has to be in the order they would look for it in. */
	const byName = $derived(
		[...tunnels.sites].sort((first, second) => first.name.localeCompare(second.name))
	);
</script>

<!--
	Rows in the pane's own column, not a hand-drawn list.

	A flex row with `space-between` sizes each `Select` to its own longest option, so no two of them
	start at the same place, on a pane whose whole point is one column of controls. `SettingGroup`
	and `LabelledRow` are what every other block on every other pane is made of, and this one too.
-->
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
						<!--
					Three states, not two. "Using X" and "X is not connected" leave out a Site
					pointed at a tunnel that has been DELETED: with only those two the badge would
					vanish and the chooser would draw the raw id, while every download refused with
					nothing on screen to say why.
				-->
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
	/* The Site's name and whichever badge says what its tunnel is doing, on one line. This is
	   the row's `name` snippet, which is compiled in this file's scope: `LabelledRow` reaches it
	   with a `:global(.name)` of its own for the weight and the ink. */
	.name {
		/* A GRID, not a flex row. Flexed, each badge would begin wherever its Site's name ended, so a
		   column of them would step in and out by the length of a word, visible the moment more than
		   one Site has a tunnel. A first track wide enough for the longest name puts every badge
		   on the same line, and `minmax` lets it give way rather than overflow on a narrow pane. */
		display: grid;
		grid-template-columns: minmax(0, 9rem) auto;
		align-items: center;
		gap: var(--space-2);
		justify-content: start;
	}
</style>
