<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'VerbMore',
		category: 'composition',
		role: 'the door at the end of a selection bar, holding the verbs the bar does not name',
		basis: 'composes:MenuButton,VerbMenuItems',
		states: ['closed', 'open']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: both halves of this are already the library one level down: `MenuButton` is
	its DropdownMenu and `VerbMenuItems` its rows; this is the join. */

	/* Everything the bar does not name, one press away: the same rows as the right-click menu.
	   Nothing is drawn when there is nothing behind it. */
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import VerbMenuItems from '$lib/components/common/VerbMenuItems.svelte';
	import type { Verb } from '$lib/components/common/verbs';
	import Icon from '$lib/components/Icon.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';

	interface Props {
		/** The rest of the bar's verbs: `barShape().rest`, never a list written out by a caller. */
		verbs: readonly Verb[];
		/** What every row here acts on, resolved once by the bar so no two rows can disagree. */
		ids: string[];
		/** What the bar counts ("file", "person"), so the name says whose bar it is. */
		noun?: string;
		/** Its plural, where an "s" will not do: "person" is the one that needs it. */
		plural?: string;
	}

	let { verbs, ids, noun = 'selection', plural }: Props = $props();

	/* Counted, as the bar's own count is: one or none is "this". */
	const label = $derived(
		ids.length > 1
			? `More for ${ids.length.toLocaleString()} ${plural ?? `${noun}s`}`
			: `More for this ${noun}`
	);
</script>

{#if verbs.length > 0}
	{#if phoneWidth.yes}
		<!-- At a phone's width the door is the bar's fifth worded column. -->
		<MenuButton {label}>
			{#snippet trigger({ props })}
				<button {...props} type="button" aria-label={label}>
					<Icon name="more_vert" size={18} />
					More
				</button>
			{/snippet}
			<VerbMenuItems {verbs} {ids} />
		</MenuButton>
	{:else}
		<MenuButton {label}>
			<VerbMenuItems {verbs} {ids} />
		</MenuButton>
	{/if}
{/if}
