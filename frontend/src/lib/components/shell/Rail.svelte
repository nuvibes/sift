<script lang="ts">
	/* NOT ON THE GALLERY: the sidebar is a singleton the layout already renders, and it reads the current
	   address to say where you are. A second live one on the gallery would be the app drawn twice:
	   a copy that can drift from the real one and is looked at instead of it. The place to review
	   the shell is the shell. */

	import { Button, Scroller, Separator } from '$lib/components/common';
	import { carriesALink, readLink } from '$lib/components/common/drag-assign.svelte';
	import { fetchOnto } from '$lib/library/aimed-drop.svelte';
	import { capture } from '$lib/capture/capture.svelte';
	import { tick } from 'svelte';
	import { page } from '$app/state';
	import Icon from '$lib/components/Icon.svelte';
	import Logo from '$lib/components/Logo.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ContextMenu from '$lib/components/common/ContextMenu.svelte';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import ContextMenuGroup from '$lib/components/common/ContextMenuGroup.svelte';
	import VerbMenuItems from '$lib/components/common/VerbMenuItems.svelte';
	import { measure, move, slide } from '$lib/shell/motion.svelte';
	import { imports } from '$lib/library/imports.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { openSettingsInstead } from '$lib/settings-ui/settings-view';
	import { rail, type RailRegion } from './rail-state.svelte';
	import { isActive, navItem, RAIL_DIVIDER, type NavItem } from './nav';
	import {
		currentFullAmount,
		FULL_AMOUNT_COPY,
		fullAmountTip,
		pressFullAmount
	} from './full-amount';

	/*
	 * The rail is 208 wide with labels and 64 with icons alone, narrow for a small window or a
	 * press, and absent below the phone breakpoint. Classes, not a media query, because the markup
	 * changes too (the mark instead of the wordmark), so the breakpoint is watched in script. No
	 * forcing it open below 1024px: there is no room.
	 */
	const NARROW_RAIL = '(min-width: 768px) and (max-width: 1023px)';

	let narrow = $state(false);
	if (typeof matchMedia === 'function') {
		const media = matchMedia(NARROW_RAIL);
		narrow = media.matches;
		media.addEventListener('change', (event) => (narrow = event.matches));
	}

	/** Narrow for either reason. The button wins upwards; the window wins downwards. */
	const tight = $derived(narrow || rail.collapsed);
	const pathname = $derived(page.url.pathname);

	/*
	 * ONE turning signal, on the gear, for background work: anything in the queue that is not a
	 * download, since Settings is where that queue lives. A download turns nothing; its row keeps
	 * its count, and the Downloads screen shows its progress.
	 */
	const settingsWorking = $derived(imports.working > 0);

	/* The leaf or the bolt above the rule, or neither. See `full-amount.ts`. */
	const fullAmount = $derived(currentFullAmount(session.isAdmin));
	/* One press at a time, so a second cannot repeat or undo the first in flight. */
	let pressing = $state(false);

	async function press(): Promise<void> {
		pressing = true;
		try {
			await pressFullAmount(fullAmount !== 'full');
		} finally {
			pressing = false;
		}
	}

	/*
	 * HOW MANY SITES ARE ASKING FOR COOKIES, on the row that leads to them: the Edit cookies count,
	 * the one thing here waiting on a PERSON. Read through `imports` (`Imports.readCookies`), the
	 * status dot's store, rather than a second poll.
	 */

	/* The dot in words, appended to the Downloads label for a screen reader. */
	const DOWNLOAD_STATUS_LABEL = {
		none: '',
		success: ', a download finished',
		error: ', a download failed'
	} as const;

	/*
	 * What is actually drawn, in the order somebody arranged it: without put-away rows, rows a
	 * guest is not shown, and the rule, which splits the rest. The window's width takes nothing
	 * out, so the rail can be learned. Hiding admin rows only avoids offering a door; the server's
	 * refusal is the control.
	 */
	const drawn = $derived(
		rail.order
			.filter((id) => id === RAIL_DIVIDER || rail.shows(id))
			.map((id) => (id === RAIL_DIVIDER ? RAIL_DIVIDER : navItem(id)))
			.filter((entry): entry is NavItem | typeof RAIL_DIVIDER => entry !== undefined)
			.filter((entry) => entry === RAIL_DIVIDER || !entry.admin || session.isAdmin)
	);

	const split = $derived(drawn.indexOf(RAIL_DIVIDER));
	const isItem = (entry: NavItem | typeof RAIL_DIVIDER): entry is NavItem => entry !== RAIL_DIVIDER;
	const above = $derived(drawn.slice(0, split === -1 ? drawn.length : split).filter(isItem));
	const below = $derived(split === -1 ? [] : drawn.slice(split + 1).filter(isItem));

	/* The rail carries destinations only; saved searches live on the filter bar. */

	let railEl = $state<HTMLElement | null>(null);

	/*
	 * Rearranging is a mode, entered by holding a row or by Rearrange, since a link that can also be
	 * picked up gets picked up by accident; `draggable` is false otherwise.
	 *
	 * Whether a reflow is still running: `dragover` fires many times a second, and a slide started
	 * mid-slide measures rows part way through and misplaces them.
	 */
	let reflowing = false;

	async function rearrange(change: () => void): Promise<void> {
		/* Already animating: make the move, skip the animation, so the arrangement keeps following
		 * the pointer while slides never overlap. */
		if (reflowing) {
			change();
			return;
		}
		reflowing = true;
		const rows = railEl?.querySelectorAll('.row') ?? [];

		// Where the rows are before the change, the "from" of the slide.
		const before = measure(rows);
		change();
		await tick();
		slide(before);
		// Held for the slide's pace, since `slide` animates many rows with nothing single to await.
		setTimeout(() => (reflowing = false), REFLOW_MS);
	}

	/* The mode's controls arriving, so they read as a mode starting rather than a redraw. */
	function pop(element: Element): void {
		void move(element, { opacity: [0, 1], scale: [0.6, 1] }, { pace: 'fast', spring: true });
	}

	/** How long a reflow is left alone for, matching the pace `slide` runs at. */
	const REFLOW_MS = 180;

	/** Which row is being dragged, while one is. */
	let carrying = $state<string | null>(null);

	function startEditing(): void {
		// Held first: Cancel means "as it was when I started".
		rail.hold();
		rail.editing = true;
	}

	/** Put the rail back as it was when arranging started, and leave the mode. */
	function cancelEditing(): void {
		rail.revert();
		rail.editing = false;
		carrying = null;
		heldOpen = false;
	}

	function stopEditing(): void {
		rail.settle();
		rail.editing = false;
		carrying = null;
		// Or a hold that ended elsewhere would swallow the next ordinary click.
		heldOpen = false;
	}

	/* Leaving the screen leaves the mode, or the rail's rows would stop clicking behind it. */
	$effect(() => {
		void pathname;
		stopEditing();
	});

	/* How long a press lasts before it means "pick this up": past a drifting click, short of hidden. */
	const HOLD_MS = 450;

	let holdTimer: ReturnType<typeof setTimeout> | undefined;
	/* The click that ends a hold must not navigate. */
	let heldOpen = false;

	function pressStart(event: PointerEvent): void {
		// Only a mouse. A touch hold is the gesture that opens the menu, which is where Rearrange
		// is; two things watching the same hold would fight over it.
		if (event.pointerType !== 'mouse' || rail.editing) return;
		clearTimeout(holdTimer);
		holdTimer = setTimeout(() => {
			heldOpen = true;
			startEditing();
		}, HOLD_MS);
	}

	function pressEnd(): void {
		clearTimeout(holdTimer);
	}

	$effect(() => () => clearTimeout(holdTimer));

	function onRowClick(event: MouseEvent, item: NavItem): void {
		if (heldOpen) {
			// The click that ended the hold, swallowed once here.
			heldOpen = false;
			event.preventDefault();
			return;
		}
		if (rail.editing) {
			event.preventDefault();
			return;
		}
		if (item.panel) openSettingsInstead(event);
	}

	function onRowKeydown(event: KeyboardEvent, item: NavItem): void {
		if (!rail.editing) return;

		if (event.key === 'Escape') {
			event.preventDefault();
			stopEditing();
			return;
		}

		const direction = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
		if (direction === 0) return;

		/* The row travels with the focus, found again by id: a row crossing the rule is rebuilt in
		 * the other loop, and focusing the detached old element would send focus to the body. */
		event.preventDefault();
		void rearrange(() => rail.nudge(item.id, direction)).then(() => {
			railEl?.querySelector<HTMLElement>(`[data-rail-row="${item.id}"]`)?.focus();
		});
	}

	function onDragStart(event: DragEvent, item: NavItem): void {
		if (!rail.editing || !event.dataTransfer) return;
		carrying = item.id;
		// A fresh grip decides nothing yet.
		decidedAt = null;
		event.dataTransfer.effectAllowed = 'move';
		// Nothing is carried in it: an id on the clipboard would be offered to other pages.
		event.dataTransfer.setData('text/plain', '');
	}

	function onDragEnd(): void {
		carrying = null;
		landed = null;
		decidedAt = null;
		pendingRegion = null;
		overZone = null;
	}

	function allowDrop(event: DragEvent): void {
		if (carrying === null) return;
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
	}

	/*
	 * The rows move while the row is being dragged, rather than when it is let go, so letting go
	 * confirms rather than reveals. `landed` remembers the target and side, since `dragover` fires
	 * many times a second and a move that changes nothing must not re-animate.
	 */
	let landed = $state<string | null>(null);

	/*
	 * Where the pointer was when the last landing was decided. Moving rows moves what a still hand
	 * is over, so a landing answers only the hand travelling `TRAVEL` (16px: under half a row, well
	 * over a mouse's jitter).
	 */
	let decidedAt: number | null = null;

	const TRAVEL = 16;

	/** Whether the pointer has moved far enough since the last landing for this to be a new one. */
	function handMoved(y: number): boolean {
		return decidedAt === null || Math.abs(y - decidedAt) >= TRAVEL;
	}

	/*
	 * A link dragged in from another tab and dropped on a destination row. `carriesALink` refuses
	 * anything begun inside the app, so the reorder drag is never mistaken for one.
	 */
	let takingALink = $state<string | null>(null);

	/* The two rail rows that are DESTINATIONS rather than places: Favorites files the fetch under
	   the heart; Downloads is the window's plain fetch, aimed at the row anybody would aim at. */
	const TAKES_A_LINK: ReadonlySet<string> = new Set(['favorites', 'downloads']);

	function takesALink(item: NavItem, event: DragEvent): boolean {
		return TAKES_A_LINK.has(item.id) && carriesALink(event);
	}

	/*
	 * Both ends of a link held over a destination row, and BOTH have to cancel the event: the
	 * browser decides a drop from the last `dragover`, not the enter, and the reorder handler does
	 * not cancel while nothing is carried.
	 */
	function linkOver(event: DragEvent, item: NavItem): boolean {
		if (!takesALink(item, event)) return false;
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'link';
		takingALink = item.id;
		return true;
	}

	/*
	 * Leaving the row, as opposed to crossing onto its own glyph or label, which also fires
	 * `dragleave`: `relatedTarget` still inside the row means nothing was left.
	 */
	function linkOut(event: DragEvent, item: NavItem) {
		if (takingALink !== item.id) return;
		const entering = event.relatedTarget;
		const row = event.currentTarget;
		if (row instanceof Node && entering instanceof Node && row.contains(entering)) return;
		takingALink = null;
	}

	function dragOverRow(event: DragEvent, item: NavItem): void {
		/* A link from outside first, as in `endDrop`: the two cannot both be true. */
		if (linkOver(event, item)) return;
		allowDrop(event);
		if (carrying === null) return;

		/* Stopped here, always, including over the row being carried, or the region's handler
		 * underneath would send the row to the end of the half on any pass over a row. */
		event.stopPropagation();
		/* Over a row, so any armed zone landing is cleared, or a drop aimed at a row would apply it. */
		pendingRegion = null;
		overZone = null;
		if (carrying === item.id) return;

		/* A dead band around the middle of the row: flipping at the exact midpoint would re-place the
		 * row, whose reflow moves the pointer's target, which would flip it back. Inside the band the last
		 * decision stands. */
		const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
		const middle = box.top + box.height / 2;
		const band = box.height * 0.25;
		if (Math.abs(event.clientY - middle) < band) return;

		const side = event.clientY > middle ? 'after' : 'before';

		/* Nothing to do when the landing is where the row already is: the seam between two rows is
		 * one place with two spellings. Asked of the arrangement. */
		if (!rail.wouldMove(carrying, item.id, side)) return;

		const at = `${item.id}:${side}`;
		if (landed === at) return;
		if (!handMoved(event.clientY)) return;
		landed = at;
		decidedAt = event.clientY;

		const moving = carrying;
		void rearrange(() => rail.placeBy(moving, item.id, side));
	}

	/* Which zone the pointer is over, or null, drawn grown and outlined so the landing is seen. */
	let overZone = $state<RailRegion | null>(null);

	/*
	 * A landing on empty space is decided while dragging and applied when the row is let go.
	 *
	 * Applied at once, a zone landing moves the row into the other half, which shifts the zone under
	 * a still pointer and throws the row to the end. So a zone only lights up, and the rail shifts on
	 * release. Rows still reflow live, since a row landing does not change halves.
	 */
	let pendingRegion: RailRegion | null = null;

	/*
	 * Which zone a point belongs to, worked out from the rail, so every pixel of it (the band's
	 * margins, the gap under the brand) is in exactly one of three bands and no drop is refused.
	 */
	function regionAt(y: number): RailRegion | null {
		const band = railEl?.querySelector('.zone.between');
		if (!band) return null;
		const box = band.getBoundingClientRect();
		if (y < box.top) return 'above';
		if (y > box.bottom) return 'below';
		return 'middle';
	}

	function dragOverRail(event: DragEvent): void {
		allowDrop(event);
		if (carrying === null) return;
		const region = regionAt(event.clientY);
		if (region === null) return;
		overZone = region;
		pendingRegion = region;
		landed = `region:${region}`;
	}

	/** The pointer has left the rail altogether, so nothing should still look like a destination. */
	function leaveRail(event: DragEvent): void {
		// `dragleave` also fires onto a child; only leaving the rail itself counts.
		if (event.currentTarget !== event.target) return;
		overZone = null;
	}

	/*
	 * Letting go. A row landing already happened in `dragOverRow`; a zone landing is made only
	 * here. `stopPropagation` so putting a row straight back is not "end of this half";
	 * `preventDefault` because an unconsumed drop navigates.
	 */
	function endDrop(event: DragEvent, item?: NavItem): void {
		/* A link from outside, before the reorder half, which returns early with nothing carried. */
		takingALink = null;
		if (item && takesALink(item, event)) {
			const url = readLink(event);
			if (url) {
				event.preventDefault();
				event.stopPropagation();
				/* Favorites AIMS the fetch; Downloads is the window's own plain fetch, one path. */
				if (item.id === 'favorites') void fetchOnto(url, 'favorite', null, 'Favorites');
				else void capture.handleDrop(event.dataTransfer as DataTransfer);
			}
			return;
		}
		if (carrying === null) return;
		event.preventDefault();
		event.stopPropagation();

		if (pendingRegion !== null) {
			const moving = carrying;
			const region = pendingRegion;
			void rearrange(() => rail.placeInRegion(moving, region));
		}

		carrying = null;
		landed = null;
		decidedAt = null;
		pendingRegion = null;
		overZone = null;
	}
