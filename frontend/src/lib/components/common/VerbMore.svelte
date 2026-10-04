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
	   its DropdownMenu and `VerbMenuItems` is its menu rows. What is here is the JOIN, which is the
	   one thing neither of them can own. */

	/*
	 * Everything the bar does not name, one press away.
	 *
	 * The third renderer beside `VerbButtons` and `VerbMenuItems`, and it is deliberately the
	 * thinnest of the three: it draws no row itself. It opens the app's menu and hands the rows to
	 * the same component the right-click menu uses, so a verb behind these dots is the identical row
	 * it would be anywhere else: a picking one opens out into its list, a destructive one gets the
	 * line in front of it, a disabled one says why.
	 *
	 * ## Why it is a component and not three lines at each bar
	 *
	 * There are seven bars. Written out at each of them, the accessible name, the glyph and the
	 * decision about which rows go inside would be seven copies of one rule, which is the exact
	 * drift `verbs.ts` exists to stop, reappearing one level up. The SPLIT is `barShape`'s and the
	 * door is this; a bar holds neither.
	 *
	 * Nothing is drawn where there is nothing behind it. A door onto an empty menu reads as a
	 * control that has stopped working, and a small bar (a single agree button, a wall with three
	 * verbs) genuinely has nothing left over.
	 */
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
		/**
		 * Whose bar this is, for the accessible name.
		 *
		 * "More" alone tells somebody using a screen reader nothing: three walls may have a bar up
		 * on one screen. The word is what the bar is counting ("file", "person", "face"), which
		 * the bar already holds and already says out loud in its count.
		 */
		noun?: string;
		/** Its plural, where an "s" will not do: "person" is the one that needs it. */
		plural?: string;
	}

	let { verbs, ids, noun = 'selection', plural }: Props = $props();

	/* COUNTED, the way the bar's own "2 people selected" is. "More for this person" with two
	   picked would name one thing to somebody who has chosen several and is told by the same bar
	   that they have. One, or none (a bar drawn over nothing picked), is "this". */
	const label = $derived(
		ids.length > 1
			? `More for ${ids.length.toLocaleString()} ${plural ?? `${noun}s`}`
			: `More for this ${noun}`
	);
</script>

{#if verbs.length > 0}
	{#if phoneWidth.yes}
		<!-- At a phone's width the door is the fifth column of the bar, worded like the four beside
		     it, and the rest come up as a sheet. Three dots alone would be the one column with no
		     word under it. -->
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
