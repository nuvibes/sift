<script lang="ts">
	/* WHY NO HOVER: these rows ARE the shared button, dressed. Their motion is the button's own:
	   a transition written here would copy one rule into two files. */
	/* NOT ON THE GALLERY: this reads what the screen underneath has published into the screen-bar
	   store and draws triggers for it; a second live copy would draw the app twice. */

	/* WHY NOT BITS-UI: the library's NavigationMenu is the right shape for this and the wrong
	   mechanics: its content is positioned, and a menu here pushes the screen down. */

	// Filter, then Sort, then the screen's own, as glyphs; a pointed-open panel shuts on leaving.
	import { Button, Select } from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import { sortIcon } from '$lib/grid/sort-state.svelte';
	import { rail } from './rail-state.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import {
		FILTERS_PANEL,
		NOT_HERE,
		SORT_MENU,
		ableTo,
		ordersOffered,
		screenBar,
		whyNot
	} from './screen-bar.svelte';
	import { stage } from './stage.svelte';

	const tools = $derived(screenBar.tools);

	// Inside the filled box while a screen fills the window; null is the end of the document.
	const inTheFilledBox = $derived(stage.whatFillsTheWindow);

	/* A label opens away from the edge the row sits near. */
	const labelSide = $derived<'top' | 'bottom'>(stage.filling ? 'top' : 'bottom');

	/* Lists hang from the top bar while the row is there, rising from the foot when filled. */
	const hangsFromTheBar = $derived(!stage.filling);

	/** The screen's own leading panels, except those whose trigger is on the top bar. */
	const leading = $derived((tools.panels ?? []).filter((one) => one.lead && !one.atTheTop));

	const filterable = $derived(ableTo(tools.filterable));
	/* A screen that cannot order says why instead of listing any. */
	const sorts = $derived(ordersOffered(tools.sorts));
	const sortable = $derived(sorts.length > 0);
	/* The screen's own sentence where it gave one, as Filter takes it. */
	const noOrders = $derived(typeof tools.sorts === 'string' ? tools.sorts : NOT_HERE.sort);

	/* What the screen says it is in, else its first order, as the screen itself falls back. */
	const order = $derived(tools.sort ?? sorts[0]?.value);

	/* Read from the row: one open thing. */
	const ordering = $derived(sortable && screenBar.open === SORT_MENU);

	/* One timer for the row, so crossing three menus opens none. */
	let pending: ReturnType<typeof setTimeout> | null = null;

	/* `--hover-intent`, with its value as the fallback where no stylesheet loads (jsdom). */
	function dwell(): number {
		if (typeof getComputedStyle === 'undefined') return 180;
		const said = getComputedStyle(document.documentElement).getPropertyValue('--hover-intent');
		const ms = Number.parseFloat(said);
		return Number.isFinite(ms) && ms > 0 ? ms : 180;
	}

	/* So the many mousemoves over one button do not restart the dwell. */
	let pendingFor: string | null = null;

	/* The layout moved the row under the pointer; pointing opens nothing until it leaves. */
	let parked = false;

	/** For "has the pointer left it", and so an open list leaves the row pressable. */
	let row: HTMLElement | null = $state(null);

	/* Watches the row move; the desktop drag region hides the mousemoves an arrival test needs. */
	$effect(() => {
		void rail.collapsed;
		if (!started) {
			started = true;
			return;
		}
		parked = true;
	});

	/** The effect above runs once on mount, and a mount is not a collapse. */
	let started = false;

	/** Outside the row, the parking is over; on the window, as a sliding row fires leaves too. */
	function trackPointer(event: MouseEvent) {
		if (!parked || row === null) return;
		const box = row.getBoundingClientRect();
		const outside =
			event.clientX < box.left ||
			event.clientX > box.right ||
			event.clientY < box.top ||
			event.clientY > box.bottom;
		if (outside) parked = false;
	}

	function cancel() {
		if (pending === null) return;
		clearTimeout(pending);
		pending = null;
		pendingFor = null;
	}

	/* Opened by movement, never arrival: a collapsing sidebar slides the row under the pointer. */
	function openAfterDwell(id: string) {
		// Not restarted, or a dwell reset by every mousemove never elapses.
		if (pendingFor === id) return;
		// Already showing: coming back cancels a pending close, whatever moved the row.
		if (screenBar.open === id) {
			cancel();
			screenBar.stillHere();
			return;
		}
		// Put here by a collapsing sidebar, not pointed at.
		if (parked) return;
		cancel();
		pendingFor = id;
		pending = setTimeout(() => {
			pending = null;
			pendingFor = null;
			screenBar.showByHover(id);
		}, dwell());
	}

	/* A press is immediate and toggles; the dwell is dropped or it would close what it opened. */
	function press(id: string) {
		cancel();
		screenBar.toggle(id);
	}

	function onkeydown(event: KeyboardEvent) {
		if (event.key !== 'Escape' || screenBar.open === null) return;
		cancel();
		screenBar.close();
		/*
		 * Taken, and said so: the screen below hears the same key, and asks before acting on a used one.
		 */
		event.preventDefault();
	}
</script>

<svelte:window {onkeydown} onmousemovecapture={trackPointer} />

