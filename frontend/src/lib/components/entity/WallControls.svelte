<script lang="ts">
	/*
	 * The two controls above every entity wall: a NarrowBox filtering as you type, and Add, a
	 * press navigating to a literal address the reachability gate can read.
	 * NOT ON THE GALLERY: it is two primitives the gallery already draws, a `NarrowBox` at its
	 * page-header size and a `Button`, arranged into one row.
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

	/* The pause before `onsettled`: one request per name, still following the typing. */
	const SETTLE_MS = 180;
	let settling: ReturnType<typeof setTimeout> | null = null;

	function typed() {
		oninput?.();
		if (!onsettled) return;
		if (settling) clearTimeout(settling);
		const words = term;
		/* Only while the box still holds what was typed. */
		settling = setTimeout(() => {
			if (term === words) onsettled?.(words.trim());
		}, SETTLE_MS);
	}

	// A wall left mid-word is not asked anything afterwards.
	$effect(() => () => {
		if (settling) clearTimeout(settling);
	});

	/* This box's words are this wall's, never the top search box's (`claimOwnBox`). */
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

	/* How narrow the box may get before tabs wrap. */
	.wall-controls :global(.find) {
		inline-size: auto;
		min-inline-size: var(--tab-box-floor);
	}

	/* On a phone the box takes what Add leaves; Add keeps its word. */
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
