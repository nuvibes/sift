<script lang="ts">
	/*
	 * Where the filter bar's panels open: the drawer under the bar on a wide window, and the sheet
	 * from the bottom edge on a phone. Each holds what it shows for the length of its close, so it is
	 * seen going, and lets go afterwards, because a hidden panel still asks the server for its counts
	 * on every query change.
	 */
	import { untrack, type Component, type Snippet } from 'svelte';
	import { Scroller, Select } from '$lib/components/common';
	import Drawer from '$lib/components/common/Drawer.svelte';
	import Field from '$lib/components/common/Field.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { sortIcon } from '$lib/grid/sort-state.svelte';
	import { FILTERS_PANEL, screenBar, type SortChoice } from './screen-bar.svelte';

	interface Props {
		/** The panel open in the drawer, or null. */
		showing: string | null;
		/** A screen's own panel, when that is what is open. */
		panel: { content: Component<Record<string, never>> } | null;
		/** The facets, drawn in the drawer or the sheet. */
		facets: Snippet;
		/** Whether this screen has facets to offer. */
		filterable: boolean;
		/** Whether the phone's sheet is up. */
		sheetOpen: boolean;
		orders: SortChoice[];
		order: string | undefined;
		onsort: (order: string) => void;
	}

	let { showing, panel, facets, filterable, sheetOpen, orders, order, onsort }: Props = $props();

	/* What the drawer draws, which lags what is open by the length of the close. */
	let lastShown = $state<string | null>(null);
	let clearing: ReturnType<typeof setTimeout> | null = null;

	$effect(() => {
		const now = showing;
		/* `untrack` around every read of what this writes, or setting `lastShown` re-runs it and undoes
		   the hold in the same tick. */
		untrack(() => {
			if (now !== null) {
				if (clearing !== null) {
					clearTimeout(clearing);
					clearing = null;
				}
				lastShown = now;
				return;
			}
			if (lastShown === null || clearing !== null) return;
			clearing = setTimeout(() => {
				clearing = null;
				lastShown = null;
			}, 220);
		});
	});

	/* The facets stay in the sheet while it slides away, then leave. */
	let sheetHeld = $state(false);
	let sheetLetGo: ReturnType<typeof setTimeout> | null = null;

	$effect(() => {
		const now = sheetOpen;
		untrack(() => {
			if (sheetLetGo !== null) {
				clearTimeout(sheetLetGo);
				sheetLetGo = null;
			}
			if (now) {
				sheetHeld = true;
				return;
			}
			if (!sheetHeld) return;
			sheetLetGo = setTimeout(() => {
				sheetLetGo = null;
				sheetHeld = false;
			}, 220);
		});
	});

	/** The height the slot is held at, while `screenBar.shapeHeld`; null lets it take its own. */
	let heldAt = $state<number | null>(null);

	/* Hold the slot at the tallest it has been since the hold began, from the layout height (the
	   drawer's own movement is a transform and must not be measured into it). */
	function keepsItsShape(slot: HTMLElement) {
		const grow = (height: number) => {
			if (!screenBar.shapeHeld) return;
			if (heldAt === null || height > heldAt) heldAt = height;
		};
		$effect(() => {
			if (!screenBar.shapeHeld) {
				heldAt = null;
				return;
			}
			untrack(() => grow(slot.offsetHeight));
		});
		const watch = new ResizeObserver((entries) => {
			for (const entry of entries) {
				grow(entry.borderBoxSize?.[0]?.blockSize ?? slot.offsetHeight);
			}
		});
		watch.observe(slot);
		return () => watch.disconnect();
	}
</script>

<!--
	Whatever is open, drawn once, over the screen rather than pushing it, so the wall stays where it
	is. Against the bar's row, so it travels with the bar. It opens as a height (`.drawer`) and is
	held in the DOM while shut, so it has a closed height to animate from; `visibility` takes it out
	of reach at the end of the close.
-->
<div
	class="drawer"
	class:down={showing !== null}
	aria-hidden={showing === null}
	onmouseenter={() => screenBar.stillHere()}
	onmouseleave={() => screenBar.leaving()}
>
	<div class="held">
		<!-- A tall panel scrolls through the shared region. Here rather than around the whole bar:
		     an `overflow: hidden` ancestor clips an absolutely positioned child. -->
		<Scroller>
			<!-- The panel lines up with this bar's inset; the inset is the host's (`BarPanel`). -->
			<div
				class="panel-slot"
				{@attach keepsItsShape}
				style:min-block-size={heldAt === null ? null : `${heldAt}px`}
			>
				{#if lastShown === FILTERS_PANEL}
					{@render facets()}
				{:else if panel}
					{@const Panel = panel.content}
					<Panel />
				{/if}
			</div>
		</Scroller>
	</div>
</div>

<!-- The phone's sheet from the bottom edge, over the page and the tab bar: the orders as one
     chooser, then the facets, each only where the screen offers it. -->
{#if phoneWidth.yes}
	<Drawer side="bottom" label="Filter and sort" open={sheetOpen} onclose={() => screenBar.close()}>
		<div class="phone-sheet">
			{#if orders.length > 0}
				<Field label="Sort by">
					{#snippet control({ id })}
						<Select
							{id}
							value={order}
							options={orders}
							label="Sort by"
							onValueChange={(next) => onsort(next)}
							onAction={(next) => onsort(next)}
						>
							{#snippet preview(option)}
								{@const mark = sortIcon(option.value)}
								{#if mark}<Icon name={mark} size={16} />{/if}
							{/snippet}
						</Select>
					{/snippet}
				</Field>
			{/if}
			{#if filterable && (sheetOpen || sheetHeld)}
				{@render facets()}
			{/if}
		</div>
	</Drawer>
{/if}

<style>
	.panel-slot {
		--bar-panel-inset: var(--space-6);
		/* Clear of the bar above it, so it reads as opening under the bar. */
		padding-block-start: var(--space-2);
	}

	/* The drawer: one row whose fraction animates, the property that moves to and from a content
	   height in every engine. */
	.drawer {
		/* Over the screen, anchored to the bar's row so it hangs from the bar wherever it is. */
		position: absolute;
		inset-inline: 0;
		inset-block-start: 100%;
		z-index: var(--z-popover, 40);
		display: grid;
		grid-template-rows: 0fr;
		transition: grid-template-rows var(--dur-base) var(--ease);
	}

	.drawer.down {
		grid-template-rows: 1fr;
	}

	/* `min-block-size: 0`, or the row is held open at its content's height. */
	.held {
		overflow: hidden;
		min-block-size: 0;
	}

	/* Out of reach while shut, not one frame sooner: `visibility` waits for the close to finish. */
	.drawer[aria-hidden='true'] .held {
		visibility: hidden;
	}

	.drawer.down .held {
		visibility: visible;
		transition: translate var(--dur-slow) var(--ease-spring);
	}

	/* The panel drops the last few pixels on the spring while the row opens round it; shutting
	   takes the plain ease back up. Its own property, so the reach rules keep theirs. */
	.held {
		translate: 0 calc(var(--space-6) * -1);
	}

	.drawer.down .held {
		translate: 0 0;
	}

	.drawer[aria-hidden='true'] .held {
		transition:
			visibility var(--dur-base) linear,
			translate var(--dur-base) var(--ease);
	}

	:global(:root[data-motion='reduce']) .held {
		translate: none;
	}

	/* The phone's sheet: the panel lines up with the sheet's inset (`--bar-panel-inset`). Not
	   `.sheet`, which is the global centred dialog. */
	.phone-sheet {
		--bar-panel-inset: 0px;
		display: grid;
		gap: var(--space-4);
	}
</style>
