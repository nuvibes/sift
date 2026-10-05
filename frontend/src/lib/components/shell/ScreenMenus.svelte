<script lang="ts">
	/* WHY NO HOVER: these rows ARE the shared button, dressed. Their motion is the button's own:
	   it transitions its background over --dur-instant for every tone, and a second transition
	   written here would be a copy of one rule in two files, which is the fault the button exists to
	   stop. What this file changes is which shade the step lands on, not whether there is a step. */
	/* NOT ON THE GALLERY: this reads what the screen underneath has published into the screen-bar
	   store and draws triggers for it. A second live copy would draw a second set of triggers for the
	   same panels, and pressing either would move the one piece of state both are reading: the app
	   drawn twice. Everything it is MADE of is on the gallery: the button, the chevron, the panel it
	   drops open. */

	/* WHY NOT BITS-UI: the library's NavigationMenu is the right shape for this and the wrong
	   mechanics. Its content is POSITIONED (a floating layer over the page) and the whole point of
	   this arrangement is that opening a menu pushes the screen down rather than covering it, so the
	   thing you were reading is still on the page while you filter it. Fighting a positioner into
	   normal flow leaves both opinions about where the panel goes in the codebase, and the screen-bar
	   store already owns which panel is open. So the trigger is a button and the opening is the
	   store's. */

	/*
	 * The named triggers for whatever screen is underneath: what filters it, what orders it, and the
	 * panels it publishes of its own.
	 *
	 * Glyphs, distinct by construction, each in a `Tooltip` that says why when dimmed; an icon-only
	 * `Button` requires an `aria-label`. Filter, then Sort, then the screen's own. No chevron: the
	 * open panel is `pressed`. Hover opens after `--hover-intent`; a pointed-open panel falls shut on
	 * leaving, a pressed one stays (`screen-bar`).
	 */
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

	/*
	 * WHERE AN OPEN LIST IS DRAWN: inside the filled box while a screen fills the window, since a
	 * fullscreen browser paints nothing else; null otherwise, the end of the document.
	 */
	const inTheFilledBox = $derived(stage.whatFillsTheWindow);

	/* Which side a label opens on: away from the window edge the row sits near (down on the top bar,
	   up along the foot of a filled window). */
	const labelSide = $derived<'top' | 'bottom'>(stage.filling ? 'top' : 'bottom');

	/* The lists hang from the top bar (`fromBar`) while the row is there, and rise from the foot of a
	   filled window. */
	const hangsFromTheBar = $derived(!stage.filling);

	/** The screen's own panels that answer "what is this screen showing". See `lead` on the panel. */
	/* A panel whose trigger is drawn on the top bar is not drawn here at all. See `atTheTop`. */
	const leading = $derived((tools.panels ?? []).filter((one) => one.lead && !one.atTheTop));

	/*
	 * Whether each of the two acts here is available; both are drawn either way (`Capability`).
	 */
	const filterable = $derived(ableTo(tools.filterable));
	/* The orders, or none: a screen that cannot order says why instead of listing any. See
	   `ScreenTools.sorts`. */
	const sorts = $derived(ordersOffered(tools.sorts));
	const sortable = $derived(sorts.length > 0);
	/* What the dimmed control says. The screen's own sentence where it gave one, exactly as the
	   Filter trigger beside it takes the screen's own. */
	const noOrders = $derived(typeof tools.sorts === 'string' ? tools.sorts : NOT_HERE.sort);

	/* What the screen says it is in, else its first order, as the screen itself falls back. */
	const order = $derived(tools.sort ?? sorts[0]?.value);

	/*
	 * Whether the order menu is showing, read from the ROW (`screenBar.open`): one open thing.
	 */
	const ordering = $derived(sortable && screenBar.open === SORT_MENU);

	/*
	 * The dwell before a menu opens: one timer for the row, so crossing three menus opens none.
	 */
	let pending: ReturnType<typeof setTimeout> | null = null;

	/*
	 * The dwell, read from `--hover-intent` on `:root`, with the token's value as the fallback where
	 * no stylesheet is loaded (jsdom).
	 */
	function dwell(): number {
		if (typeof getComputedStyle === 'undefined') return 180;
		const said = getComputedStyle(document.documentElement).getPropertyValue('--hover-intent');
		const ms = Number.parseFloat(said);
		return Number.isFinite(ms) && ms > 0 ? ms : 180;
	}

	/*
	 * Which trigger the pending open is for, so the many mousemoves over one button do not restart
	 * the dwell for ever.
	 */
	let pendingFor: string | null = null;

	/*
	 * Whether the row was put under the pointer by the LAYOUT (the rail collapsing or expanding):
	 * pointing opens nothing then, until the pointer leaves the row, a fact about a position.
	 */
	let parked = false;

	/** The row itself, so "the pointer has left it" is a question that can be asked, and so an
	    open list's sheet can leave the row pressable (see `spare` on `Select`). */
	let row: HTMLElement | null = $state(null);

	/*
	 * The guard watches the row moving, not the pointer arriving: an arrival test needs an unbroken
	 * stream of mousemoves, which the desktop window's drag region does not hand the page.
	 */
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

	/**
	 * The pointer moved somewhere; outside the row, the parking is over. On the window, since a row
	 * sliding away fires a leave as readily as a pointer walking off.
	 */
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

	/*
	 * Open after a dwell, scheduled by movement, never by arrival: collapsing the sidebar slides the
	 * row under a still pointer, and `mouseenter` would open whatever slid there; a `mousemove`
	 * happens only when the pointer moves (`parked` covers the move right after).
	 */
	function openAfterDwell(id: string) {
		// Already waiting on this one. Not restarted: a mousemove fires repeatedly while the pointer
		// crosses the button, and a dwell that restarts on each of them never elapses.
		if (pendingFor === id) return;
		// Already showing: nothing to wait for, but cancel a pending CLOSE (coming back means staying),
		// before the `parked` test, so a pointer on an open trigger keeps it whatever moved the row.
		if (screenBar.open === id) {
			cancel();
			screenBar.stillHere();
			return;
		}
		// Put here by a collapsing sidebar rather than pointed at. Nothing scheduled, and nothing
		// already scheduled is disturbed either.
		if (parked) return;
		cancel();
		pendingFor = id;
		pending = setTimeout(() => {
			pending = null;
			pendingFor = null;
			screenBar.showByHover(id);
		}, dwell());
	}

	/*
	 * A press is immediate and it toggles, dropping the dwell, or the running timer would close what
	 * the click opened.
	 */
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
	<!-- The tooltip is the name (WCAG 2.5.3: the accessible name holds the visible one), and the
	     sentence is only for when the control is off. -->
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

	<!--
		The order is a menu, a list you pick one of: `Select` in its icon form, dressed bare below,
		drawn dimmed where a screen offers none. It opens on the same dwell and holds focus on its
		trigger (`aria-activedescendant`), so a dwell never pulls the caret. `screenBar` holds which is
		open. The portalled list reports its own pointer to the store, which asks no veto of a menu.
		`Shuffle again` arrives at `onSort` like any order (`action`, `RESHUFFLE`).
	-->
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
				<!-- A glyph in front of each order (`sortIcon`), so the one wanted is found by shape. The
				     trigger keeps the general sort glyph; a key with no mark draws none. -->
				{#snippet preview(option)}
					{@const mark = sortIcon(option.value)}
					{#if mark}<Icon name={mark} size={16} />{/if}
				{/snippet}
			</Select>
		</span>
	</Tooltip>

	<!-- No saved-filters trigger: reaching for a kept filter is part of choosing one, so they are at
	     the foot of the Filter panel (`SavedFilters`). -->

	<!-- A screen's own MENUS (Theater's Layouts), drawn exactly as the order is; `isAMenu` reads this
	     published list, so a new one inherits every menu behaviour. -->
	<!-- THE SCREEN'S OWN MENUS COME FIRST, then its panels: on Theater, Layouts and then Saved
	     Layouts, the shape of the wall before the walls kept. -->
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

	<!-- Whatever panels the screen publishes; its label is both the tooltip and the accessible name. -->
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
	/*
	 * The same gap as every other group on this bar, and buttons at `--control-height`, so hover
	 * grounds match from end to end.
	 */
	.menus {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/*
	 * The order chooser, stripped to the ghost square the row is built of (`--control-height`, no
	 * border or ground, `--sift-ink-2`). `:global` on the primitive's trigger, anchored on `.menus`.
	 */
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

	/* The hover layer the two glyph buttons beside it take (the shared button's `ghost` tone,
	   see `--layer-hover`), over no ground, so the bar's own surface shows through the same way. */
	.menus :global(.ui-select.bare:hover:not(:disabled)) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
		color: var(--sift-ink);
	}

	/* Open reads exactly like pressed does on the two beside it (the accent ground and the accent
	   mark) so "this menu is the one showing" is one appearance on this row rather than two. */
	.menus :global(.ui-select.bare[data-state='open']) {
		border: 0;
		box-shadow: none;
		background: var(--sift-accent-bg);
		color: var(--sift-accent-text);
	}

	/* Dimmed, at the same amount the shared button uses when it is disabled: two controls sitting
	   against each other must not be dimmed by different amounts for one meaning. */
	.menus :global(.ui-select.bare:disabled) {
		opacity: 0.5;
	}

	/* The wrapper exists only to hear the pointer crossing this control. See the markup. It must
	   not become a box of its own between the tooltip and the trigger. */
	.order {
		display: inline-flex;
	}
</style>
