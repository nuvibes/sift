<script lang="ts">
	/* NOT ON THE GALLERY: a singleton that owns the window's search state; its parts are drawn there. */

	import { Button, Slider } from '$lib/components/common';
	/*
	 * The bar across the top: what acts on the library or the application, and the screen's own
	 * menus. The row below says what is in force (its chips). A control that cannot act here is drawn
	 * disabled rather than removed, so the row keeps its shape between screens.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { openSettings } from '$lib/settings-ui/settings-view';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { vault, vaultPrompt } from '$lib/shell/vault.svelte';
	import { rail } from './rail-state.svelte';
	import {
		FIELD_FLOOR,
		NOT_HERE,
		PHONE_SHEET,
		ableTo,
		ordersOffered,
		screenBar,
		whyNot
	} from './screen-bar.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { searchBox } from '$lib/search/search.svelte';
	import { stage } from './stage.svelte';
	import { gridAutoplay, gridSize, previewWords } from '$lib/grid/grid.svelte';
	import { VISIBLE_POOL_CAP } from '$lib/grid/viewport.svelte';
	import { SIZE_STEPS } from '$lib/grid/justify';
	import AddButton from './AddButton.svelte';
	import Breadcrumbs from '$lib/components/common/Breadcrumbs.svelte';
	import { pageTrail } from './trail.svelte';
	import ScreenMenus from './ScreenMenus.svelte';
	import SearchBox from './SearchBox.svelte';
	import TileSizePanel, { nearestStep } from './TileSizePanel.svelte';

	/** Whether the screen under this bar has tiles the slider would change. */
	const sizable = $derived(ableTo(screenBar.tools.resizable));

	/* Whether the tiles can play, asked apart from resizing: a wall of cards resizes and plays nothing. */
	const playable = $derived(ableTo(screenBar.tools.playable));

	/* What the play control is called (what pressing it will do) and whether it is lit, from the screen,
	   with the grid's words where the screen said nothing. */
	const playingNow = $derived(screenBar.tools.playing ?? gridAutoplay.mode === 'visible');
	/* While everything visible previews, how many are moving now, the ceiling said only once it binds. */
	const gridWords = $derived(
		previewWords(gridAutoplay.mode, gridAutoplay.playing, VISIBLE_POOL_CAP)
	);
	const previewLabel = $derived(screenBar.tools.playLabel ?? gridWords.label);
	const play = $derived(screenBar.tools.onPlay ?? (() => gridAutoplay.toggle()));
	const previewHint = $derived(
		screenBar.tools.playLabel === undefined ? gridWords.hint : previewLabel
	);

	screenBar.sizeHome = {
		id: 'tile-size',
		icon: 'grid_view',
		label: 'Tile size',
		content: TileSizePanel
	};

	/* Measured by `screenBar.watchRoom`: the rail and the caption buttons take room `min-width` cannot see. */
	let bar = $state<HTMLElement | null>(null);

	/* The phone's one control for Filter and Sort acts where either half does (`PHONE_SHEET`). */
	const narrowable = $derived(
		ableTo(screenBar.tools.filterable) || ordersOffered(screenBar.tools.sorts).length > 0
	);

	/* Watched again across a phone's width, where the field it reads comes and goes. */
	$effect(() => {
		void phoneWidth.yes;
		return bar ? screenBar.watchRoom(bar) : undefined;
	});

	function toggleVault() {
		if (vault.unlocked) {
			void vault.lock();
			return;
		}
		// On `vaultPrompt`, since a toast must be able to ask for the PIN too.
		vaultPrompt.ask();
	}
</script>

<header
	class="topbar"
	bind:this={bar}
	style:--bar-end="{screenBar.barEnd}px"
	style:--field-floor="{FIELD_FLOOR}px"
