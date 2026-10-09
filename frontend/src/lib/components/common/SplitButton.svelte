<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'SplitButton',
		category: 'primitive',
		role: 'a main act and a second one sharing its edge, or a door to a few',
		basis: 'composes:Button,MenuButton',
		states: ['with an act', 'with a menu', 'a door on each half']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * WHY NOT BITS-UI: there is no split button in it. This is two of the shared Buttons, which are
	 * the library's behaviour already, joined at the corner; either half can be a MenuButton door.
	 */
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	import Button, { type ButtonSize, type ButtonTone } from '$lib/components/common/Button.svelte';
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import type { IconName } from '$lib/design/icons';

	/* No `class`: a caller restyling a half would undo the join, the only thing this adds. */
	interface Props extends Omit<HTMLButtonAttributes, 'class'> {
		/** How loud the pair is. Both halves take it, so they read as one control. */
		tone?: ButtonTone;
		size?: ButtonSize;
		/** The glyph on the main half, before its words. */
		icon?: IconName;
		children?: Snippet;
		/** Rows behind the main half, which then opens a menu instead of firing `onclick`. */
		leadMenu?: Snippet;
		/** Whether the main half's menu scrolls its own contents (see `MenuButton.scrolls`). */
		leadMenuScrolls?: boolean;
		/** What the main half's menu is called; required with `leadMenu`, enforced below. */
		leadMenuLabel?: string;
		/** The trailing half's glyph; a chevron when it is a door. */
		trailingIcon?: IconName;
		/** The trailing half's name, for its tooltip and a screen reader; required. */
		trailingLabel: string;
		/** The trailing half was pressed. Unused when the half is a door. See `menu`. */
		ontrailing?: () => void;
		/** The main half alone cannot be pressed, while the trailing menu still opens. */
		leadDisabled?: boolean;
		/** Rows behind the trailing half, which then opens a menu instead of `ontrailing`. */
		menu?: Snippet;
		/**
		 * The pointer on the trailing half, reported apart so a hover action on the main does not
		 * fire.
		 */
		ontrailingenter?: () => void;
		ontrailingleave?: () => void;
		/** The trailing half alone is unavailable. */
		trailingDisabled?: boolean;
		/** Fills the width it is given: the main half grows, the chevron keeps its square. */
		full?: boolean;
		/** The main half's act is running: its arc turns and the pair takes no second press. */
		busy?: boolean;
	}

	let {
		tone = 'secondary',
		size,
		icon,
		children,
		leadMenu,
		leadMenuLabel,
		leadMenuScrolls = true,
		trailingIcon = 'expand_more',
		trailingLabel,
		ontrailing,
		menu,
		ontrailingenter,
		ontrailingleave,
		trailingDisabled = false,
		full = false,
		leadDisabled = false,
		busy = false,
		...rest
	}: Props = $props();

	/* In an effect, as in `Chip`: a prop read at the top is checked on the first render only. */
	$effect(() => {
		if (leadMenu !== undefined && !leadMenuLabel) {
			throw new Error('A main half that opens a menu has to name it. See `leadMenuLabel`.');
		}
	});
</script>

<!-- One control made of two, announced together. -->
<div class="split" class:full role="group">
	<!-- The caller's props go to the main half, unless it is a door; each half has its own hook,
	since the tooltip wrapping the trailing one defeats :first-child. -->

	<span class="half lead">
		{#if leadMenu}
			<!--
			The door on the worded half; the caller's `onclick` is not wired: one press, one act.
			-->
			<MenuButton
				label={leadMenuLabel ?? ''}
				disabled={leadDisabled || busy || rest.disabled === true}
				scrolls={leadMenuScrolls}
			>
				{#snippet trigger({ props })}
					<Button {...props} {tone} {size} {icon} {full} {busy} type="button"
						>{#if children}{@render children()}{/if}</Button
					>
				{/snippet}
				{@render leadMenu()}
			</MenuButton>
		{:else}
			<Button
				{tone}
				{size}
				{icon}
				{full}
				{busy}
				{...rest}
				disabled={leadDisabled || rest.disabled === true}
				>{#if children}{@render children()}{/if}</Button
			>
		{/if}
	</span>
	<!-- The app's tooltip, never a `title`. -->
	<span class="half trail">
		{#if menu}
			<MenuButton
				label={trailingLabel}
				disabled={trailingDisabled || busy || rest.disabled === true}
			>
				{#snippet trigger({ props })}
					<Tooltip label={trailingLabel} placement="bottom">
						<Button
							{...props}
							{tone}
							{size}
							icon={trailingIcon}
							type="button"
							aria-label={trailingLabel}
							onmouseenter={ontrailingenter}
							onmouseleave={ontrailingleave}
						/>
					</Tooltip>
				{/snippet}
				{@render menu()}
			</MenuButton>
		{:else}
			<Tooltip label={trailingLabel} placement="bottom">
				<Button
					{tone}
					{size}
					icon={trailingIcon}
					type="button"
					disabled={trailingDisabled || busy || rest.disabled}
					aria-label={trailingLabel}
					onclick={ontrailing}
					onmouseenter={ontrailingenter}
					onmouseleave={ontrailingleave}
				/>
			</Tooltip>
		{/if}
	</span>
</div>

<style>
	.split {
		display: inline-flex;
		align-items: stretch;
		/* Never wider than its container; the lead half gives way (see below). */
		max-inline-size: 100%;
	}

	/* The halves themselves take no space of their own. They exist to be addressed. */
	.half {
		display: inline-flex;
	}

	/* Filling the width it is given: the main half takes the room, the chevron keeps its square. */
	.split.full {
		display: flex;
		inline-size: 100%;
	}

	.split.full .lead {
		flex: 1 1 auto;
	}

	.lead {
		flex: 0 1 auto;
		min-inline-size: 0;
	}

	.lead :global(.btn) {
		min-inline-size: 0;
		white-space: normal;
	}

	.trail {
		flex: 0 0 auto;
	}

	/* The join, two hooks deep so it beats the shared `.btn` radius whatever the stylesheet order. */
	.lead :global(.btn) {
		border-start-end-radius: 0;
		border-end-end-radius: 0;
	}

	.trail :global(.btn) {
		border-start-start-radius: 0;
		border-end-start-radius: 0;
		/* A faint seam mixed from the ink, so it holds against every tone. */
		border-inline-start: 1px solid color-mix(in oklab, currentColor 28%, transparent);
	}

	/* Lift the focused half, so its ring is not painted under the other. */
	.split :global(.btn:focus-visible) {
		position: relative;
		z-index: 1;
	}
</style>
