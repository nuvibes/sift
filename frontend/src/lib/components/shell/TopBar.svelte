<script lang="ts">
	/* NOT ON THE GALLERY: the top bar is a singleton the layout already renders, and it owns the search
	   state for the whole window. A second one would hold a second copy of that state and the two
	   would disagree. Everything it is MADE of (the search box, the filter bar, the saved-search
	   popover) is drawn on the gallery on its own. */

	import { Button, Slider } from '$lib/components/common';
	/*
	 * The bar across the top: what spans every screen, and what acts on whatever screen is under
	 * it.
	 *
	 * The split between this bar and the row below is by what a control acts on, a line somebody
	 * can learn in one go:
	 *
	 * - this row acts on the library, or on the application: search, the vault, adding, and how big
	 *   the tiles are wherever there are tiles, plus the screen's own menus (what it is filtered to,
	 *   and what order it is in), which are the same three jobs on every screen and so have one home;
	 * - the row below describes what is in force on the screen: its chips.
	 *
	 * The alternative, each screen carrying its own filter bar, order dropdown and sharing filter,
	 * is the same jobs written once per screen, each free to drift, with no two screens agreeing on
	 * where a person should look.
	 *
	 * A control that would do nothing on the screen being looked at is drawn disabled rather than
	 * removed, so the row does not change shape as somebody moves about.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { openSettings } from '$lib/settings-ui/settings-view';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { vault, vaultPrompt } from '$lib/shell/vault.svelte';
	import { rail } from './rail-state.svelte';
	import {
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
	import { SIZE_STEPS, type SizeStep } from '$lib/grid/justify';
	import AddButton from './AddButton.svelte';
	import Breadcrumbs from '$lib/components/common/Breadcrumbs.svelte';
	import { pageTrail } from './trail.svelte';
	import ScreenMenus from './ScreenMenus.svelte';
	import SearchBox from './SearchBox.svelte';

	/** Whether the screen under this bar has tiles the slider would change. */
	const sizable = $derived(ableTo(screenBar.tools.resizable));

	/*
	 * Whether the tiles here can PLAY, which is not the same question as whether they can be resized.
	 *
	 * A wall of cards (people, sites, tags, collections) resizes and has nothing to play, so the
	 * two are asked separately and answered separately. Both controls are drawn on every screen
	 * either way; see `Capability`.
	 */
	const playable = $derived(ableTo(screenBar.tools.playable));

	/**
	 * What the preview toggle is called, which is what pressing it will do: the state it will put
	 * the tiles into, never the one they are in. Named for the state they are in, the tooltip over
	 * the lit control would name what was already happening and read backwards to every press.
	 *
	 * What the play control is called, and whether it is lit.
	 *
	 * Both come from the screen. A control drawn on every screen must be operable by every screen,
	 * so each publishes its own answer (Theater, four videos playing at once, has its own) rather
	 * than this bar reaching for the grid's autoplay store. A screen that says it is playable and
	 * does not say what pressing does gets the grid's words.
	 */
	const playingNow = $derived(screenBar.tools.playing ?? gridAutoplay.mode === 'visible');
	/*
	 * The grid's words, and while everything visible is previewing, how many are moving now: read
	 * off the walls as they scroll (`gridAutoplay.playing`), with the ceiling said only once it is
	 * what holds the rest still. Each tile playing is a video being decoded, so a real ceiling
	 * holds (see `VISIBLE_POOL_CAP`); saying the ceiling as if it were the count would read as 48
	 * playing on a screen of a dozen.
	 */
	const gridWords = $derived(
		previewWords(gridAutoplay.mode, gridAutoplay.playing, VISIBLE_POOL_CAP)
	);
	const previewLabel = $derived(screenBar.tools.playLabel ?? gridWords.label);
	const play = $derived(screenBar.tools.onPlay ?? (() => gridAutoplay.toggle()));
	const previewHint = $derived(
		screenBar.tools.playLabel === undefined ? gridWords.hint : previewLabel
	);

	/*
	 * Which notch the slider sits on.
	 *
	 * Nothing chosen means the grid is sizing itself to the window, and the honest position for that
	 * is the notch nearest what it settled on, but the bar cannot measure a grid it does not own.
	 * So an unset slider shows the middle: it is where the control starts from rather than a claim
	 * about the tiles, and the first nudge in either direction takes hold immediately.
	 */
	function nearestStep(step: SizeStep | null): number {
		if (step === null) return Math.floor(SIZE_STEPS.length / 2);
		const at = SIZE_STEPS.indexOf(step);
		return at === -1 ? Math.floor(SIZE_STEPS.length / 2) : at;
	}

	/*
	 * Opening asks; shutting does not.
	 *
	 * Neither end redraws anything from here. Changing the vault bumps a counter the shell watches,
	 * and the shell empties every cache the vault scopes, which is what makes the next render
	 * honest wherever somebody happens to be standing. Doing it here would fix only the screen this
	 * button is on, and the button is on all of them.
	 *
	 * The bar's own element, so the bar can measure the room it has.
	 *
	 * Whether the screen menus fit up here is a question about the bar, not the window: the rail
	 * takes 208px of this row when it is open and the caption buttons take about 138 more in the
	 * desktop window, both invisible to `min-width`, and together enough to squeeze the search
	 * field to 176px at half-screen. The measurement is on `screenBar.watchRoom`, along with the
	 * arithmetic.
	 */
	let bar = $state<HTMLElement | null>(null);

	/*
	 * THE PHONE'S ONE CONTROL FOR FILTER AND SORT, and whether it can act.
	 *
	 * It acts where either half does: a screen that orders and does not filter still has a sheet
	 * worth opening, with the orders in it. Where neither does, it is dimmed with the screen's own
	 * reason for the filter, the same sentence the desktop's Filter carries there. See `PHONE_SHEET`.
	 */
	const narrowable = $derived(
		ableTo(screenBar.tools.filterable) || ordersOffered(screenBar.tools.sorts).length > 0
	);

	/* Watched again when the width crosses a phone's, because the search field it reads is not on
	   this bar at a phone's width and comes back when the window widens. */
	$effect(() => {
		void phoneWidth.yes;
		return bar ? screenBar.watchRoom(bar) : undefined;
	});

	function toggleVault() {
		if (vault.unlocked) {
			void vault.lock();
			return;
		}
		// Both halves of the asking (the prompt, and sending somebody with no PIN off to set one)
		// live on `vaultPrompt`, because a toast has to be able to ask for the PIN too and a
		// toast action cannot mount a dialog. See `vault.svelte.ts`.
		vaultPrompt.ask();
	}
