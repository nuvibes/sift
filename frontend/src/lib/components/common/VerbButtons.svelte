<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'VerbButtons',
		category: 'composition',
		role: 'a declared list of verbs drawn as buttons',
		basis: 'composes:Button,MenuButton',
		states: ['default', 'with a group']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is nothing left here for it to do. This renders a declared list as
	buttons and hands the one control with behaviour to `RatingChip`; a selection bar's buttons
	are ordinary tab stops, not a Toolbar. */
	/*
	 * A declared list of verbs as a selection bar's buttons, so a verb cannot reach the bar alone.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import RatingChip from '$lib/components/common/RatingChip.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import VerbMenuItems from '$lib/components/common/VerbMenuItems.svelte';
	import type { Verb } from '$lib/components/common/verbs';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';

	interface Props {
		verbs: readonly Verb[];
		/** What every verb here acts on, resolved once by the bar so no two buttons can disagree. */
		ids: string[];
	}

	let { verbs, ids }: Props = $props();
</script>

{#each verbs as verb (verb.id)}
	{#if verb.children}
		<!-- A group is one button opening onto VerbMenuItems, the right-click's rows. -->

		{#if phoneWidth.yes}
			<!-- At a phone's width, a column like the rest, opening a sheet. -->
			<MenuButton label={verb.label}>
				{#snippet trigger({ props })}
					<button {...props} type="button">
						<Icon name={verb.icon} size={18} filled={verb.filled ?? false} />
						{verb.label}
					</button>
				{/snippet}
				<VerbMenuItems verbs={verb.children} {ids} />
			</MenuButton>
		{:else}
			<MenuButton label={verb.label} words={verb.label}>
				<VerbMenuItems verbs={verb.children} {ids} />
			</MenuButton>
		{/if}
	{:else if verb.stars}
		<!-- The rating as RatingChip, the mark alone; a word under it only on a phone. -->

		<RatingChip
			rating={verb.rating ?? null}
			onchange={(value) => verb.rate?.(ids, value)}
			label={verb.label}
			words={phoneWidth.yes ? verb.label : undefined}
		/>
	{:else if verb.disabled && verb.why}
		<!-- An offered verb that cannot be answered says why. -->

		<Tooltip label={verb.why}>
			<button type="button" class="unanswerable {verb.destructive ? 'destructive' : ''}" disabled>
				<Icon name={verb.icon} size={18} filled={verb.filled ?? false} />
				{verb.label}
			</button>
		</Tooltip>
	{:else}
		<button
			type="button"
			class={verb.destructive ? 'destructive' : undefined}
			disabled={verb.disabled ?? false}
			onclick={() => verb.run?.(ids)}
		>
			<Icon name={verb.icon} size={18} filled={verb.filled ?? false} />
			{verb.label}
		</button>
	{/if}
{/each}

<style>
	/* A disabled button lets the pointer through, so its tooltip can be read. */
	.unanswerable {
		pointer-events: none;
	}
</style>