>
	<!-- The rail's collapse, on this side of the divide so it stays put at both rail widths. -->
	<div class="lead">
		<span class="collapse">
			<Tooltip
				label={rail.collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
				placement="bottom"
			>
				<!-- `icon=`, the icon-only shape: a glyph handed in as children draws a 52px box. -->
				<Button
					tone="ghost"
					icon={rail.collapsed ? 'left_panel_open' : 'left_panel_close'}
					aria-label={rail.collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
					aria-expanded={!rail.collapsed}
					aria-controls="main-rail"
					onclick={() => rail.toggle()}
				/>
			</Tooltip>
		</span>

		<!-- The screen's trail, folded to fit and to the press alone before the tile size leaves. On a
		     phone the frame draws it as the page's first line instead. -->
		{#if !phoneWidth.yes}
			<div class="trail">
				{#if pageTrail.crumbs.length > 1}
					<Breadcrumbs crumbs={pageTrail.crumbs} fit pressAlone={!screenBar.sizeOnBar} />
				{/if}
			</div>
		{:else}
			<!-- A phone's bar is a row of squares: this one opens the search the shortcut opens. -->
			<Tooltip label="Search" placement="bottom">
				<Button
					tone="ghost"
					icon="search"
					aria-label="Search"
					aria-haspopup="dialog"
					onclick={() => searchBox.askForSheet()}
				/>
			</Tooltip>
			<!-- Filter and Sort as one control, opening a sheet with both (`PHONE_SHEET`). -->
			<Tooltip
				label={narrowable ? 'Filter and sort' : whyNot(screenBar.tools.filterable, NOT_HERE.filter)}
				placement="bottom"
			>
				<Button
					tone="ghost"
					icon="filter_alt"
					aria-label="Filter and sort"
					aria-haspopup="dialog"
					disabled={!narrowable}
					pressed={narrowable && screenBar.open === PHONE_SHEET}
					aria-expanded={narrowable ? screenBar.open === PHONE_SHEET : undefined}
					onclick={() => screenBar.toggle(PHONE_SHEET)}
				/>
			</Tooltip>
		{/if}
	</div>

	<!-- One centre group, the screen's menus and the search box, whose midpoint is the bar's. The
	     menus are drawn in one home only: here, or on the screen's own row (`roomOnTopBar`). -->
	{#if !phoneWidth.yes}
		<div class="centre">
			{#if screenBar.roomOnTopBar && !stage.filling}
				<ScreenMenus />
			{/if}
			<SearchBox />
		</div>
	{/if}

	<div class="actions">
		<div class="near">
			<!-- Whether tiles preview as you point at them; dimmed where there is nothing to preview. -->
			<span class="preview">
				<Tooltip
					label={playable ? previewHint : whyNot(screenBar.tools.playable, NOT_HERE.play)}
					shortcut={playable ? screenBar.tools.playShortcut : undefined}
					placement="bottom"
				>
					<!-- The glyph follows the state: pressed offers to stop. -->
					<Button
						tone="ghost"
						icon={playable && playingNow ? 'autostop' : 'autoplay'}
						aria-label={previewLabel}
						disabled={!playable}
						pressed={playable && playingNow}
						onclick={play}
					/>
				</Tooltip>
			</span>

			<!-- Hidden holds one place on every screen, after play. Showing costs the PIN every time; hiding
			     never fails, since it is also the panic button. Nothing is drawn until the server answers. -->
			{#if vault.loaded}
				<Tooltip
					label={vault.unlocked
						? 'Hide hidden items'
						: vault.pinSet
							? 'Show hidden items (PIN)'
							: 'Set a PIN to hide items'}
					placement="bottom"
				>
					<Button
						tone="ghost"
						icon={vault.unlocked ? 'visibility' : 'visibility_off'}
						aria-label={vault.unlocked ? 'Hide hidden items' : 'Show hidden items'}
						aria-pressed={vault.unlocked}
						onclick={toggleVault}
						class={vault.unlocked ? 'open' : ''}
					/>
				</Tooltip>
			{:else}
				<!-- Its place, held so the row does not change width when the answer arrives. -->
				<span class="reserved" aria-hidden="true"></span>
			{/if}

			<!-- What a screen puts up about the whole window (`topExtra`, `atTheTop`). -->
			{@render screenBar.tools.topExtra?.()}

			{#each (screenBar.tools.panels ?? []).filter((one) => one.atTheTop) as one (one.id)}
				<Tooltip label={one.label} placement="bottom">
					<Button
						tone="ghost"
						icon={one.icon}
						aria-label={one.label}
						pressed={screenBar.open === one.id}
						aria-expanded={screenBar.open === one.id}
						onclick={() => screenBar.toggle(one.id)}
					/>
				</Tooltip>
			{/each}
		</div>

		<div class="trailing">
			<!-- Tile size, dimmed rather than hidden where there is nothing to resize. -->
			<Tooltip
				label={sizable ? 'Tile size' : whyNot(screenBar.tools.resizable, NOT_HERE.resize)}
				placement="bottom"
			>
				<label class="size" class:off={!sizable} class:gone={!screenBar.sizeOnBar}>
					<Icon name="grid_view" size={18} label="Tile size" />
					<Slider
						class="steps"
						label="Tile size"
						max={SIZE_STEPS.length - 1}
						disabled={!sizable}
						value={nearestStep(gridSize.step)}
						valueText="{nearestStep(gridSize.step) + 1} of {SIZE_STEPS.length}"
						oninput={(step) => gridSize.set(SIZE_STEPS[step])}
					/>
				</label>
			</Tooltip>

			<!-- Admin only: every route behind Add is refused a guest. -->
			{#if session.isAdmin}
				<AddButton />
			{/if}
		</div>
	</div>
</header>

<style>
	/*
	 * Three columns, the middle one the centre group, capped so each equal end holds the end group
	 * (`--bar-end`): its midpoint is the bar's. Where the field's floor still binds, the start end
	 * gives way rather than the controls overlapping.
	 */
	.topbar {
		grid-area: topbar;
		/* Padded by the top edge's safe area, painting its ground under a notch (`--safe-top`). */
		height: calc(var(--topbar-height) + var(--safe-top));
		padding-block-start: var(--safe-top);
		display: grid;
		grid-template-columns: minmax(max-content, 1fr) auto minmax(max-content, 1fr);
		align-items: center;
		gap: var(--space-3);
		padding-inline: var(--space-4);
		/* Puts the collapse's centre on the line of the glyph beside every page title. */
		padding-inline-start: var(--space-4);
		/* Askable by its width, for the centre group's cap. */
		container-type: inline-size;
		background: var(--sift-surface-1);
		border-bottom: 1px solid var(--border);
	}

	/* The collapse at the far left, then the trail in what is left. */
	.lead {
		display: flex;
		align-items: center;
		justify-content: flex-start;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* No floor: the trail folds into whatever room this end has. Contained, so its words never size
	   the track and push the centre aside. */
	.trail {
		flex: 1 1 0;
		min-inline-size: 0;
		contain: inline-size;
		display: flex;
		align-items: center;
	}

	.trail > :global(.crumbs) {
		flex: 1;
	}

	.centre {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
		max-inline-size: max(var(--field-floor), 100cqi - 2 * (var(--bar-end) + var(--space-3)));
	}

	.centre > :global(.menus) {
		flex: none;
	}

	/* A definite width, so the group's own width is the menus and the field's ceiling; the field is
	   the part that gives way. */
	.topbar .centre > :global(.wrap) {
		inline-size: var(--search-width);
		flex: 0 1 auto;
		min-inline-size: 0;
	}

	/* Packed against the centre group, with the tile size and Add at the far end. */
	.actions {
		display: flex;
		align-items: center;
		justify-content: flex-start;
		gap: var(--space-2);
	}

	/* Never shrunk: their widths are the end group's, which the centre group is capped by. */
	.near {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		flex: none;
	}

	/* Holds the vault control's place until it is drawn, so the row keeps its width. */
	.reserved {
		inline-size: var(--control-height);
		block-size: var(--control-height);
	}

	/* A wider gap, so the slider's track does not run into Add's filled pill. */
	.trailing {
		display: flex;
		align-items: center;
		gap: var(--space-4);
		flex: none;
		margin-inline-start: auto;
	}

	.size {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		color: var(--sift-ink-3);
	}

	/* DRESSED BY: .steps (`Slider` draws the track, the fill and the thumb; this says how wide). */
	.size :global(.steps) {
		inline-size: 96px;
	}

	/* The shared button's disabled 0.5, so the controls beside it dim alike. */
	.size.off {
		opacity: 0.5;
	}

	/* With its tooltip's box, or the row keeps the gap beside it. */
	.trailing > :global(.wrap:has(.size.gone)) {
		display: none;
	}

	@media (max-width: 767px) {
		.size {
			display: none;
		}
	}

	/* Boxes that lay nothing out; they exist to be left off the phone's bar. */
	.collapse,
	.preview {
		display: contents;
	}

	/* The phone's bar: one line of squares, the screen's at the start and the window's at the end. */
	@media (max-width: 767px) {
		.topbar {
			grid-template-columns: max-content minmax(0, 1fr);
			gap: var(--space-2);
			/* Every square a thumb's target. */
			--control-height: var(--touch-target);
		}

		.actions {
			justify-content: flex-end;
		}

		/* No tile size here, so Add stays beside Hidden. */
		.trailing {
			margin-inline-start: 0;
		}

		.collapse,
		.preview {
			display: none;
		}
	}
</style>