</script>

<header class="topbar" bind:this={bar}>
	<!--
		The rail's width, from the content side of the divide.

		Here rather than in the rail itself, and that is the whole reason it works: a control that
		lives inside the thing it collapses has to shrink with it, and at 64px there is nowhere for
		it to go that is not on top of a nav icon. On this side it stays exactly where it is at both
		widths, which is what makes it findable again once the rail is narrow.

		`aria-expanded` on the rail's own name rather than on nothing: what this button expands is
		the navigation, and a toggle that announces only itself says nothing about what it did.
	-->
	<div class="lead">
		<span class="collapse">
			<Tooltip
				label={rail.collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
				placement="bottom"
			>
				<!--
				`icon=` rather than an `<Icon>` handed in as children, and the difference is visible.

				A glyph passed as CHILDREN is a label as far as the button is concerned, so the button
				is not icon-only: it keeps the padded word-shaped box and comes out 52px wide beside a
				36px square, so one row would hold different hover-ground widths. `icon=` is the
				icon-only shape, and the type makes `aria-label` compulsory with it, which is where the
				name lives.
			-->
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

		<!--
			THE SCREEN'S TRAIL, after the rail's collapse, on the search box's line.

			Said by the screen's frame (`pageTrail`), so a screen with a trail starts where every
			screen starts rather than a band lower. It folds to fit the room it is given: its middle
			behind one press first, then everything but where you are.

			The room is held on EVERY screen, with or without a trail: the bar has one shape (a trail
			arriving must not slide the menus and the search box sideways), so a screen without one
			leaves the room empty. Not on a phone, whose bar is a row of squares: the frame draws the
			trail as the page's first line there.
		-->
		{#if !phoneWidth.yes}
			<div
				class="trail"
				style:--trail-gives="{screenBar.trailGives}px"
				class:bare={screenBar.trailBare}
			>
				{#if pageTrail.crumbs.length > 1}
					<Breadcrumbs crumbs={pageTrail.crumbs} fit />
				{/if}
			</div>
		{/if}

		<!--
			What filters and orders whatever screen is under this bar, up here rather than on a row
			of its own.

			The controls belong out of the screens, but a second full-width bar of them would be two
			rows that both look global, one directly under the other, with a split that has to be
			explained rather than seen. This bar is already the one that is always there. The row
			below keeps what it is for: the chips, a description of what is in force rather than a
			control.

			Not drawn while the window is filled, because this whole bar is not, and not drawn on a
			narrow window either: below 880px the three plus the actions leave the search field
			unusably small. Both times the filter bar draws them instead, on a row with the width to
			spare. See its own file, and `roomOnTopBar`.

			The filling half is a condition here, not a description. Filling the window draws the
			fullscreen element and nothing else, so this bar is invisible while a copy inside it
			would still be in the document, and a copy nobody can see is still a second Layout
			button in the accessibility tree and the tab order, the fault `roomOnTopBar` exists to
			prevent. The two homes are mutually exclusive, always.
		-->
		{#if phoneWidth.yes}
			<!--
				THE PHONE'S BAR IS A ROW OF SQUARES, and the search is the first of them.

				The field needs a line of its own to be typed into, and a phone's bar has none to give:
				squeezed beside the squares it would come out about 72px wide with the words cut off at
				three letters. So the square opens the search the keyboard shortcut opens, the same box given
				the whole width (see `SearchOverlay`). Drawn in place of the field rather than beside a
				hidden one: a field hidden by a stylesheet is still a second search box in the tab order.
			-->
			<Tooltip label="Search" placement="bottom">
				<Button
					tone="ghost"
					icon="search"
					aria-label="Search"
					aria-haspopup="dialog"
					onclick={() => searchBox.askForSheet()}
				/>
			</Tooltip>
			<!--
				Filter and Sort as ONE control, opening a sheet from the bottom edge with both in it.
				See `PHONE_SHEET`. The filter's mark, since at this width there is no separate Filter
				for it to be confused with.
			-->
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
		{:else if screenBar.roomOnTopBar && !stage.filling}
			<ScreenMenus />
		{/if}
	</div>

	{#if !phoneWidth.yes}
		<SearchBox />
	{/if}

	<div class="actions">
		<!--
			Whether tiles preview as you point at them.

			It controls how the tiles behave, as the size slider does; the slider sits beside Add,
			where somebody looks for it, so the two are not a pair on the bar. Drawn on every screen
			and dimmed where there is nothing to preview, as the slider is, so the row keeps its
			shape on a wall of cards.
		-->
		<span class="preview">
			<Tooltip
				label={playable ? previewHint : whyNot(screenBar.tools.playable, NOT_HERE.play)}
				shortcut={playable ? screenBar.tools.playShortcut : undefined}
				placement="bottom"
			>
				<!-- Named through `aria-label`: a glyph with only a tooltip is an unlabelled button to
			     a screen reader, and the shared button refuses to be icon-only without one. -->
				<!-- The glyph follows the STATE, not the action: pressed means everything visible is
			     already previewing, so the shape offers to stop rather than repeating the one it
			     was in before it was pressed. -->
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

		<!--
			The vault control, straight after the play control and before whatever the screen puts up
			here, so it holds one place on every screen: on Theater the row reads Play everything,
			Hidden, Sound.

			Showing hidden things costs the PIN every single time, including when
			the vault is already open: the asking is what makes it a decision rather than a state
			somebody drifted into. Hiding them again costs nothing and never fails, because this is
			also the panic button and the one direction that must always work.

			Drawn for everybody: hiding is personal, so a guest has their own to open, and this is
			the control that opens it.

			Nothing is drawn until the server has answered.

			The client starts at shut, because shut is the safe thing to assume and the answer takes
			a moment to arrive. Drawn during that moment, the control would say "hidden items are
			hidden" and then correct itself, so every reload of an open vault would flash locked.
			There is no third state to draw: the honest answer before the answer arrives is nothing
			at all, and it is one control's width for one request.
		-->
		{#if vault.loaded}
			<Tooltip
				label={vault.unlocked
					? 'Hide hidden items'
					: vault.pinSet
						? 'Show hidden items (PIN)'
						: 'Set a PIN to hide items'}
				placement="bottom"
			>
				<!-- Icon-only through `icon=`, for the reason spelled out on the collapse control above:
				     handed in as children it is not icon-only and draws a 52px box. -->
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
			<!--
				The space it will take, held while the answer is on its way.

				Nothing is drawn and nothing is announced: the honest answer before the answer
				arrives is still nothing at all. What is not honest is the bar changing SHAPE for the
				length of one request: the control arrives, the row it is in grows by its width, and
				the three screen menus at the other end slide sideways on every page load. That is the
				exact fault this bar's own rule is written against, arriving as a flicker instead of as
				a difference between screens.
			-->
			<span class="reserved" aria-hidden="true"></span>
		{/if}

		<!--
			WHAT THE SCREEN PUTS UP HERE, after the two controls that are about the whole window.

			A screen's controls belong on the screen's own row and these are the exception: things that
			are about the window rather than about what is on it. Theater's silence sits beside the play
			control because it is the same question, and its key sheet beside them because the keys are
			the application's rather than the wall's. See `topExtra` and `atTheTop`.
		-->
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

		<div class="trailing">
			<!--
				How big the tiles are: at the far end of the bar, beside Add.

				Disabled rather than hidden where there is nothing to resize: a wall of people, a
				collection's own hand-ordered grid, Settings. A control that vanishes between screens is one
				people stop reaching for, because they cannot tell "not here" from "not there yet"; one that
				is visibly unavailable has answered them.
			-->
			<Tooltip
				label={sizable ? 'Tile size' : whyNot(screenBar.tools.resizable, NOT_HERE.resize)}
				placement="bottom"
			>
				<label class="size" class:off={!sizable}>
					<Icon name="grid_view" size={18} label="Tile size" />
					<!-- The shared slider, for the app's own fill: Chromium draws no filled part of a
					     track by itself, so a bare range input is a grey groove with a dot on it beside
					     the player's timeline filled in accent. -->
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

			<!--
				Add is admin-only, and not as a courtesy.

				Every one of the three things behind it (import a file, add a folder, paste a link)
				is a route the server refuses a guest. A menu whose every item fails is worse than no
				menu: it reads as a feature that is broken rather than one that is not theirs.
			-->
			{#if session.isAdmin}
				<AddButton />
			{/if}
		</div>
	</div>
</header>

<style>
	/*
	 * Three columns, so the search box is centred on the bar rather than on what is left over:
	 * equal ends with the box in a fixed middle column keep it put when the rail collapses or a
	 * control appears beside the account. `minmax(0, var(--search-width))` rather than a fixed
	 * track: below about 900px there is no room for the full box plus both ends, and a fixed track
	 * would push the actions off the edge instead of letting the box give way.
	 *
	 * `max-content` is the floor on the ends, and it is load-bearing. With named menus on the left
	 * end, an even split of the leftover can be narrower than its content, and an item with
	 * `justify-self: start` takes its content width anyway and overlaps what is beside it with no
	 * clip or warning. A `1fr` track is `minmax(auto, 1fr)`, and `auto` as a minimum is not the
	 * content's width for an item told where to sit, so `max-content` says it outright. The middle
	 * keeps a floor of 0, so when the window runs out of room the search box gives way, the only
	 * one of the three that can afford to.
	 *
	 * The box is then not dead-centre (the ends have different content widths, so it sits about
	 * 34px right of centre when everything fits). That is the right trade: a control cut in half is
	 * a fault, and a field a few pixels off the middle is not visible by eye.
	 */
	.topbar {
		grid-area: topbar;
		/*
		 * The bar stands at the top edge of the window, so it pads by that edge's safe area and
		 * paints its own ground under it: on a phone with a notch or a camera the presses sit below
		 * it while the bar's colour still reaches the glass. `--safe-top` is zero on a desk and in any
		 * browser that reports nothing, so this is the bar's ordinary height there. The token, never
		 * the inset read directly (see `--safe-top` in `app.css`).
		 */
		height: calc(var(--topbar-height) + var(--safe-top));
		padding-block-start: var(--safe-top);
		display: grid;
		grid-template-columns:
			minmax(max-content, 1fr)
			minmax(0, var(--search-width))
			minmax(max-content, 1fr);
		align-items: center;
		gap: var(--space-3);
		padding-inline: var(--space-4);
		/*
		 * The start inset puts the collapse control's centre on the same vertical line as the glyph
		 * beside every page title underneath: the control is icon-only and 36px, so its centre
		 * needs 16 + 18. Written as a token: the alignment is the fact, and the inset is what
		 * produces it at this button's width.
		 */
		padding-inline-start: var(--space-4);
		/* Askable by its width, for the trail's floor (`.trail`). The bar's width is the shell's
		   column and never its content's, so containing it changes no measurement. */
		container-type: inline-size;
		background: var(--sift-surface-1);
		border-bottom: 1px solid var(--border);
	}

	/*
	 * This bar is not the window's title bar. `WindowBar` is a strip of Sift's own above the whole
	 * shell, which holds the minimise, maximise and close buttons and is what you drag the window
	 * by, so this bar is an ordinary row of controls, identical in the desktop window and a
	 * browser, with the search box on the middle of the bar.
	 */

	/*
	 * The ends hug the search box, and only the two controls that belong to an edge stay there.
	 *
	 * Packed against the window's edges, the controls spread across the whole width of a wide
	 * window with the search box alone in the middle, and reaching Filter or the preview toggle
	 * from the box is a trip across the bar. So each end stretches across its track and packs its
	 * controls against the box. The rail's collapse keeps the far left, where it stays findable at
	 * both rail widths, and the tile size with Add keeps the far right.
	 */
	.topbar > :global(:first-child) {
		justify-self: stretch;
	}

	/*
	 * The rail's collapse and the screen's own menus, as one end of the bar.
	 *
	 * A wrapper rather than two grid children, because the grid has exactly three columns and the
	 * middle one is the search box: a fourth child would take the box's track and put it wherever the
	 * ends left it, which is the arrangement the three-column grid above was written to replace.
	 *
	 * `min-inline-size: 0` so this end can give way before the box does. The menus name themselves in
	 * words, so this end is wide and on a narrow window something has to yield,
	 * and the search box giving way first is what the middle track's `minmax(0, ...)` already says.
	 */
	.lead {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* The collapse takes the leftover, which parks it at the far left and pushes the menus after it
	   up against the search box. A margin, because a spacer would be one more thing to tab past. */
	.collapse > :global(*) {
		margin-inline-end: auto;
	}

	/*
	 * The trail's room: what this end has after the collapse and the menus, and a floor under it.
	 *
	 * `contain: inline-size` takes the trail's own words out of the bar's arithmetic: without it the
	 * longest trail would size this end's track (`max-content`) and push the search box aside by a
	 * whole trail. With it the room asks for its floor and nothing more, and takes whatever the
	 * track has beyond that (`flex: 1`); the trail folds to fit it.
	 *
	 * The floor is what the search box gives up for the trail, on every screen alike, and it
	 * follows the bar's own width: the bar less 820px (about what the collapse, the menus, the far
	 * end and a search box of a usable width take between them), between 64px (the fold's press
	 * and the start of where you are) and 360px. Measured with the rail open: at 1600 wide the
	 * trail has 360px, at 1280 about 210, at 1024 the 64; the search box is 520, 353 and 264 wide
	 * there, never under its floor, so the menus keep their place on the bar. A screen with more
	 * controls than that (Theater) takes back what its field is short of, down to the 64
	 * (`screenBar.trailGives`), before its menus leave the bar for a row of their own. With the
	 * menus gone and the field still short (the desktop window's caption buttons), the room goes
	 * whole (`screenBar.trailBare`).
	 */
	.trail {
		--trail-least: 64px;
		--trail-beside: 820px;
		--trail-most: 360px;
		flex: 1 1 0;
		contain: inline-size;
		min-inline-size: clamp(
			var(--trail-least),
			100cqi - var(--trail-beside) - var(--trail-gives),
			var(--trail-most)
		);
		display: flex;
		align-items: center;
	}

	.trail > :global(.crumbs) {
		flex: 1;
	}

	/* The trail's room given up whole, gap and all, once nothing else is left to give. */
	.trail.bare {
		display: none;
	}

	.actions {
		display: flex;
		align-items: center;
		justify-content: flex-start;
		gap: var(--space-2);
		justify-self: stretch;
	}

	/* A square the size of the control it stands in for. See the markup: this keeps the row's width
	   the same before and after the vault answers. */
	/* A control-sized square holding the place of a control not yet drawn. Not an in-flight line. */
	.reserved {
		inline-size: var(--control-height);
		block-size: var(--control-height);
	}

	/* The tile size and Add, as one pair at the end. A wider gap than the rest of the bar, on
	   purpose: everything else here is a square ghost button, and Add is a filled pill with its own
	   16px of padding. At the bar's ordinary 8px the slider's track runs straight into the blue. */
	.trailing {
		display: flex;
		align-items: center;
		gap: var(--space-4);
		margin-inline-start: auto;
	}

	/*
	 * The tile-size control's rules, kept with the control: a control that changes rooms without
	 * its stylesheet arrives undressed, and an unused selector left behind is how that ships
	 * unnoticed.
	 */
	.size {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		color: var(--sift-ink-3);
	}

	/* DRESSED BY: .steps (`Slider` draws the track, the fill and the thumb; this file only says how
	   wide the control is, which is a fact about the room the bar has). */
	.size :global(.steps) {
		inline-size: 96px;
	}

	/* Unavailable, and saying so. Dimmed rather than removed, and the cursor says it too for anybody
	   who reads that before they read colour.

	   The same 0.5 the shared button takes when it is disabled: the preview toggle immediately to
	   the left of this is a disabled Button on the screens where this is off, and two controls
	   sitting against each other must not be dimmed by different amounts for one meaning. */
	.size.off {
		opacity: 0.5;
	}

	/* There is no room for a slider beside a search box on a phone, and the screens that have tiles
	   to size are reached through the tabs rather than this bar at that width. */
	@media (max-width: 767px) {
		.size {
			display: none;
		}
	}

	/* Boxes that lay nothing out, so each control stands in its row as before; they exist to be
	   left off the phone's bar. */
	.collapse,
	.preview {
		display: contents;
	}

	/*
	 * The phone's bar: one line of squares, what decides the screen packed at the start (the
	 * search, then Filter and Sort) and what acts on the window at the end (Hidden, then Add).
	 *
	 * Two columns, because the search box is not on this bar at this width (see the markup): the
	 * start end takes what its squares need and the other end the rest, packed against the edge.
	 * The rail is off the screen, so the control that collapses it goes; there is no pointer to
	 * hover with, so the preview control goes.
	 */
	@media (max-width: 767px) {
		.topbar {
			grid-template-columns: max-content minmax(0, 1fr);
			gap: var(--space-2);
			/* A thumb's target, not a pointer's: every square on this bar is the touch target across,
			   which the 56px bar has room for. Through the variable every control here sizes itself
			   by, so the squares, the Add and the placeholder for Hidden stay one size. */
			--control-height: var(--touch-target);
		}

		.lead {
			justify-content: flex-start;
		}

		.actions {
			justify-content: flex-end;
		}

		/* Add stays last, beside Hidden rather than a line's width from it: on the desktop the
		   leftover on this end goes before the tile size, and at this width there is no tile size. */
		.trailing {
			margin-inline-start: 0;
		}

		.collapse,
		.preview {
			display: none;
		}
	}
</style>