</script>

{#snippet navLink(item: NavItem)}
	{@const active = isActive(item.href, pathname)}
	{@const isDownloads = item.id === 'downloads'}
	{@const downloadStatus = isDownloads ? imports.downloadStatus : 'none'}
	{@const downloadsActive = isDownloads && imports.downloading > 0}
	{@const cookiesWanted = isDownloads ? imports.cookiesWanted : 0}
	{@const turning = item.id === 'settings' && settingsWorking}
	{#snippet link()}
		<a
			href={item.href}
			class="item"
			class:active
			aria-current={active ? 'page' : undefined}
			data-rail-row={item.id}
			aria-label={isDownloads
				? `${item.label}${downloadsActive ? ', downloading' : ''}${DOWNLOAD_STATUS_LABEL[downloadStatus]}${
						cookiesWanted > 0 ? `, ${cookiesWanted} waiting for cookies` : ''
					}`
				: item.label}
			draggable={rail.editing}
			onclick={(event) => onRowClick(event, item)}
			onkeydown={(event) => onRowKeydown(event, item)}
			onpointerdown={pressStart}
			onpointerup={pressEnd}
			onpointerleave={pressEnd}
			onpointercancel={pressEnd}
			ondragstart={(event) => onDragStart(event, item)}
			ondragend={onDragEnd}
		>
			<!--
				The two rows that report as well as navigate. Settings turns the gear for background
				work, the one thing moving in a still rail. Downloads keeps its still arrow and says
				only an outcome not yet seen (the light), the Sites waiting for cookies (the count),
				and, to a screen reader, that a download is fetching.
			-->
			<span class="glyph" class:working={turning}>
				<Icon name={item.icon} filled={active} size={20} />
				<!-- The Downloads status light until the screen is opened; the label carries it in words. -->
				{#if downloadStatus !== 'none'}
					<span class="status status-{downloadStatus}" aria-hidden="true"></span>
				{/if}
			</span>
			<span class="label">{item.label}</span>
			<!-- The count after the label, absent from the icons-only rail where it would be crushed;
			     the label carries it at every width. -->
			{#if cookiesWanted > 0 && !tight}
				<span class="wanted" aria-hidden="true">{cookiesWanted}</span>
			{/if}
		</a>
	{/snippet}

	<!--
		One row: the link, and while arranging, the control that puts it away, a sibling since a
		button inside an anchor leaves the browser to pick which one works.
	-->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="row"
		class:carrying={carrying === item.id}
		class:taking={takingALink === item.id}
		data-drop-zone={TAKES_A_LINK.has(item.id) ? 'link' : undefined}
		ondragover={(event) => dragOverRow(event, item)}
		ondragenter={(event) => void linkOver(event, item)}
		ondragleave={(event) => linkOut(event, item)}
		ondrop={(event) => endDrop(event, item)}
	>
		<ContextMenu label="{item.label} row" triggerClass="trigger">
			{#snippet items()}
				<!-- What this destination offers (`nav.ts`), first; the arranging below is the same on
				     every row. -->
				{#if item.verbs}
					<ContextMenuGroup>
						<VerbMenuItems verbs={item.verbs(imports)} ids={[]} />
					</ContextMenuGroup>
				{/if}
				<!-- Arranging, for anybody not holding a mouse button: Move up and Move down work without
				     entering the mode. A sequence, not alphabetical. -->
				<ContextMenuGroup>
					{#if rail.editing}
						<ContextMenuItem label="Done" icon="check" onselect={stopEditing} />
					{:else}
						<ContextMenuItem label="Rearrange" icon="drag_indicator" onselect={startEditing} />
					{/if}
					<ContextMenuItem
						label="Move up"
						icon="arrow_upward"
						onselect={() => void rearrange(() => rail.nudge(item.id, -1))}
					/>
					<ContextMenuItem
						label="Move down"
						icon="arrow_downward"
						onselect={() => void rearrange(() => rail.nudge(item.id, 1))}
					/>
					<!-- Settings is refused rather than absent, so the menu keeps its shape. -->
					<ContextMenuItem
						label="Hide"
						icon="visibility_off"
						disabled={item.fixed}
						onselect={() => void rearrange(() => rail.hide(item.id))}
					/>
				</ContextMenuGroup>
			{/snippet}

			{#if rail.iconsOnly && !rail.editing}
				<!--
					A tooltip only while the label is off screen (either reason it goes); the link's
					`aria-label` names it at every width. Not while arranging, when a row goes nowhere.
				-->
				<Tooltip label={item.label} placement="right" stretch>
					{@render link()}
				</Tooltip>
			{:else}
				{@render link()}
			{/if}
		</ContextMenu>

		{#if rail.editing && !item.fixed}
			<span class="put-away-at">
				<Tooltip label="Hide {item.label}">
					<Button
						tone="ghost"
						size="small"
						shape="circle"
						class="put-away"
						icon="close"
						aria-label="Hide {item.label}"
						onclick={() => void rearrange(() => rail.hide(item.id))}
						{@attach pop}
					/>
				</Tooltip>
			</span>
		{/if}
	</div>
{/snippet}

<nav
	id="main-rail"
	class="rail"
	class:collapsed={tight}
	class:editing={rail.editing}
	class:carrying={carrying !== null}
	aria-label="Main"
	bind:this={railEl}
	ondragover={dragOverRail}
	ondragleave={leaveRail}
	ondrop={endDrop}
>
	<!--
		The rail scrolls rather than squashing its rows. The whole body scrolls, brand and footer
		included, so the "you are here" marker in the rail's padding is not clipped; `fill` keeps the
		footer at the bottom when there is room.
	-->
	<Scroller fill>
		<div class="rail-body">
			<!-- The brand, which is also the way home: the mark alone when narrow, since the lockup at
			     64px is illegible, and a link at every width. -->
			<a href="/browse" class="brand" aria-label="Sift, home">
				<Logo variant={tight ? 'mark' : 'lockup'} height={tight ? 30 : 48} />
			</a>

			<!-- The empty space in a region takes a drop too, so an emptied region can be refilled. -->
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="group" class:zoned={rail.editing} class:over={overZone === 'above'}>
				{#each above as item (item.id)}
					{@render navLink(item)}
				{/each}
			</div>

			<!-- The band between the two halves, while arranging: a drop here lands first below the
			     rule, which no row a few pixels tall could be aimed at for. -->
			{#if rail.editing}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="zone between" class:over={overZone === 'middle'}></div>
			{/if}

			<!-- The bottom of the rail, and the rule above it: a position in the arrangement, not a wall. -->
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="group footer" class:zoned={rail.editing} class:over={overZone === 'below'}>
				<!--
					The leaf, or the bolt: background work stepping back while this device is in use, and
					the press that overrules it, in the glyphs' column above the rule. Drawn only while it
					is in play (`full-amount.ts`), and not while arranging.
				-->
				{#if fullAmount !== null && !rail.editing}
					<div class="full-amount">
						<Tooltip label={fullAmountTip(fullAmount)} placement="right">
							<Button
								tone="ghost"
								shape="circle"
								class="rail-full-amount"
								icon={fullAmount === 'full' ? 'bolt' : 'energy_savings_leaf'}
								iconSize={20}
								iconFilled
								aria-label={FULL_AMOUNT_COPY.name}
								pressed={fullAmount === 'full'}
								disabled={pressing}
								onclick={() => void press()}
							/>
						</Tooltip>
					</div>
				{/if}
				<Separator class="rail-rule" />
				{#each below as item (item.id)}
					{@render navLink(item)}
				{/each}

				{#if rail.editing}
					<!-- Two ways out: Done keeps what was moved; Cancel puts the rail back as it began. -->
					<Button class="cancel" onclick={cancelEditing}>Cancel</Button>
					<Button tone="primary" class="done" onclick={stopEditing} {@attach pop}>Done</Button>
				{/if}
			</div>
		</div>
	</Scroller>
</nav>

<style>
	/* Turning while there is work, still while there is not. */
	.glyph {
		display: grid;
		place-items: center;
		position: relative;
	}

	/*
	 * The Downloads status light, cut into the top right of the glyph, where badges go: the ring in
	 * the rail's own ground punches a gap, so it is a badge on any glyph.
	 */
	.status {
		position: absolute;
		inset-block-start: -2px;
		inset-inline-end: -2px;
		inline-size: 8px;
		block-size: 8px;
		border-radius: var(--radius-full);
		border: 1.5px solid var(--sift-surface-1);
		box-sizing: content-box;
	}

	/* The one number here about something wrong, so the warning colour; tabular, since it changes. */
	.wanted {
		margin-inline-start: auto;
		color: var(--sift-warn);
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}

	.status-success {
		background: var(--sift-ok);
	}

	.status-error {
		background: var(--sift-bad);
	}

	/*
	 * The leaf and the bolt, in the glyphs' column, in `--sift-ok`, not the accent a person may have
	 * recoloured: considerate, or flat out because somebody asked. Held on hover too.
	 */
	.full-amount {
		display: flex;
		/* No inline padding: the button is a row's glyph cell wide, so the centres line up. */
		padding-block-end: var(--space-2);
	}

	.rail.collapsed .full-amount {
		justify-content: center;
	}

	.full-amount :global(.btn.ghost.rail-full-amount),
	.full-amount :global(.btn.ghost.rail-full-amount:hover:not(:disabled)) {
		color: var(--sift-ok);
	}

	.full-amount :global(.btn.ghost.rail-full-amount[aria-pressed='true']) {
		--btn-ground: var(--sift-ok-bg);
		background-color: var(--btn-ground);
	}

	.glyph.working {
		animation: turn var(--dur-loop) linear infinite;
	}

	/* Somebody who has asked for less movement gets the fact without the motion. */
	:global(:root[data-motion='reduce']) .glyph.working {
		animation: none;
		color: var(--sift-accent-text);
	}

	/*
	 * THE CELL AND THE BODY ARE TWO BOXES: the rail is the grid cell (place, width, the collapse);
	 * the body inside the scroller holds the padding, the gap and the halves.
	 */
	.rail {
		grid-area: rail;
		width: var(--rail-width);
		/* The widest movement in the shell, so slow enough to read as one thing sliding. */
		transition: width var(--dur-slow) var(--ease);
		/* Re-stated here, since a modal's inert guard is inherited down through the scroll region. */
		pointer-events: auto;
		/* No background and no border: the gap to the inset content card is the separation. */
		min-block-size: 100%;
		/* The scroller inside is `block-size: 100%`, which needs a definite height to resolve. */
		block-size: 100%;
	}

	.rail-body {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		padding: var(--space-3);
		/* Fill the scroller's content box and never give that height back, or it never scrolls. */
		flex: 1 0 auto;
	}

	:global(:root[data-motion='reduce']) .rail {
		transition: none;
	}

	.brand {
		display: flex;
		align-items: center;
		padding-inline: var(--space-2);
		margin-bottom: var(--space-2);
		flex: none;
	}

	.group {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	/*
	 * The top half takes the empty space, rather than a margin pushing the bottom down, so the gap
	 * belongs to the group, whose drop handler means "the end of this half".
	 */
	.group:not(.footer) {
		/* GROW, NEVER SHRINK: shrinking would absorb the shortfall here, and the scroller around it
		   would never have anything to scroll, leaving the rows stacked on one another. */
		flex: 1 0 auto;
	}

	.footer {
		flex: none;
		padding-top: var(--space-3);
	}

	/*
	 * A drop zone, drawn only while arranging: dashed and quiet at rest, emphasised under the
	 * pointer. The outline covers all the area that takes a drop. Nothing here changes size while a
	 * row is in the air (that would move the target under the hand), so the emphasis is an edge, the
	 * accent, a tint and a painted scale.
	 */
	.zone,
	.group.zoned {
		border: 1px dashed var(--sift-line);
		border-radius: var(--radius-md);
		padding: var(--space-2);
		transition:
			border-color var(--dur-fast) var(--ease),
			background-color var(--dur-fast) var(--ease),
			scale var(--dur-fast) var(--ease);
	}

	.zone.over,
	.group.zoned.over {
		border-color: var(--sift-accent);
		border-style: solid;
		background-color: color-mix(in oklab, var(--sift-accent) 12%, transparent);
	}

	/* The band between the halves holds nothing, so its size is its own, and fixed. */
	.zone.between {
		flex: none;
		/* Tall enough to aim at: a smaller band can be crossed between two `dragover` events. */
		block-size: 40px;
		margin-block: var(--space-1);
		transition:
			border-color var(--dur-fast) var(--ease),
			background-color var(--dur-fast) var(--ease),
			scale var(--dur-fast) var(--ease);
	}

	/* The band grows, scaled rather than resized, so it is not missed and moves nothing. */
	.zone.between.over {
		scale: 1.02 1.15;
	}

	/* Somebody who has asked for less movement gets the colour and not the growth. */
	:global(:root[data-motion='reduce']) .zone.between.over {
		scale: none;
	}

	/* Beside Done and quieter (`secondary`): two primary buttons side by side is a coin toss. */
	.rail.editing :global(.cancel) {
		inline-size: 100%;
		margin-block-end: var(--space-2);
	}

	/* The rule's place in the column is the rail's; the line is the shared Separator's. */
	.group :global(.rail-rule) {
		inline-size: auto;
		margin: var(--space-3) var(--space-2);
		background: var(--sidebar-border);
	}

	/* The row stretches, and so does the menu's wrapper, or only the words would be clickable. */
	.row {
		position: relative;
		display: flex;
		width: 100%;
		/* A destination is 38px tall or not drawn, never squashed by the column. */
		flex: none;
	}

	.row :global(.trigger) {
		width: 100%;
	}

	.item {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		/* Fill the row, so the empty space after a short label answers too. */
		width: 100%;
		height: 38px;
		padding-inline: var(--space-2);
		border-radius: var(--radius-md);
		color: var(--sidebar-foreground);
		text-decoration: none;
		font: var(--text-body);
		white-space: nowrap;
		/* A row is held down to rearrange the rail, and a held press must not select its label. */
		user-select: none;
		position: relative;
		transition:
			background-color var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	.item:hover {
		background: var(--accent);
		color: var(--accent-foreground);
	}

	.item.active {
		background: var(--sidebar-accent);
		color: var(--sidebar-accent-foreground);
	}

	/* The marker for where you are, half the rail's padding out: in the gutter, inside the rail. */
	.item.active::before {
		content: '';
		position: absolute;
		left: calc(var(--space-2) * -1);
		top: 50%;
		translate: 0 -50%;
		width: 3px;
		height: 20px;
		border-radius: var(--radius-sm);
		background: var(--sidebar-primary);
	}

	/*
	 * Being arranged: the rows wiggle, the signal that the rail is in a mode, as a phone's icons do;
	 * the hide buttons say it again without motion for anybody who turned animation off.
	 */
	.rail.editing .item {
		cursor: grab;
	}

	/*
	 * The phone-home-screen wiggle: fast, a degree or so, and a small shift on its own period, so no
	 * two rows are ever in step and it reads as nervous rather than as a pendulum. Linear, since an
	 * eased curve dwells at the extremes, where a stop looks like a stop.
	 */
	.rail.editing .row {
		animation:
			wiggle-turn var(--dur-wiggle) linear infinite,
			wiggle-shift var(--dur-jostle) linear infinite;
	}

	/* Every row a different distance into both cycles (negative delays), so the rail never leans in
	 * a pattern. */
	.rail.editing .row:nth-child(3n + 1) {
		animation-delay: calc(var(--dur-wiggle) * -0.31), calc(var(--dur-jostle) * -0.57);
	}

	.rail.editing .row:nth-child(3n + 2) {
		animation-delay: calc(var(--dur-wiggle) * -0.65), calc(var(--dur-jostle) * -0.14);
	}

	.rail.editing .row:nth-child(3n) {
		animation-delay: calc(var(--dur-wiggle) * -0.15), calc(var(--dur-jostle) * -0.78);
	}

	/* Nothing wiggles while a row is dragged: the reflow's slide animates the same properties. */
	.rail.editing.carrying .row {
		animation: none;
	}

	:global(:root[data-motion='reduce']) .rail.editing .row {
		animation: none;
	}

	/* The row being carried, dimmed: the space it will return to shows where it is dropping. */
	.row.carrying {
		opacity: 0.4;
	}

	/*
	 * A destination with something held over it: the window-wide offer at the size of one row
	 * (dashed accent edge, accent wash), so the rail, a card and the window speak one language. An
	 * `outline`, pulled inside, since an inset shadow would paint under the glyph and label.
	 */
	.row.taking {
		border-radius: var(--radius-md);
		outline: 2px dashed var(--sift-accent);
		outline-offset: -2px;
		background: var(--sift-accent-wash);
	}

	/*
	 * `:global`, because the class lands on an element compiled in `Button`'s file, which a scoped
	 * rule here would never reach. Only what is about the RAIL is set; the rest is the button's.
	 * The wrapper is placed so the tooltip and the button keep their own layout.
	 */
	.row .put-away-at {
		position: absolute;
		inset-block-start: -2px;
		inset-inline-end: -2px;
	}

	.row :global(.put-away) {
		inline-size: 20px;
		block-size: 20px;
		/* A ring in the sidebar's colour, so the cross sits ON the row. */
		border: 1.5px solid var(--sidebar);
		background: var(--sift-surface-4);
		color: var(--sift-ink);
	}

	.row :global(.put-away:hover) {
		background: var(--sift-bad);
		color: var(--destructive-foreground);
	}

	/* The way out of rearranging: the full width of the rail. */
	.rail :global(.done) {
		inline-size: 100%;
		block-size: 38px;
		margin-block-start: var(--space-2);
	}

	/* No rail on a phone; the tabs take over. Here, since a component's scoped style outranks a
	   :global() rule aimed at it from outside. */
	@media (max-width: 767px) {
		.rail {
			display: none;
		}
	}

	/*
	 * Icons only: the labels and the width go, and the tooltip names them. One class, reached by the
	 * button and by the narrow window (`NARROW_RAIL`), never a media query copy of these rules.
	 */
	.rail.collapsed {
		width: var(--rail-width-collapsed);
	}

	/* On the body rather than the rail: the body is the flex column. */
	.rail.collapsed .rail-body {
		align-items: center;
	}

	.rail.collapsed .label {
		display: none;
	}

	.rail.collapsed .item {
		justify-content: center;
		width: 40px;
		padding-inline: 0;
	}

	.rail.collapsed .row,
	.rail.collapsed .row :global(.trigger),
	.rail.collapsed :global(.done),
	.rail.collapsed :global(.cancel) {
		width: 40px;
	}

	.rail.collapsed .brand {
		padding-inline: 0;
	}
</style>
