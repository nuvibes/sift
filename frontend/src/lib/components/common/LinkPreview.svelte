<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'LinkPreview',
		category: 'composition',
		role: 'a card about the thing under the pointer, shown while a link is hovered',
		basis: 'bits-ui:LinkPreview',
		states: ['closed', 'open']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * What is on the other end of a link, without going there.
	 *
	 * A name in a list is a name. A face beside it, with what that person actually HAS (files,
	 * Sites, collections) is enough to decide whether to open them at all, and deciding
	 * without navigating is the whole point: the alternative is open, look, come back, and lose
	 * whatever was on screen.
	 *
	 * ## It is not a Tooltip, and the difference is what may be in it
	 *
	 * A tooltip LABELS a control that does not carry its own words, and nothing in one may be
	 * operated: it is not focusable and it vanishes the moment the pointer leaves. This is the
	 * other thing: a card that can be walked into. The library keeps it open while the pointer is
	 * travelling towards it and while anything inside it has focus, so the links in it are real
	 * links that can be clicked and tabbed to. That behaviour is the entire reason this is the
	 * library's and not another hand-built layer: the hover delay, the safe travel, the escape and
	 * the focus handling are the hard half, and every hand-rolled version of them gets one wrong.
	 *
	 * A card is therefore never the ONLY way to something. Everything it offers is reachable by
	 * following the link itself, because a pointer is not something everybody has.
	 *
	 * ## The surface is the app's floating surface
	 *
	 * The same ground, corner, shadow and layer as every menu (`--sift-surface-3`, `--radius-lg`,
	 * `--elev-3`, `--z-menu`). A second floating look would be a second answer to "what does a thing
	 * drawn over the page look like".
	 *
	 * ## Portalled, with the same fullscreen door the menus use
	 *
	 * Out of every `overflow: hidden` and every stacking context between it and its trigger, and,
	 * when a screen is FILLING the window, into that screen instead. A browser filling the screen
	 * paints the fullscreened element and its subtree and nothing else in the document, so a card at
	 * the end of `body` is not hidden, it is not drawn at all.
	 *
	 * `document.fullscreenElement` is asked at the moment of opening rather than tracked, and the
	 * component is deliberately not told which of the app's screens can be filled: that is what makes
	 * it a shared primitive rather than one that knows about Theater. The same answer `Tooltip`
	 * reached, for the same reason.
	 */
	import type { Snippet } from 'svelte';
	import { LinkPreview } from 'bits-ui';
	import { surface } from '$lib/shell/motion.svelte';

	interface Props {
		/**
		 * The thing being hovered, given the attributes that make it the trigger.
		 *
		 * A snippet handed `props` rather than a wrapper around whatever was passed in, because the
		 * trigger is nearly always an anchor the caller has already written, with its own class,
		 * its own href and its own contents. Wrapping it would put a box in the middle of somebody
		 * else's layout; spreading onto it leaves the markup exactly as it was.
		 */
		trigger: Snippet<[{ props: Record<string, unknown> }]>;
		/** What is drawn in the card. Links and text; it is walk-into-able, so controls are fine. */
		card: Snippet;
		/**
		 * How long a pointer has to rest before the card opens: 350ms.
		 *
		 * Slower than `Tooltip`'s 180ms, because a tooltip only labels what is on screen and this
		 * card asks the server a question, but not so slow that a pointer resting on a name reads
		 * as nothing happening. Written here rather than borrowed from that file: they are two
		 * different judgements about two different things.
		 */
		openDelay?: number;
		/** How long it stays after the pointer leaves, so it can be walked into. */
		closeDelay?: number;
		/** Which side of the trigger to sit on, when there is room. */
		side?: 'top' | 'bottom' | 'left' | 'right';
		/** Told when it opens, for a caller that fetches what the card shows. */
		onOpenChange?: (open: boolean) => void;
	}

	let {
		trigger,
		card,
		openDelay = 350,
		closeDelay = 300,
		side = 'top',
		onOpenChange
	}: Props = $props();

	let open = $state(false);

	/*
	 * Where the card is drawn: the end of the document, unless a screen is filling the window.
	 *
	 * SAMPLED AT THE MOMENT IT OPENS, and held, not derived off `open`: the card is force-mounted
	 * so it can be seen leaving. A derived would be answering the question continuously, for a
	 * portal that is there the whole time, and it answers it once during teardown when the effect
	 * that owns it is already going away.
	 *
	 * Not cleared on close, deliberately: a card on its way out is still on screen, and moving it to
	 * the other end of the document mid-movement would make it vanish rather than fade.
	 */
	let door = $state<Element | undefined>(undefined);

	function changed(next: boolean): void {
		if (next && typeof document !== 'undefined') door = document.fullscreenElement ?? undefined;
		open = next;
		onOpenChange?.(next);
	}