<!-- `onmouseleave` on the group, not each trigger: moving along the row is not leaving it. -->
<div
	class="menus"
	bind:this={row}
	onmouseenter={() => screenBar.stillHere()}
	onmouseleave={() => {
		cancel();
		screenBar.leaving();
	}}
	role="group"
	aria-label="What is on screen"
>
	<!-- The tooltip is the name (WCAG 2.5.3); the sentence only when off. -->
	<Tooltip
		label={filterable ? 'Filter' : whyNot(tools.filterable, NOT_HERE.filter)}
		placement={labelSide}
	>
		<Button
			tone="ghost"
			icon="filter_alt"
			aria-label="Filter"
			disabled={!filterable}
			pressed={filterable && screenBar.open === FILTERS_PANEL}
			aria-expanded={filterable ? screenBar.open === FILTERS_PANEL : undefined}
			onmousemove={() => filterable && openAfterDwell(FILTERS_PANEL)}
			onfocus={() => cancel()}
			onclick={() => press(FILTERS_PANEL)}
		/>
	</Tooltip>

	<!-- The order as `Select` in its icon form; it keeps focus on its trigger during the dwell. -->
	<Tooltip label={sortable ? 'Sort by' : noOrders} placement={labelSide}>
		<span
			class="order"
			onmousemove={() => sortable && openAfterDwell(SORT_MENU)}
			onmouseleave={() => cancel()}
			onpointerdown={() => cancel()}
			role="none"
		>
			<Select
				value={order}
				options={sorts}
				label="Sort by"
				spare={row}
				icon="sort"
				class="bare"
				disabled={!sortable}
				open={ordering}
				portalTo={inTheFilledBox}
				fromTheBar={hangsFromTheBar}
				onValueChange={(next) => tools.onSort?.(next)}
				onAction={(next) => tools.onSort?.(next)}
				onListPointer={(inside) => (inside ? screenBar.stillHere() : screenBar.leaving())}
				onOpenChange={(open) => {
					/* The chooser closing itself is the row being told, only while the menu is still the
					   open one, so another trigger's opening is not shut. */
					if (!open && screenBar.open === SORT_MENU) screenBar.close();
					if (open && screenBar.open !== SORT_MENU) screenBar.show(SORT_MENU);
				}}
			>
				<!-- A glyph per order (`sortIcon`), so the one wanted is found by shape. -->
				{#snippet preview(option)}
					{@const mark = sortIcon(option.value)}
					{#if mark}<Icon name={mark} size={16} />{/if}
				{/snippet}
			</Select>
		</span>
	</Tooltip>

	<!-- The screen's own menus, drawn as the order is (`isAMenu` reads this list), then panels. -->
	{#each tools.menus ?? [] as menu (menu.id)}
		<Tooltip label={menu.label} placement={labelSide}>
			<span
				class="order"
				onmousemove={() => openAfterDwell(menu.id)}
				onmouseleave={() => cancel()}
				onpointerdown={() => cancel()}
				role="none"
			>
				<Select
					value={menu.value}
					options={menu.options}
					label={menu.label}
					spare={row}
					icon={menu.icon}
					class="bare"
					preview={menu.preview}
					open={screenBar.open === menu.id}
					portalTo={inTheFilledBox}
					fromTheBar={hangsFromTheBar}
					onValueChange={(next) => menu.onChoose(next)}
					onListPointer={(inside) => (inside ? screenBar.stillHere() : screenBar.leaving())}
					onOpenChange={(open) => {
						if (!open && screenBar.open === menu.id) screenBar.close();
						if (open && screenBar.open !== menu.id) screenBar.show(menu.id);
					}}
				/>
			</span>
		</Tooltip>
	{/each}

	<!-- The screen's panels; a label is both the tooltip and the accessible name. -->
	{#each leading as one (one.id)}
		<Tooltip label={one.label} placement={labelSide}>
			<Button
				tone="ghost"
				icon={one.icon}
				aria-label={one.label}
				pressed={screenBar.open === one.id}
				aria-expanded={screenBar.open === one.id}
				onmousemove={() => openAfterDwell(one.id)}
				onfocus={() => cancel()}
				onclick={() => press(one.id)}
			/>
		</Tooltip>
	{/each}
</div>

<style>
	/* The bar's group gap, so hover grounds match end to end. */
	.menus {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* The order chooser stripped to the row's ghost square; `:global` anchored on `.menus`. */
	.menus :global(.ui-select.bare) {
		inline-size: var(--control-height);
		block-size: var(--control-height);
		min-block-size: var(--control-height);
		justify-content: center;
		padding: 0;
		border: 0;
		border-radius: var(--radius-md);
		background: none;
		color: var(--sift-ink-2);
	}

	/* The ghost tone's hover layer, over no ground. */
	.menus :global(.ui-select.bare:hover:not(:disabled)) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
		color: var(--sift-ink);
	}

	/* Open reads as pressed does on the buttons beside it. */
	.menus :global(.ui-select.bare[data-state='open']) {
		border: 0;
		box-shadow: none;
		background: var(--sift-accent-bg);
		color: var(--sift-accent-text);
	}

	/* The shared button's disabled dimming, so one meaning is one amount. */
	.menus :global(.ui-select.bare:disabled) {
		opacity: 0.5;
	}

	/* Only hears the pointer; it must not become a box between tooltip and trigger. */
	.order {
		display: inline-flex;
	}
</style>
