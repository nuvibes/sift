<script module lang="ts">
	import { pageScroll } from '$lib/components/shell/page-scroll';
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Pager',
		category: 'composition',
		role: 'the way through a paged wall: previous, next, and where you are',
		basis: 'own',
		states: ['first page', 'middle', 'last page', 'numbered']
	} satisfies DesignEntry;

	/**
	 * Everything a pager needs, as one shape a panel reports through `onpaging` to the frame's
	 * foot.
	 */
	export interface PagerProps {
		/** Where the page starts, counting from zero. */
		offset: number;
		/** How many rows this page is actually showing. */
		shown: number;
		/** How many there are in all. */
		total: number;
		/** What the rows are, plural ("files"), said after the total. */
		noun?: string;
		/** The same word for one row, where dropping the plural's last "s" would not make it. */
		one?: string;
		/** The first page is coming: the readout says nothing, not "No files". */
		loading?: boolean;
		onfirst: () => void;
		onprevious: () => void;
		onnext: () => void;
		onlast: () => void;
		/** Take me to the nth one, counting from one the way the readout does. */
		onjump: (position: number) => void;
		/**
		 * A fixed page size, for numbered pages (the queue, History); a fitted wall keeps
		 * positions.
		 */
		perPage?: number;
	}

	/** One press of a numbered pager: a page, or a gap standing for the pages between two. */
	export type PageMark = { page: number } | { gap: string };

	/** First, last, current and either side, with gaps; a gap of one page is that page. */
	export function pageMarks(current: number, pages: number): PageMark[] {
		if (pages <= 0) return [];
		const wanted = new Set([1, pages, current - 1, current, current + 1]);
		const kept = [...wanted].filter((page) => page >= 1 && page <= pages).sort((a, b) => a - b);
		const marks: PageMark[] = [];
		let before = 0;
		for (const page of kept) {
			if (page - before === 2) marks.push({ page: page - 1 });
			else if (page - before > 2) marks.push({ gap: `gap-${before}` });
			marks.push({ page });
			before = page;
		}
		return marks;
	}

	/** How a panel reports its pager to the frame that draws it. `null` once it has nothing to page. */
	export type OnPaging = (pager: PagerProps | null) => void;
</script>

<script lang="ts">
	/*
	 * WHY NOT BITS-UI: bits-ui's Pagination is built around page numbers, and a wall's pager
	 * deliberately has none: a page holds what fits the window, so it says positions ("1,240-1,287
	 * of 9,000") and the jump box takes one. The numbered variant (`perPage`) is the same bar.
	 */
	import Button from '$lib/components/common/Button.svelte';
	import NumberInput from '$lib/components/common/NumberInput.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';

	type Props = PagerProps;

	let {
		offset,
		shown,
		total,
		noun = 'files',
		one,
		loading = false,
		onfirst,
		onprevious,
		onnext,
		onlast,
		onjump,
		perPage
	}: Props = $props();

	/* The numbered variant: which page this is, how many there are, and the numbers to draw. */
	const page = $derived(perPage ? Math.floor(offset / perPage) + 1 : 0);
	const pages = $derived(perPage ? Math.max(1, Math.ceil(total / perPage)) : 0);
	const marks = $derived(perPage ? pageMarks(page, pages) : []);

	/** Page `wanted`, as the position its first row stands at. */
	function toPage(wanted: number): void {
		if (!perPage) return;
		onjump((Math.min(Math.max(1, wanted), pages) - 1) * perPage + 1);
		pageScroll.toTop();
	}

	/* Every turn lands at the top of the new page; `pageScroll` knows which box scrolls. */
	function turning(turn: () => void): () => void {
		return () => {
			turn();
			pageScroll.toTop();
		};
	}

	/* Grouped thousands: a number read, not computed. */
	const count = (value: number) => value.toLocaleString();

	/* "of 1 file"; an irregular plural names its singular through `one`. */
	const said = $derived(
		total !== 1 ? noun : (one ?? (noun === 'people' ? 'person' : noun.replace(/s$/, '')))
	);

	const first = $derived(total === 0 ? 0 : offset + 1);
	const last = $derived(Math.min(total, offset + shown));

	const atStart = $derived(offset <= 0);
	const atEnd = $derived(offset + shown >= total);

	let jumping = $state(false);

	/* The box opens on the current position. */
	const typed = $derived(Math.min(total, offset + 1));

	function jump(wanted: number) {
		// The box clamps and never commits an unreadable value.
		if (perPage) toPage(wanted);
		else {
			onjump(Math.min(Math.max(1, wanted), total));
			pageScroll.toTop();
		}
		jumping = false;
	}
</script>

