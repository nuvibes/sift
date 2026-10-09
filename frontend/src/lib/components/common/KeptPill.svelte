<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'KeptPill',
		category: 'primitive',
		role: 'a kept filter or layout drawn as a pill that previews what it holds',
		basis: 'own',
		states: ['default', 'applied', 'working']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no pill in the library, and there is nothing to build: this is the
	app's Pressable, RowMenu, ContextMenu and Tooltip, arranged; it owns the box. */

	/* Something kept under a name (a saved filter or wall) as one object: press to put it on,
	   hover to see what it holds, three dots and a right-click for the same rows. */
	import type { Snippet } from 'svelte';
	import type { IconName } from '$lib/design/icons';
	import Icon from '$lib/components/Icon.svelte';
	import ContextMenu from './ContextMenu.svelte';
	import RowMenu from './RowMenu.svelte';
	import Spinner from './Spinner.svelte';
	import Tooltip from './Tooltip.svelte';
	import VerbMenuItems from './VerbMenuItems.svelte';
	import Pressable from './Pressable.svelte';
	import type { Verb } from './verbs';
	import { stage } from '$lib/components/shell/stage.svelte';

	interface Props {
		id: string;
		name: string;
		/** Put it on. The press, and the only thing the pill itself does. */
		onapply: () => void;
		/** What this one holds, drawn in the bubble: a filter's chips or a wall's shape. */
		holds?: Snippet;
		/** What to say when there is nothing to draw. A bubble with nothing in it looks broken. */
		nothing?: string;
		/** Whether what `holds` draws is a picture rather than a row of chips. See `Tooltip.wide`. */
		wide?: boolean;
		/** Everything else that can be done to it: rename, update, delete. Drawn twice, listed once. */
		verbs?: Verb[];
		/** A glyph for the kind before the name: filters wear the funnel, walls nothing. */
		icon?: IconName;
		/** A write against it is in flight: the spinner in the glyph's place, no second press. */
		busy?: boolean;
	}

	let {
		id,
		name,
		onapply,
		holds,
		nothing = '',
		verbs = [],
		wide = false,
		icon,
		busy = false
	}: Props = $props();
</script>

<!-- The whole pill is the right-click target, with the three dots' rows. `portalTo` is what
draws either menu while the screen is filled. -->

<ContextMenu
	items={rightClick}
	label="More for {name}"
	triggerClass="kept-trigger"
	portalTo={stage.whatFillsTheWindow}
>
	<div class="kept">
		<!-- No heading in the bubble: the chips are what it keeps. -->
		<Tooltip label={holds ? '' : nothing} placement="top" {wide}>
			{#snippet detail()}
				{@render holds?.()}
			{/snippet}
			<!-- The glyph inside the press: one target, not two. -->
			<Pressable
				class="name"
				feedback="wash"
				radius="sm"
				disabled={busy}
				aria-busy={busy ? 'true' : undefined}
				onclick={onapply}
			>
				<!-- The spinner stands where the glyph stands, so the pill keeps its width. -->
				{#if busy}
					<Spinner size={16} label="Saving {name}" />
				{:else if icon}
					<Icon name={icon} size={16} />
				{/if}
				{name}
			</Pressable>
		</Tooltip>
		{#if verbs.length > 0}
			<!-- Not disabled while writing: the right-click must offer the same rows, and the caller
			refuses a second write. -->
			<RowMenu {verbs} ids={[id]} label="More for {name}" portalTo={stage.whatFillsTheWindow} />
		{/if}
	</div>
</ContextMenu>

{#snippet rightClick()}
	<VerbMenuItems {verbs} ids={[id]} subjectId={id} />
{/snippet}

<style>
	/* The column header's box, so a kept thing reads as picked up whole, like a column. */
	/* The context menu's wrapper, a plain block until told otherwise. */
	:global(.kept-trigger) {
		display: inline-flex;
	}

	.kept {
		display: inline-flex;
		align-items: center;
		gap: 2px;
		min-block-size: 36px;
		padding-inline: var(--space-1);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		background: var(--sift-surface-3);
		transition: border-color var(--dur-instant) var(--ease);
	}

	.kept:hover {
		border-color: var(--sift-line-strong);
	}

	/* Global: Pressable compiles the class. Padded to the text only. */

	.kept :global(.name) {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		padding: var(--space-1) var(--space-2);
		border-radius: var(--radius-sm);
		color: var(--sift-ink);
		font: var(--text-body-sm);
		white-space: nowrap;
	}

	/* The kind mark one step quieter than the name. */
	.kept :global(.name .icon) {
		color: var(--sift-ink-3);
	}

	/* The dots a size down, so the box matches the column headers' height. */
	.kept :global(button.more) {
		inline-size: 1.5rem;
		block-size: 1.5rem;
	}
</style>
