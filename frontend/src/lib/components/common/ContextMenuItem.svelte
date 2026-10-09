<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ContextMenuItem',
		category: 'surface',
		role: 'one row of a menu: a verb or a checkbox, its icon, and whether it is destructive',
		basis: 'bits-ui:ContextMenu',
		states: ['default', 'filled', 'destructive', 'disabled', 'checked', 'with a shortcut']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * DRESSED BY: .ui-menu (ContextMenu styles the surface a row's own rows open onto; it is the
	 * same surface the outer menu is drawn on)
	 * One line in a context menu, or a row opening out to hold rows of its own; the behaviour is
	 * the
	 * library's. `destructive` is red, which means one thing here: this destroys something.
	 */
	import type { Snippet } from 'svelte';
	import { ContextMenu } from 'bits-ui';
	import Scroller from './Scroller.svelte';
	import { theMenu } from './menu-portal';
	import { givesWayToARestingPointer } from './menu-resting.svelte';
	import { phoneWidth } from './phone-width.svelte';
	import { strokes } from '$lib/components/player/swipe';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Row {
		label: string;
		icon?: IconName;
		/** Draw the glyph solid, for glyphs whose outline is mostly empty space. */
		filled?: boolean;
		destructive?: boolean;
		disabled?: boolean;
		/** A row whose press flips a state: a checkbox row, its tick the state. */
		checked?: boolean;
		/** With `checked`: one choice of several, so picking closes the menu (`menuitemradio`). */
		oneOf?: boolean;
		/** Its flyout as wide as its own rows, free of the menu's width floor. */
		fit?: boolean;
		/** A second line about the row's subject ("Last: ..."); absent draws nothing. */
		note?: string;
	}

	/* A row acts or holds rows, never both, so nothing acts as a pointer passes. */
	type Props = Row &
		(
			| { children: Snippet; fit?: boolean; onselect?: never; href?: never; onfollow?: never }
			| { children?: never; fit?: never; onselect: () => void; href?: never; onfollow?: never }
			/* A row that goes somewhere, as a real link. */
			| {
					children?: never;
					fit?: never;
					onselect?: () => void;
					href: string;
					/** Run before the link is followed, as a crumb's `returnTo`. */
					onfollow?: (event: MouseEvent) => void;
			  }
		);

	let {
		label,
		icon,
		filled = false,
		destructive = false,
		disabled = false,
		fit = false,
		checked,
		oneOf = false,
		note,
		children,
		onselect,
		href,
		onfollow
	}: Props = $props();

	/* Where the flyout is drawn and dressed (menu-portal.ts). */
	const menu = theMenu();

	/* Whether its rows show, so a stroke down a sheet can put them away. */
	let subOpen = $state(false);

	/* The row that opens it, for menu-resting.svelte.ts. */
	let subTrigger = $state<HTMLElement | null>(null);
	givesWayToARestingPointer({
		open: () => subOpen,
		close: () => (subOpen = false),
		trigger: () => subTrigger
	});
</script>

