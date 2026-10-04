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
	   buttons and hands the one control with behaviour (the rating and the chooser it opens) to
	   `RatingChip`, which uses the library's popover. Roving focus would be bits-ui's Toolbar, and a
	   selection bar is not one: its buttons are ordinary tab stops, which is what somebody arriving
	   from the grid with the keyboard expects. What is left is a loop and a site `<button>`. */
	/*
	 * A declared list of verbs, drawn as the buttons in a selection bar.
	 *
	 * Written as a component rather than as a loop inside each bar for one reason: it is what makes
	 * "the bar and the menu offer the same verbs" checkable. Both surfaces render from the same
	 * declaration through one of these two renderers, so a verb cannot be added to the bar alone:
	 * there is no markup in a bar to add it to.
	 *
	 * It has no opinion about which verbs it is given. The list decides that, once, for both.
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
		<!--
			A group, as one button that opens onto what it holds, the same shape the file's own
			screen has. The bar does not flatten "Add to" into five worded buttons, which would be
			most of the strip.

			The rows are `VerbMenuItems`, not a list written here, so this is the same menu the
			right-click one opens on a group: a picking child opens into its list, a plain one is a
			row, and neither surface can gain a row the other lacks. No subject, because a bar never
			addresses one thing in particular (see `VerbMenuItems`).
		-->
		{#if phoneWidth.yes}
			<!-- At a phone's width a group is a column like every verb beside it, its glyph over its
			     word, and what it holds comes up as the sheet every phone menu is. The chevron goes:
			     the bar's own shape there says every column opens something or does something. -->
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
		<!--
			The rating, as the chip everything else shows one as: the mark alone, with no word
			beside it. `RatingChip` reports the value and opens the same `RatingChoices` the
			right-click submenu opens, so there is one place joining a rating to its chooser, and it
			is not here.

			No word under it on a wide window: the star is the mark a rating is drawn as on every
			card, row and menu beside the heart, and the bar is the surface with the least room. On a
			phone the bar's columns are glyph over word, every one of them, so the rating takes its
			word too. The accessible name is
			unchanged: `RatingChip` speaks the label it is handed ("Rating: 3 out of 5", "Rating:
			not rated").
		-->
		<RatingChip
			rating={verb.rating ?? null}
			onchange={(value) => verb.rate?.(ids, value)}
			label={verb.label}
			words={phoneWidth.yes ? verb.label : undefined}
		/>
	{:else if verb.disabled && verb.why}
		<!--
			A verb that is offered and cannot be answered, saying why.

			The tooltip is here rather than at each caller for the reason everything else in this file
			is: a per-screen answer to a general rule ends with some buttons saying why and others
			saying nothing, once there is more than one screen.
		-->
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
	/*
	 * The button under a "why is this off" tooltip lets the pointer through to the tooltip.
	 *
	 * A disabled control takes no pointer events at all, so a pointer resting on it never reaches
	 * the label wrapped around it and the sentence explaining the state could not be read by
	 * anybody. Letting it through costs nothing, because there is nothing to press either way.
	 *
	 * Here rather than at a caller, so no button is left out of it. A class rather than
	 * `button:disabled`, so it
	 * does not sit at the same specificity as the bar's own rules and win or lose by source order.
	 */
	.unanswerable {
		pointer-events: none;
	}
</style>
