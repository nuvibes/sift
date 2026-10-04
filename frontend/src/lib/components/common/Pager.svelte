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
	 * Everything the pager needs, as one shape a screen can hand up.
	 *
	 * The pager belongs in the FRAME's foot (the same place on every screen, whether the wall
	 * fills the window or holds four rows) and on the Organize screens the frame is the route's
	 * while the paging is the panel's. So a panel that pages describes its pager with this and
	 * reports it through `onpaging`, and the route draws it in the foot. A panel drawing its own
	 * pager under its last row would move the band from screen to screen.
	 */
	export interface PagerProps {
		/** Where the page starts, counting from zero. */
		offset: number;
		/** How many rows this page is actually showing. */
		shown: number;
		/** How many there are in all. */
		total: number;
		/**
		 * What the rows are, plural, as the screen names them: "files", "people", "Sites". The
		 * readout says it after the total, so a count says what it counts ("1-16 of 246 Sites").
		 */
		noun?: string;
		/** The same word for one row, where dropping the plural's last "s" would not make it. */
		one?: string;
		/**
		 * The first page is still on its way, so there is no count to say. The readout says nothing
		 * rather than "No files": a wall whose files have not arrived yet is not an empty wall, and
		 * the skeleton above is already saying so.
		 */
		loading?: boolean;
		onfirst: () => void;
		onprevious: () => void;
		onnext: () => void;
		onlast: () => void;
		/** Take me to the nth one, counting from one the way the readout does. */
		onjump: (position: number) => void;
		/**
		 * The page size, for a list whose pages are FIXED: numbered pages then stand between the
		 * arrows, and the box takes a page number. Only where a page is the same rows on every
		 * screen (the queue, History: a set number of lines a read, newest first); a wall that
		 * fits its page to the window keeps positions, for the reason at the head of the pager.
		 */
		perPage?: number;
	}

	/** One press of a numbered pager: a page, or a gap standing for the pages between two. */
	export type PageMark = { page: number } | { gap: string };

	/**
	 * The page numbers a pager shows: the first and the last, the current one and one either side
	 * of it, and a gap where pages are left out. A gap standing for ONE page is that page instead:
	 * an ellipsis hiding a single number is longer to read than the number.
	 */
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
	 * deliberately has none; a page holds as many rows as fit the window, so a numbered page is a
	 * different set of files on each screen. The numbered variant (`perPage`) is the same bar with
	 * the numbers added, so a list with fixed pages is not a second pager.
	 *
	 * Where you are in a long list, and how to move through it.
	 *
	 * No page numbers: "page 3" names a different set of files on a laptop than on a monitor, so a
	 * bookmark or shared link would open somewhere else. This says where you are ("1,240-1,287 of
	 * 9,000") and moves by pages without naming them. The jump box takes a position, which is a
	 * fact about the library: the 5,000th file is the 5,000th on any screen.
	 *
	 * At the end of the content, inside the scrolling area, directly under the last row. Pinned in
	 * the frame's footer it would leave a band of empty ground on every page whose rows do not fill
	 * the window, and that band cannot be closed by shrinking the frame: the wall measures the
	 * scrolling body to decide how many files to ask for, so a body sized to its content makes the
	 * page size a function of its own result (see `PageFrame`). The accepted cost is scrolling to
	 * the bottom of a long wall to reach it.
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

	/*
	 * Every turn lands at the top of the new page: the pager sits at the foot, and the box would
	 * otherwise stay at the bottom with the new page's first rows off the top. The pager owns the
	 * press, not the scroll box; `pageScroll` knows which box is scrolling.
	 */
	function turning(turn: () => void): () => void {
		return () => {
			turn();
			pageScroll.toTop();
		};
	}

	/* Grouped thousands, because the readout is a number somebody reads rather than a number
	   somebody computes with, and "9000" scans as four digits of something. */
	const count = (value: number) => value.toLocaleString();

	/* "of 1 file", not "of 1 files". People is the one plural in use that is not the word plus an
	   "s"; any other caller with an irregular plural names its singular through `one`. */
	const said = $derived(
		total !== 1 ? noun : (one ?? (noun === 'people' ? 'person' : noun.replace(/s$/, '')))
	);

	const first = $derived(total === 0 ? 0 : offset + 1);
	const last = $derived(Math.min(total, offset + shown));

	const atStart = $derived(offset <= 0);
	const atEnd = $derived(offset + shown >= total);

	let jumping = $state(false);

	/* The box opens on the position you are already at, which is what a jump box should say: it is a
	   number you are changing rather than a blank somebody has to work out the units of. */
	const typed = $derived(Math.min(total, offset + 1));

	function jump(wanted: number) {
		// The box clamps to `min` and `max` itself, and an empty or unreadable one never reaches
		// here: it commits nothing and puts the position back. So there is one thing left to do.
		if (perPage) toPage(wanted);
		else {
			onjump(Math.min(Math.max(1, wanted), total));
			pageScroll.toTop();
		}
		jumping = false;
	}
</script>

<!--
	A nav rather than a toolbar: it moves between views of the same thing, which is what a browser,
	a screen reader and a keyboard user all already understand a nav to be.
-->
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
		<!-- The pages by number: the first, the last, and the ones around this one. The one being
		     read is marked for a screen reader as well as drawn in the accent.

		     Not on a phone. Up to seven numbers a finger apart do not fit beside the four steps on a
		     phone's width, and a finger apart is the only width at which each is its own press. The
		     phone keeps the steps and the position, which is "Choose a page" and takes any number. -->
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
		<!--
			`NumberInput`, not a bare `type="number"`.

			A native one draws the operating system's own stepper arrows: a pair of grey chevrons in
			the site's look, at a size the page has no say over, in the middle of the app's own
			chrome. That is the fault `NumberInput` exists to fix. A gate refuses a raw number input
			anywhere but inside that component, because nothing else would catch it.
		-->
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
		<!--
			The position, and a way to change it by typing.

			A button rather than a label with a control beside it: the number IS the thing being
			changed, so making it the target is one fewer thing on the bar and says what it does
			without a word of explanation.
		-->
		<Tooltip label={perPage ? 'Choose a page' : 'Go to a position'}>
			<button
				type="button"
				class="where"
				onclick={() => (jumping = true)}
				aria-label={perPage ? 'Choose a page' : 'Go to a position'}
			>
				{#if total === 0 && loading}
					<!-- Nothing to say yet: the box keeps its width so the band does not change shape
					     when the count lands. -->
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
	 * A row of controls at the end of the content, and NOT a bar.
	 *
	 * It sits in the frame's footer track with no rule along its top and no ground of its own, by
	 * choice. A full-width line under a wall of tiles reads as the page ending twice;
	 * the row of controls is legible against the screen without one, and the gap the track gives it
	 * is the separation.
	 *
	 * THIS HEIGHT IS THE WHOLE BAND. The track around it carries inline padding and no block padding
	 * at all, because the space inside this box is the space under the last row. See `PageFrame`:
	 * padding the track as well would make a band far taller than the pager.
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
	 * Tabular figures, so the numbers do not shuffle sideways as they change width. Without it the
	 * readout jitters on every page turn.
	 *
	 * CENTRED, not baseline-aligned, so the numbers do not sit high above the arrows.
	 * `align-items: baseline` in a box with a fixed height puts the text's baseline where the font
	 * wants it and leaves the slack underneath, so the readout would ride above the middle of its
	 * own box while the icon buttons beside it are centred in theirs, and the row would read as
	 * tilted. The two spans in here are the same size in the same face, so there is no baseline to
	 * protect: centring them is the same alignment with the box's own slack shared evenly.
	 *
	 * `line-height: 1` after the font shorthand, for the same reason the tile's clock needs it: the
	 * shorthand carries the token's 1.5, which makes the text box half again as tall as the letters
	 * and puts the spare room under the baseline where these digits have nothing.
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
		/* One line. On a phone, where every press beside it is a finger's width, the readout would
		   otherwise be squeezed until "1-24" broke into two lines of its own. */
		white-space: nowrap;
		cursor: pointer;
		transition:
			background-color var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	/* A finger's reach on a phone, the readout keeping the pager's own height: the ring `Pressable`
	   draws, for the reason given there. */
	@media (max-width: 767px) {
		/* The presses a finger's width apart, centre to centre: their reach is a ring round each,
		   and two rings closer than that share the space between them. */
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

	/* The box brings its own look from `NumberInput`; what is set here is the ring that says it is
	   the thing that just opened, and the height that keeps the bar from changing shape when it
	   does. Reaching into it with `:global` is the point: the input is rendered by the component,
	   so it carries no scoping class of this file's and an unscoped rule would match nothing. */
	.jump :global(input) {
		block-size: var(--control-height-sm);
		border-color: var(--sift-accent);
		text-align: center;
	}
</style>