<!-- A nav: it moves between views of one thing. -->
<nav class="pager" aria-label="Pages">
	<div class="steps">
		<Tooltip label="First">
			<Button
				icon="first_page"
				size="small"
				tone="ghost"
				aria-label="First page"
				disabled={atStart}
				onclick={turning(onfirst)}
			/>
		</Tooltip>
		<Tooltip label="Previous">
			<Button
				icon="chevron_left"
				size="small"
				tone="ghost"
				aria-label="Previous page"
				disabled={atStart}
				onclick={turning(onprevious)}
			/>
		</Tooltip>
	</div>

	{#if perPage && pages > 1 && !phoneWidth.yes}
		<!--
		The page numbers, the current one marked; not on a phone, which has no room for them.
		-->

		<ol class="numbers">
			{#each marks as mark ('page' in mark ? mark.page : mark.gap)}
				<li>
					{#if 'page' in mark}
						<Button
							size="small"
							tone="ghost"
							class={mark.page === page ? 'number current' : 'number'}
							aria-label="Show page {mark.page}"
							aria-current={mark.page === page ? 'page' : undefined}
							onclick={() => toPage(mark.page)}>{count(mark.page)}</Button
						>
					{:else}
						<span class="gap" aria-hidden="true">{'\u2026'}</span>
					{/if}
				</li>
			{/each}
		</ol>
	{/if}

	{#if jumping}
		<!-- NumberInput, never a native number box with the OS's arrows (a gate refuses one). -->

		<div class="jump">
			<NumberInput
				value={perPage ? page : typed}
				min={1}
				max={perPage ? pages : total}
				label={perPage ? 'Choose a page' : 'Go to position'}
				placeholder={'Go to\u2026'}
				autofocus
				width={7}
				onchange={(next) => jump(next)}
				onblur={() => (jumping = false)}
			/>
		</div>
	{:else}
		<!-- The position as a button: the number is the thing changed. -->

		<Tooltip label={perPage ? 'Choose a page' : 'Go to a position'}>
			<button
				type="button"
				class="where"
				onclick={() => (jumping = true)}
				aria-label={perPage ? 'Choose a page' : 'Go to a position'}
			>
				{#if total === 0 && loading}
					<!-- Nothing yet; the box keeps its width. -->
				{:else if total === 0}
					No {noun}
				{:else}
					<span class="range">{count(first)}-{count(last)}</span>
					<span class="of">of {count(total)} {said}</span>
				{/if}
			</button>
		</Tooltip>
	{/if}

	<div class="steps">
		<Tooltip label="Next">
			<Button
				icon="chevron_right"
				size="small"
				tone="ghost"
				aria-label="Next page"
				disabled={atEnd}
				onclick={turning(onnext)}
			/>
		</Tooltip>
		<Tooltip label="Last">
			<Button
				icon="last_page"
				size="small"
				tone="ghost"
				aria-label="Last page"
				disabled={atEnd}
				onclick={turning(onlast)}
			/>
		</Tooltip>
	</div>
</nav>

<style>
	/*
	 * A row of controls, not a bar: no rule or ground. Its height is the whole band (`PageFrame`).
	 */
	.pager {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: var(--space-3);
		block-size: var(--page-footer-height);
	}

	.steps {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	/* The page numbers, in the data face so a number keeps its width as the pages turn. */
	.numbers {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.numbers :global(.number) {
		min-inline-size: var(--control-height-sm);
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}

	/* The page being read: the accent, the one mark of where you are. */
	.numbers :global(.number.current) {
		color: var(--sift-accent);
		font-weight: 600;
	}

	.gap {
		color: var(--sift-ink-3);
		font: var(--text-data);
	}

	/*
	 * Tabular figures, centred rather than baseline-aligned so the readout sits level with the
	 * arrows.
	 */
	.where {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 12ch;
		justify-content: center;
		block-size: var(--control-height-sm);
		padding-inline: var(--space-3);
		border: 1px solid transparent;
		border-radius: var(--radius-md);
		background: transparent;
		color: var(--sift-ink-2);
		font: var(--text-data);
		/* AFTER the shorthand, which would otherwise reset it to the token's 1.5. */
		line-height: 1;
		/* One line, even on a phone. */
		white-space: nowrap;
		cursor: pointer;
		transition:
			background-color var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	/* A finger's reach on a phone through Pressable's ring. */
	@media (max-width: 767px) {
		/* The presses a finger apart, so their rings do not overlap. */
		.steps {
			gap: calc(var(--touch-target) - var(--control-height-sm));
		}

		.where {
			position: relative;
		}

		.where::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}

	.where:hover {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
		color: var(--hover-ink);
	}

	.where:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.range {
		color: var(--sift-ink);
	}

	.of {
		color: var(--sift-ink-3);
	}

	.jump {
		display: flex;
		align-items: center;
	}

	/*
	 * The ring that says the box just opened, and the bar's height; global, as NumberInput renders
	 * it.
	 */
	.jump :global(input) {
		block-size: var(--control-height-sm);
		border-color: var(--sift-accent);
		text-align: center;
	}
</style>