</script>

<LinkPreview.Root {open} onOpenChange={changed} {openDelay} {closeDelay}>
	<LinkPreview.Trigger>
		{#snippet child({ props })}
			{@render trigger({ props })}
		{/snippet}
	</LinkPreview.Trigger>

	<LinkPreview.Portal to={door}>
		<!--
			`forceMount`, so the card can be seen LEAVING.

			Without it the library takes the element out of the document the instant the pointer
			leaves, and no script can animate something that is already gone. Force-mounted, the
			library hands the open state to the snippet and the `{#if}` below is ours, which is what
			puts the card under Svelte's own transition machinery, and a transition is the one
			mechanism that holds an element on the page long enough to play its way out.

			It costs nothing while the card is shut: the branch renders no element at all, so there is
			no invisible box sitting over the page waiting to be hovered.
		-->
		<LinkPreview.Content {side} sideOffset={8} forceMount>
			<!-- The `child` snippet, so this file's scoped rules reach the card. Rendered by the
			     library it would be a stranger's element and every rule below would match nothing,
			     which is the failure mode that looks like the component simply has no styling. The
			     wrapper is Floating UI's and is never styled here; the card inside it is ours. -->
			{#snippet child({ props, wrapperProps, open: shown })}
				{#if shown}
					<div {...wrapperProps}>
						<!--
							It opens as every small surface does: it rises four pixels as it arrives and fades
							as it goes.

							`surface` is the application's own transition and it is used rather than a pair
							of keyframes written here: it takes its duration from the motion setting, it plays
							in both directions from one declaration, and under reduced motion it drops the
							travel and keeps the fade, which is the rule every other movement in Sift
							follows. A keyframes rule cannot animate anything leaving.
						-->
						<div {...props} class="preview" transition:surface>
							{@render card()}
						</div>
					</div>
				{/if}
			{/snippet}
		</LinkPreview.Content>
	</LinkPreview.Portal>
</LinkPreview.Root>

<style>
	/* The app's one floating surface, said again rather than borrowed: `.ui-menu` is a MENU's class
	   and carries a menu's width floor and a menu's row grid with it. What is shared is the four
	   tokens, which is where the decision actually lives. */
	.preview {
		z-index: var(--z-menu);
		/* Wide enough for a name and a row of counts, measured in characters so it follows the text
		   rather than a guess about pixels. */
		max-inline-size: 44ch;
		padding: var(--space-3);
		border-radius: var(--radius-lg);
		background: var(--sift-surface-3);
		box-shadow: var(--elev-3);
		color: var(--sift-ink);
		/*
		 * IT ARRIVES, AND IT LEAVES, and both are declared on the element above rather than here.
		 *
		 * Not a CSS animation such as the app's shared `appear` fade: an animation runs when an
		 * element is drawn and there is nothing left to run it on when the element is removed, so
		 * the card would appear politely and then simply stop existing.
		 *
		 * `surface` covers both from one declaration and carries the reduced-motion rule with it, so
		 * neither is written twice. See the element.
		 */
	}
</style>
