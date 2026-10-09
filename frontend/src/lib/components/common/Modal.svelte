<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Modal',
		category: 'surface',
		role: 'the one dialog: veil, trapped focus, Escape closes or steps back, focus returns',
		basis: 'bits-ui:Dialog; bits-ui:AlertDialog',
		states: ['dialog', 'alert']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* The app's one dialog. `insistent` takes the alert role; both flavours close on a click
	   outside, since no dialog's dismissing half is destructive. forceMount, to be seen leaving. */
	import type { Snippet } from 'svelte';
	import { AlertDialog, Dialog } from 'bits-ui';
	import { arrive, veil } from '$lib/shell/motion.svelte';
	import Scroller from './Scroller.svelte';
	import { givePressSize } from './press-size';

	/* A dialog's presses are the default size, whatever the row that opened it gave. */
	givePressSize(undefined);

	/* Both flavours' answer controls share one props type, so either pair can be handed down. */
	type Answer = typeof Dialog.Close;

	interface Props {
		open?: boolean;
		/** What the sheet is, in a heading. Named, never "Are you sure?". */
		title: string;
		/** The sentence under the heading: what this is about or what is about to happen. */
		description?: string | Snippet;
		/** Anything the sheet's own stylesheet needs to reach it by, beside the shared `sheet`. */
		sheetClass?: string;
		/** Anything the description needs beyond the shared one, for a sheet that dresses it. */
		descriptionClass?: string;
		/** The alert role: a destructive question or a credential box. */
		insistent?: boolean;
		/** The element the sheet is drawn on, for a caller that has to animate it. */
		sheet?: HTMLElement | null;
		/** The body is the one scrolling region; off for a body that scrolls its own regions. */
		scrolls?: boolean;
		/** Fill the screen for a picture: no card, a heading for assistive technology only. */
		bleed?: boolean;
		/** The buttons at the foot, kept out of the scroll. */
		footer?: Snippet<[{ Cancel: Answer; Act: Answer }]>;
		onOpenChange?: (open: boolean) => void;
		/** Escape calls this instead of closing, to pop a step; pass the step's own Back. */
		onback?: () => void;
		/** Where focus lands on open; a plain `autofocus` loses to the library's. */
		onOpenAutoFocus?: (event: Event) => void;
		/** The contents, handed the two buttons wired into the dialog's own dismiss. */
		children: Snippet<[{ Cancel: Answer; Act: Answer }]>;
		/** Where the sheet is drawn: the filled box while a screen fills the window, as `ContextMenu`. */
		portalTo?: Element | null;
	}

	let {
		open = $bindable(false),
		title,
		description,
		sheetClass,
		descriptionClass,
		insistent = false,
		bleed = false,
		scrolls = !bleed,
		sheet = $bindable(null),
		footer,
		onOpenChange,
		onback,
		onOpenAutoFocus,
		children,
		portalTo
	}: Props = $props();

	/* The two flavours resolved to one set of parts, so the markup is written once. */
	const Parts = $derived(insistent ? AlertDialog : Dialog);

	/* A plain sheet's buttons are the library's close control, which closes and nothing else. */
	const Cancel: Answer = $derived(insistent ? AlertDialog.Cancel : Dialog.Close);
	const Act: Answer = $derived(insistent ? AlertDialog.Action : Dialog.Close);

	/* Escape is prevented so the sheet stays up while `onback` pops a step. */
	function escaped(event: KeyboardEvent) {
		if (!onback) return;
		event.preventDefault();
		onback();
	}

	/** Whether the sentence was written as markup rather than passed as a string. */
	const written = $derived(typeof description === 'function');
</script>

<Parts.Root bind:open {onOpenChange}>
	<Parts.Portal to={portalTo ?? undefined}>
		<Parts.Overlay forceMount>
			{#snippet child({ props, open: showing })}
				{#if showing}<div {...props} class="veil" transition:veil></div>{/if}
			{/snippet}
		</Parts.Overlay>

		<Parts.Content
			forceMount
			interactOutsideBehavior="close"
			onEscapeKeydown={escaped}
			{onOpenAutoFocus}
		>
			{#snippet child({ props, open: showing })}
				{#if showing}
					<div
						{...props}
						bind:this={sheet}
						class="sheet {sheetClass ?? ''}"
						class:bleed
						transition:arrive={{ pace: 'base', scale: 0.96 }}
					>
						<Parts.Title class="title">{title}</Parts.Title>
						{#if description !== undefined}
							<Parts.Description class="consequence {descriptionClass ?? ''}">
								{#if written}
									{@render (description as Snippet)()}
								{:else}
									{description}
								{/if}
							</Parts.Description>
						{/if}

						<!--
						The only scrolling region: a grid row, because a scroller must be told its
						height.
						-->

						{#if scrolls}
							<div class="sheet-scroll">
								<Scroller viewportClass="sheet-scroll-view">
									{@render children({ Cancel, Act })}
								</Scroller>
							</div>
						{:else}
							{@render children({ Cancel, Act })}
						{/if}

						{#if footer}
							<div class="sheet-foot">{@render footer({ Cancel, Act })}</div>
						{/if}
					</div>
				{/if}
			{/snippet}
		</Parts.Content>
	</Parts.Portal>
</Parts.Root>

<style>
	/* A long unbroken name wraps anywhere, uncut, in every sheet's heading and sentence. */
	.sheet > :global(.title),
	.sheet > :global(.consequence) {
		overflow-wrap: anywhere;
	}
</style>