{#if children}
	<ContextMenu.Sub bind:open={subOpen}>
		<ContextMenu.SubTrigger class="item" {disabled} bind:ref={subTrigger}>
			{#if icon}<Icon name={icon} {filled} />{/if}
			<span class="said">
				<span>{label}</span>
				{#if note}<span class="note">{note}</span>{/if}
			</span>
			<span class="arrow"><Icon name="chevron_right" size={16} /></span>
		</ContextMenu.SubTrigger>
		<!-- Portalled, or the menu's scrolling region would clip the flyout (menu-portal.ts). -->

		<ContextMenu.Portal to={menu.where() ?? undefined}>
			{#if phoneWidth.yes}
				<!-- At a phone's width its rows are a sheet over the sheet, put back by a stroke down its head.
				DRESSED BY: .menu-sheet (ContextMenu styles the sheet every menu is at a phone width)
				DRESSED BY: .menu-sheet-head (ContextMenu styles the sheet's head beside the sheet) -->
				<ContextMenu.SubContentStatic class="ui-menu menu-sheet">
					<p
						class="menu-sheet-head"
						aria-hidden="true"
						{@attach strokes(() => ({ live: subOpen, on: { down: () => (subOpen = false) } }))}
					>
						{label}
					</p>
					<Scroller arrows>
						{@render children()}
					</Scroller>
				</ContextMenu.SubContentStatic>
			{:else}
				<ContextMenu.SubContent class="ui-menu {fit ? 'fits' : ''}">
					<!-- The rows scroll on the menu's ceiling. -->
					<Scroller arrows>
						{@render children()}
					</Scroller>
				</ContextMenu.SubContent>
			{/if}
		</ContextMenu.Portal>
	</ContextMenu.Sub>
{:else if checked !== undefined && oneOf}
	<!-- One choice of several: the menu closes on the pick. -->
	<ContextMenu.CheckboxItem {disabled} {checked} onSelect={() => onselect?.()}>
		{#snippet child({ props })}
			<div {...props} role="menuitemradio" class="item {destructive ? 'destructive' : ''}">
				<span class="tick" class:unpicked={!checked}><Icon name="check" {filled} /></span>
				<span>{label}</span>
			</div>
		{/snippet}
	</ContextMenu.CheckboxItem>
{:else if checked !== undefined}
	<!-- A checkbox row: refusing the select keeps the menu open and the state the caller's. -->
	<ContextMenu.CheckboxItem
		class="item {destructive ? 'destructive' : ''}"
		{disabled}
		{checked}
		onSelect={(event: Event) => {
			event.preventDefault();
			onselect?.();
		}}
	>
		<Icon name={checked ? 'check_box' : 'check_box_outline_blank'} {filled} />
		<span>{label}</span>
	</ContextMenu.CheckboxItem>
{:else if href !== undefined}
	<!-- The library's row, drawn as a link. -->
	<ContextMenu.Item {disabled} onSelect={onselect}>
		{#snippet child({ props })}
			<a
				{...props}
				class="item link {destructive ? 'destructive' : ''}"
				{href}
				onclick={(event: MouseEvent) => {
					(props.onclick as ((event: MouseEvent) => void) | undefined)?.(event);
					onfollow?.(event);
				}}
			>
				{#if icon}<Icon name={icon} {filled} />{/if}
				<span class="said">
					<span>{label}</span>
					{#if note}<span class="note">{note}</span>{/if}
				</span>
			</a>
		{/snippet}
	</ContextMenu.Item>
{:else}
	<ContextMenu.Item class="item {destructive ? 'destructive' : ''}" {disabled} onSelect={onselect}>
		{#if icon}<Icon name={icon} {filled} />{/if}
		<span class="said">
			<span>{label}</span>
			{#if note}<span class="note">{note}</span>{/if}
		</span>
	</ContextMenu.Item>
{/if}

<style>
	/*
	 * The app's row inset (--menu-row-padding), from `.ui-menu` only, as other lists use `.item`.
	 */

	:global(.ui-menu .item) {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--menu-row-padding);
		border-radius: var(--menu-row-radius);
		font: var(--text-body);
		color: var(--sift-ink);
		cursor: pointer;
		user-select: none;
		/* The ground steps; data-highlighted covers pointer and keyboard. */
		transition: background var(--dur-instant) var(--ease);
	}

	/* The tick of one choice of several holds its room on every row, so the labels line up. */
	.tick {
		display: inline-flex;
	}

	.tick.unpicked {
		visibility: hidden;
	}

	/* A row that is a link reads as a row, not as underlined words. */
	:global(.ui-menu a.item.link) {
		text-decoration: none;
	}

	:global(.ui-menu .item[data-highlighted]) {
		background: var(--menu-row-highlight);
	}

	/* A flyout as wide as its rows. See `fit`. */
	:global(.ui-menu.fits) {
		min-inline-size: 0;
	}

	/* The one disabled strength; a row has no pressed state. */
	:global(.ui-menu .item[data-disabled]) {
		opacity: var(--disabled-opacity);
		cursor: default;
	}

	/* Red means one thing in this app: this destroys something. Nothing else uses it. */
	:global(.ui-menu .item.destructive) {
		color: var(--sift-bad-text);
	}

	/* The chevron at the row's end; global, so PickMenu's row wears it too. */
	:global(.ui-menu .arrow) {
		display: inline-flex;
		margin-inline-start: auto;
		color: var(--sift-ink-3);
	}

	/* The label and its note as one column. */
	.said {
		display: flex;
		flex-direction: column;
	}

	/* Quiet ink-3, not ink-4: a date is read from it. */
	.note {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}
</style>
