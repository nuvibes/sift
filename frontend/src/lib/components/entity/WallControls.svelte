<script lang="ts">
	/*
	 * The two controls every entity wall has above it: a box that filters the wall, and the way to
	 * add one.
	 *
	 * NOT ON THE GALLERY: it is two primitives the gallery already draws, a `NarrowBox` at its
	 * page-header size and a `Button`, arranged into one row.
	 *
	 * One component rather than five copies, so the same act looks the same on every wall and each
	 * wall says only what it is a wall of.
	 *
	 * The box has no button beside it. Typing filters, and the wall visibly filters as the letters
	 * land, so a Find button would only be a second way to run the same search, taking the room the
	 * Add needs. The box is `NarrowBox`, the one box in Sift that filters a list as you type, with
	 * its own cross and Escape, since a hand on the mouse wants the cross; the walls get exactly
	 * what Settings, Downloads and the pickers have.
	 *
	 * Add is a button that goes somewhere. Sift's buttons are never links (see `Button`), so this
	 * is a press that navigates, to an address the caller writes as a literal on the wall: that is
	 * what the reachability gate reads, and a path assembled here from a noun would be a screen
	 * nothing in the client names.
	 */
	import { Button, NarrowBox } from '$lib/components/common';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { boxWords } from './wall-box';

	interface Props {
		/** The singular, for the Add: "site" gives "Add site". */
		noun: string;
		/** The plural, for the box: "sites" gives "Search sites". */
		plural: string;
		/** What is typed in the box. Bound, so the wall owns the term and this owns the shape. */
		term: string;
		/** Told on every keystroke. The wall decides how long to wait before asking the server. */
		oninput?: () => void;
		/** Told once typing pauses, with the words trimmed: the moment a wall asks the server. */
		onsettled?: (typed: string) => void;
		/** Where Add goes. Absent draws no Add at all, which is what a guest sees. */
		onadd?: () => void;
		/** How long a name may be. The server's own limit, so the box refuses what it would. */
		maxlength?: number;
	}

	let {
		noun,
		plural,
		term = $bindable(),
		oninput,
		onsettled,
		onadd,
		maxlength = 120
	}: Props = $props();

	/* How long typing pauses before `onsettled`: long enough that typing a name is one request
	   rather than one per letter, short enough that the wall follows the typing. The one copy: every
	   wall hands its settle here rather than keeping a timer of its own. */
	const SETTLE_MS = 180;
	let settling: ReturnType<typeof setTimeout> | null = null;

	function typed() {
		oninput?.();
		if (!onsettled) return;
		if (settling) clearTimeout(settling);
		const words = term;
		/* Only while the box still says what was typed. Words that reached the wall some other way
		   in the pause (the chip's cross, Back, a link) are in the box now, and the typing they
		   replaced is not written over them. */
		settling = setTimeout(() => {
			if (term === words) onsettled?.(words.trim());
		}, SETTLE_MS);
	}

	// A wall left mid-word is not asked anything afterwards.
	$effect(() => () => {
		if (settling) clearTimeout(settling);
	});

	/* This box's words are this wall's. While it is drawn the top search box reads nothing from the
	   address, so what is typed here never appears there. See `screenBar.claimOwnBox`. */
	const mine = Symbol('wall-box');
	$effect(() => screenBar.claimOwnBox(mine));

	/* A box squeezed by a row of tabs shows its noun, which says more than a verb cut short. */
	let row = $state<HTMLElement | null>(null);
	let room = $state(0);
	let widthOf = $state((text: string) => text.length);
	const placeholder = $derived(boxWords(plural, room, widthOf));
	$effect(() => {
		const box = row?.querySelector('input');
		if (!box || typeof ResizeObserver === 'undefined') return;
		let pen: CanvasRenderingContext2D | null = null;
		const measure = () => {
			if (box.clientWidth === 0) return;
			pen ??= document.createElement('canvas').getContext('2d');
			if (!pen) return;
			const style = getComputedStyle(box);
			const font = style.font;
			const held = pen;
			widthOf = (text) => {
				held.font = font;
				return held.measureText(text).width;
			};
			room =
				box.clientWidth - parseFloat(style.paddingInlineStart) - parseFloat(style.paddingInlineEnd);
		};
		measure();
		const observer = new ResizeObserver(measure);
		observer.observe(box);
		return () => observer.disconnect();
	});
</script>

<!-- The box on the left and the Add on the right: an action sits on the right of a row. -->
<div class="wall-controls" bind:this={row}>
	<!-- The page-header height, so the box and the Add beside it stand on one line. -->
	<NarrowBox
		class="find"
		size="medium"
		label="Search {plural}"
		{placeholder}
		bind:value={term}
		oninput={typed}
		{maxlength}
	/>
	{#if onadd}
		<Button tone="primary" icon="add" onclick={onadd}>Add {noun}</Button>
	{/if}
</div>

<style>
	.wall-controls {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* How narrow the box may get, and nothing else of it, which is `NarrowBox`'s. The floor is
	   reached only on a short row, where the box gives way before a row of tabs wraps. */
	.wall-controls :global(.find) {
		inline-size: auto;
		min-inline-size: var(--tab-box-floor);
	}

	/* At a phone's width the row is the header's whole second line: the box takes whatever the Add
	   leaves, and the Add keeps its word, since a plus alone does not say what it adds. */
	@media (max-width: 767px) {
		.wall-controls {
			flex: 1 1 100%;
			min-inline-size: 0;
		}

		.wall-controls :global(.find) {
			flex: 1 1 0%;
			min-inline-size: 0;
		}

		.wall-controls > :global(button) {
			flex: none;
		}
	}
</style>
