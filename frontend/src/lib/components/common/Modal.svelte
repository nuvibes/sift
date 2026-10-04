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
	/*
	 * The one dialog in the app. Every sheet that dims the page behind it is this component.
	 *
	 * It owns the part nobody should write twice: the portal out of the markup, the veil, the
	 * layering, the transition in both directions, and the heading and sentence the sheet opens
	 * with.
	 *
	 * Two flavours, chosen with `insistent`. A plain sheet asks for something and goes away if you
	 * click beside it. An insistent one takes the role that says it interrupted on purpose, for a
	 * destructive question or a credential box. One component, so the veil, layering and transition
	 * live in one place.
	 *
	 * Both are dismissed by a click outside, which is not the library's default for the insistent
	 * one: no dialog here has a destructive action on the dismissing half of its question, so
	 * clicking away can only decline, and a box that ignores that gesture reads as stuck.
	 *
	 * `forceMount` with a snippet at each layer is the only way a dialog can be seen leaving;
	 * otherwise the library removes it the instant it closes and the exit has nothing to animate.
	 */
	import type { Snippet } from 'svelte';
	import { AlertDialog, Dialog } from 'bits-ui';
	import { arrive, veil } from '$lib/shell/motion.svelte';
	import Scroller from './Scroller.svelte';
	import { givePressSize } from './press-size';

	/* A dialog is a surface of its own: its presses are the default size even when the row that
	   opened it gave the small one to everything it holds. */
	givePressSize(undefined);

	/* Decline and agree. The library gives both flavours the same shape for these: an alert
	   dialog's Cancel and Action and a plain one's Close are one props type, which is what lets
	   one wrapper hand either pair down without the caller knowing which it got. */
	type Answer = typeof Dialog.Close;

	interface Props {
		open?: boolean;
		/** What the sheet is, in a heading. Named, never "Are you sure?". */
		title: string;
		/**
		 * The sentence under the heading, saying what this is about or what is about to happen.
		 *
		 * A string for the usual case; a snippet where the sentence carries markup. It is not
		 * optional by accident on the sheets that pass one: a dialog with no plain statement of
		 * what it will do is a dialog people learn to click through.
		 */
		description?: string | Snippet;
		/** Anything the sheet's own stylesheet needs to reach it by, beside the shared `sheet`. */
		sheetClass?: string;
		/** Anything the description needs beyond the shared one, for a sheet that dresses it. */
		descriptionClass?: string;
		/**
		 * Whether this interrupted on purpose.
		 *
		 * On means the alert role: a destructive question, or a box asking for a credential. Off is
		 * an ordinary sheet. It changes what assistive technology is told, so it follows what the
		 * dialog IS rather than how important it feels.
		 */
		insistent?: boolean;
		/** The element the sheet is drawn on, for a caller that has to animate it. */
		sheet?: HTMLElement | null;
		/**
		 * Whether this sheet's contents are the thing that scrolls.
		 *
		 * On, and the body between the heading and the footer is one scrolling region, through the
		 * shared scroller, with the heading and the buttons staying put. The default, because the
		 * wrong answer is invisible: a sheet with no cap and no overflow does not scroll, it grows,
		 * and a tall one runs off the top and bottom of the screen with no way to reach either.
		 *
		 * Off is for a sheet whose body is a layout that scrolls in its own right: the folder
		 * browser is three columns scrolling separately, and folding them into one would be a
		 * different screen. Such a sheet still gets the cap (a flex column with a bounded height,
		 * so its own regions are bounded too) and keeps its own regions.
		 */
		scrolls?: boolean;
		/**
		 * The sheet fills the screen, with nothing else on it.
		 *
		 * For one thing being looked at rather than something being asked: a picture, at the size
		 * of the window. It is a flavour of this component rather than a component of its own
		 * because everything under the surface is identical: the portal, the focus trap, Escape,
		 * the return of focus, the rest of the page being hidden from a screen reader. A hand-built
		 * overlay looks the same and has none of that, and this file exists so that nobody
		 * hand-builds one.
		 *
		 * Two things change: the sheet has no card around it, and the heading is present for
		 * assistive technology and drawn nowhere, since a full-bleed picture has no corner to put a
		 * title in that would not be chrome over the thing being looked at.
		 *
		 * The veil is the ordinary one, tint and blur and all, not an opaque ground: a picture
		 * opened closes and leaves you where you were, so it wants the same ground every other
		 * sheet has. See the note beside the veil in `app.css`.
		 *
		 * The body does not scroll by default here. What fills the screen is fitted to it.
		 */
		bleed?: boolean;
		/**
		 * The row of buttons at the foot, kept out of the scroll.
		 *
		 * Passed here rather than written at the end of `children` so that it stays put while the
		 * body moves. A confirm button that scrolls away is a confirm button somebody has to go
		 * looking for, on the one screen where what to press next is the whole question.
		 */
		footer?: Snippet<[{ Cancel: Answer; Act: Answer }]>;
		onOpenChange?: (open: boolean) => void;
		/**
		 * The way back, for a sheet that is a stack and is showing a step on top of another.
		 *
		 * Given, and Escape calls this INSTEAD of closing: the sheet stays up and the caller takes
		 * its top step off. Left out (which is what a sheet on its first step passes, by handing
		 * `undefined` there) and Escape closes the sheet, through the library,
		 * so the focus return and `onOpenChange` still happen. The caller says which by what it
		 * passes rather than by answering a question at keypress time, so a sheet cannot pop a step
		 * it is not showing.
		 *
		 * It is the same move a sheet's own Back or Cancel makes on that step, and the caller
		 * should pass that very function: Escape and the button are two ways of saying one thing,
		 * and must not disagree (Cancel returning to the list while Escape throws away the whole
		 * sheet).
		 *
		 * Only Escape. A click on the veil still closes the sheet from any step: it is a gesture at
		 * the page behind, not at the step in front.
		 */
		onback?: () => void;
		/**
		 * Where focus should land, for a sheet that would rather it went somewhere in particular.
		 *
		 * The library moves focus to the sheet itself on open (that is what makes Escape and Tab
		 * work from the first render) and that lands after a plain `autofocus`, so the native
		 * attribute never wins on its own.
		 */
		onOpenAutoFocus?: (event: Event) => void;
		/**
		 * The sheet's contents, handed the two buttons that answer an insistent question.
		 *
		 * `Cancel` declines and `Act` agrees, and they are passed down rather than imported because
		 * they are wired into the dialog's own focus and dismiss behaviour: a plain button in
		 * their place looks identical and is not connected to anything. A plain sheet's buttons are
		 * its own ordinary buttons, so it simply does not take these.
		 */
		children: Snippet<[{ Cancel: Answer; Act: Answer }]>;
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
		children
	}: Props = $props();

	/* The two flavours resolved to one set of parts. Everything below is written once against
	   these, so the markup does not exist twice with two prefixes. */
	const Parts = $derived(insistent ? AlertDialog : Dialog);

	/* What the buttons of an insistent question are. A plain sheet has no equivalent: its buttons
	   close it themselves, so it is handed the library's close control for both, which does
	   exactly that and nothing else. */
	const Cancel: Answer = $derived(insistent ? AlertDialog.Cancel : Dialog.Close);
	const Act: Answer = $derived(insistent ? AlertDialog.Action : Dialog.Close);

	/* Escape, answered here when the sheet has a step to go back to. The library closes the sheet
	   after this unless the event was prevented, so preventing it is what keeps the sheet up; with
	   no `onback` the library's own handler runs untouched. */
	function escaped(event: KeyboardEvent) {
		if (!onback) return;
		event.preventDefault();
		onback();
	}

	/** Whether the sentence was written as markup rather than passed as a string. */
	const written = $derived(typeof description === 'function');
