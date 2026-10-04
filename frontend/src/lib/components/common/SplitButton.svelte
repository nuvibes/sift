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
	 * the library's behaviour already, joined at the corner; the join is shape only.
	 *
	 * One button with a second, narrower action joined to its end: one control to look at and two
	 * to press, for a second action not important enough for its own button and not unimportant
	 * enough to be deep in a menu.
	 *
	 * Two real buttons, not one with a hit test: each is separately focusable, has its own name,
	 * gets Enter and Space, and is announced as its own thing to do. Splitting one button by where
	 * the pointer landed would be unreachable from a keyboard.
	 *
	 * In `common/` because more than one screen uses the shape (the Add button, and the Importing
	 * pane's "Generate now / tonight" row).
	 *
	 * It composes the shared Button, so tones, sizes, hover register, focus ring and disabled
	 * semantics are the shared one's. The only addition is the join: the two inner corners are
	 * flattened and a hairline is drawn between the halves, so the pair reads as one object.
	 *
	 * The trailing half as a door. Handed `menu`, the trailing half becomes the app's own
	 * `MenuButton`, drawn as the same shared Button so the join holds, with the caller's rows: a
	 * main act and a chevron dropping the rarer ones (a face card's "Add as person", with Ignore
	 * and Remove behind it).
	 *
	 * Both halves as doors. The main half takes `leadMenu` the same way; a file's own screen uses
	 * it, the main half opening the places a file can be put and the trailing half everything else.
	 * Two menus about one file are one control. The second door is the same `MenuButton`; only
	 * which corners are flattened and where the hairline sits differ.
	 */
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	import Button, { type ButtonSize, type ButtonTone } from '$lib/components/common/Button.svelte';
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import type { IconName } from '$lib/design/icons';

	/* `class` is deliberately not accepted. The join (the flattened inner corners and the hairline
	   between the halves) is what makes the pair one control, and a caller restyling either half
	   would be undoing the only thing this component adds. It is also what the shared Button's own
	   props say: it narrows `class` to a plain string where the site type allows anything. */
	interface Props extends Omit<HTMLButtonAttributes, 'class'> {
		/** How loud the pair is. Both halves take it, so they read as one control. */
		tone?: ButtonTone;
		size?: ButtonSize;
		/** The glyph on the main half, before its words. */
		icon?: IconName;
		/** The words on the main half. */
		children?: Snippet;
		/**
		 * Rows behind the MAIN half. Given, that half opens the app's menu on them instead of firing
		 * the caller's `onclick`; the words and the icon stay exactly what they were.
		 *
		 * `trailingLabel` names the trailing door; this one is named by the words on the button, so
		 * there is nothing extra to pass. A half with words and a menu behind it is already a
		 * complete sentence to a screen reader ("Add to, menu"), where the icon-only trailing
		 * half is not.
		 */
		leadMenu?: Snippet;
		/**
		 * Whether the main half's menu scrolls its own contents. See `MenuButton`'s `scrolls`.
		 *
		 * Rows want it and a composed thing that scrolls itself must not have it: a picker drawn as
		 * a whole menu is laid out at its full content height inside a second scroller, so the box
		 * it pins at one end lands past the foot of the list rather than at the edge of the menu.
		 * The two callers here are one of each: a file's "Add this file to" is rows, a face group's
		 * "Add as person" is the picker, which is why this is answered rather than assumed.
		 */
		leadMenuScrolls?: boolean;
		/**
		 * What the main half's menu is called, for anybody who cannot see it open.
		 *
		 * Required alongside `leadMenu` and enforced below rather than by the type: the words on the
		 * button are a snippet, so there is nothing here that could be read as a default, and a menu
		 * surface with no name is announced as "menu" and nothing else.
		 */
		leadMenuLabel?: string;
		/**
		 * The glyph on the trailing half. Icon-only by design: the whole point of the shape is that
		 * the second action costs a fraction of the width of the first. A chevron when the half is
		 * a door, which is the one glyph that means "more of these" everywhere.
		 */
		trailingIcon?: IconName;
		/**
		 * What the trailing half is called, for anyone who cannot see the glyph, and for the
		 * tooltip, which is the only thing that names it for anyone who can.
		 *
		 * Required rather than optional, because an icon-only control with no name is a button
		 * nobody can identify and there is no honest default for one.
		 */
		trailingLabel: string;
		/** The trailing half was pressed. Unused when the half is a door. See `menu`. */
		ontrailing?: () => void;
		/** The MAIN half alone cannot be pressed (its act is off for now) while the trailing half's
		   menu stays open to a press: a row whose act is Open on the open library still offers More. */
		leadDisabled?: boolean;
		/**
		 * Rows behind the trailing half. Given, the half opens the app's menu on them instead of
		 * firing `ontrailing`; `ContextMenuItem` and `VerbMenuItems` are what go in it, exactly as
		 * in every other menu. `trailingLabel` names the menu as well as the half.
		 */
		menu?: Snippet;
		/**
		 * The pointer arrived on, or left, the TRAILING half.
		 *
		 * Reported separately because the two halves are two actions, and a caller whose main half
		 * opens something on hover must not have that opened by a pointer on its way to the other
		 * one. `...rest` carries the caller's own hover handlers to the main half only, which is
		 * what makes that the default rather than something each caller has to remember.
		 */
		ontrailingenter?: () => void;
		ontrailingleave?: () => void;
		/** The trailing half alone is unavailable: its action cannot be done here, but the main
		 *  one still can. Separate from `disabled`, which takes the whole pair. */
		trailingDisabled?: boolean;
		/** Fills the width it is given: the main half grows, the chevron keeps its square. */
		full?: boolean;
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
		...rest
	}: Props = $props();

	/* A door with no name is announced as "menu" and nothing else. Inside an effect rather than at
	   the top of the component for the reason the same guard in `Chip` is: a prop read out here is
	   captured on the first render and never looked at again, so the check would pass for ever
	   after one good one. */
	$effect(() => {
		if (leadMenu !== undefined && !leadMenuLabel) {
			throw new Error('A main half that opens a menu has to name it. See `leadMenuLabel`.');
		}
	});
</script>

<!-- A group, because it is one control made of two: the pair is announced together and the two
     halves are read as parts of it rather than as unrelated neighbours. -->
<div class="split" class:full role="group">
	<!-- Everything the caller passes goes to the main half: it is the button, the trailing one an
	     accessory, and an `aria-expanded` or a `type` belongs on the half it describes.

	     Except where that half is a door: the library supplies its press and its `aria-expanded`,
	     so a caller's spread there would fight the trigger wiring. `disabled` still crosses,
	     because it is a fact about the control, not the press.

	     Each half is wrapped in an element of this component's own, because the join has to be
	     addressed and the halves are not siblings: the tooltip below wraps the trailing one, so
	     `:first-child` and `:last-child` would both match it and flatten all four corners. A hook
	     per half says which is which. -->
	<span class="half lead">
		{#if leadMenu}
			<!-- The same door the trailing half opens, on the half that carries the words. The caller's
			     own `onclick` is deliberately not wired here: a half that both acts and opens is one
			     press doing two things, which is the rule the trailing half already follows. -->
			<MenuButton
				label={leadMenuLabel ?? ''}
				disabled={leadDisabled || rest.disabled === true}
				scrolls={leadMenuScrolls}
			>
				{#snippet trigger({ props })}
					<Button {...props} {tone} {size} {icon} {full} type="button"
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
				{...rest}
				disabled={leadDisabled || rest.disabled === true}
				>{#if children}{@render children()}{/if}</Button
			>
		{/if}
	</span>
	<!-- The application's own tooltip rather than the site `title` attribute. A `title` is drawn
	     by the operating system in its own typeface after a delay nothing here controls, which on a
	     control inside the app chrome reads as a piece of a different program. -->
	<span class="half trail">
		{#if menu}
			<!-- The same door every menu in the app opens through, with this half as its trigger.
			     Named by a tooltip like the plain half: a glyph alone always is. -->
			<MenuButton label={trailingLabel} disabled={trailingDisabled || rest.disabled === true}>
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
					disabled={trailingDisabled || rest.disabled}
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
		/*
		 * It can never be wider than what it is in. Both halves are the shared Button,
		 * `white-space: nowrap` and `inline-size: fit-content`, sized by their words and unable to
		 * give width back. In a card narrower than the words the pair would draw past the edge, and
		 * a row that ends its children (`justify-content: flex-end`) puts that overflow on the
		 * leading side, over the gutter and the next card. So the control is capped at its
		 * container and the lead half gives way (see the two rules below). With room, nothing
		 * changes: a flex item only shrinks when there is not enough.
		 */
		max-inline-size: 100%;
	}

	/* The halves themselves take no space of their own. They exist to be addressed. */
	.half {
		display: inline-flex;
	}

	/*
	 * Which half gives way when there is not enough room: the one with the words, never the
	 * chevron.
	 *
	 * `min-inline-size: 0` lets a flex item go under its content width at all; the default floor is
	 * the content, which is why a nowrap button cannot be squeezed. The words wrap rather than
	 * being clipped: a half out of room grows a second line and stays whole, where `overflow:
	 * hidden` would cut a word in half. The button's height is a minimum (`min-block-size`), so it
	 * grows to hold them.
	 *
	 * The trailing half is fixed: an icon-only square the size of the control height, the only way
	 * to the rest of the acts, and a clipped chevron is the failure to avoid.
	 */
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

	/*
	 * The join.
	 *
	 * `:global` on the inner part because `.btn` is the shared Button's own class and a scoped rule
	 * written here would never reach it. Anchored on this component's own `.lead` / `.trail`, so it
	 * can only ever reach the two halves of a split button and never a button somewhere else.
	 *
	 * Two hooks deep, which beats the shared `.btn` radius outright rather than depending on which
	 * stylesheet the bundler happened to write first: an override at the same specificity as the
	 * rule it overrides is a coin toss, and one that lands right in development.
	 */
	.lead :global(.btn) {
		border-start-end-radius: 0;
		border-end-end-radius: 0;
	}

	.trail :global(.btn) {
		border-start-start-radius: 0;
		border-end-start-radius: 0;
		/* The hairline. Mixed from the ink rather than from a fixed line colour, so it holds against
		   every tone: a primary is light ink on a saturated ground and a ghost is the page's own,
		   and one grey would disappear into one of them. Faint, because it is a seam and not a
		   border: at full strength the pair reads as two controls that happen to be touching. */
		border-inline-start: 1px solid color-mix(in oklab, currentColor 28%, transparent);
	}

	/* The focus ring is painted outside the button's own box, and the halves are touching, so the
	   ring on one is drawn UNDER the other unless the focused half is lifted. Nothing moves; only
	   the paint order changes. */
	.split :global(.btn:focus-visible) {
		position: relative;
		z-index: 1;
	}
</style>