</script>

<Parts.Root bind:open {onOpenChange}>
	<Parts.Portal>
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
							The body, and the only thing on a sheet that scrolls.

							A grid of one `minmax(0, 1fr)` row rather than a plain box: a scrolling region
							has to be TOLD how tall it is, and a `max-block-size` on its own does not
							make anything scroll: the region lays out at its full content height and
							paints straight out of the box, which is identical in a stylesheet and is a
							list running off the bottom of a sheet on screen.
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
	/*
	 * THE HEADING AND THE SENTENCE UNDER IT BREAK A LONG NAME ANYWHERE, ON EVERY SHEET.
	 *
	 * Either can carry a name: a file's, a folder's, a username. A name is often one unbroken token
	 * (`3840x2560_0123abcd...jpg`, a hash, an address) and a line with no space in it has nowhere
	 * to wrap, so it would paint straight out through the sheet's right edge.
	 *
	 * It lives HERE, in the one component that draws both, and it is not an option: a rule each
	 * sheet writes for itself on the class it hands to `descriptionClass` is a fix a caller has to
	 * remember, and the next sheet forgets it. `anywhere` rather than `break-word` because it also
	 * lowers the element's narrowest size, so a sheet laid out by its content cannot be pushed wider
	 * by the name either; it changes nothing for a sentence whose words fit.
	 *
	 * The full name, wrapped, and no line cap. Three lines of this sheet hold about 150 characters
	 * and a name may run to 255, so a three-line cap would cut the longest names with nothing
	 * anywhere saying what was cut, and a cut with no tooltip is what the design rule on long
	 * names forbids. A heading a few lines taller on a rare name is honest; a missing tail is not.
	 *
	 * Direct children of the sheet: the heading and the sentence are the sheet's own, so a `.title`
	 * somebody draws inside the body is not reached.
	 */
	.sheet > :global(.title),
	.sheet > :global(.consequence) {
		overflow-wrap: anywhere;
	}
</style>
