<script lang="ts">
	/*
	 * The list under the search box: one listbox holding the groups the server filled (the filters
	 * that match what is typed, the things in the library that do, the searches already run), each
	 * under its heading, with the letters that were typed picked out of every row.
	 *
	 * WHY NOT SHARED: button: a row here is a listbox OPTION, not a button. It carries `role="option"`
	 * and `aria-selected` inside a `role="listbox"`, and is an element at all only because an `<li>`
	 * cannot be reached by keyboard or activated.
	 */
	import { counted } from '$lib/entity/entity-counts';
	import { Button } from '$lib/components/common';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import MarkedText from '$lib/components/common/MarkedText.svelte';
	import { fromBar } from '$lib/shell/motion.svelte';
	import { searchBox, type Remembered, type Row } from '$lib/search/search.svelte';
	import { rowKind } from './search-kinds';

	interface Props {
		/** Whether the list is up. */
		showing: boolean;
		/** The letters the rows are on the list for, which are marked in bold. */
		typing: string;
		/** Whether pressing a row leaves the page, which the row says with an arrow. */
		leaves: (row: Row) => boolean;
		/** Act on the row that was drawn. */
		onchoose: (row: Row) => void;
		onremoverecent: (event: Event, remembered: Remembered) => void;
		onclearrecent: (event: Event) => void;
	}

	let { showing, typing, leaves, onchoose, onremoverecent, onclearrecent }: Props = $props();

	/*
	 * A heading above the first row of each group, read off the rows the list is drawn from, so a
	 * new kind added at the front cannot file every heading over the wrong group.
	 */
	const HEADINGS: Record<Row['kind'], string | null> = {
		// The words typed need no heading: the row says them.
		text: null,
		// It sits at the foot of its band, which already has a heading.
		more: null,
		filter: 'Filters',
		match: 'In your library',
		recent: 'Recent'
	};

	function headingAt(index: number): string | null {
		const rows = searchBox.rows;
		const row = rows[index];
		if (row === undefined) return null;
		if (rows[index - 1]?.kind === row.kind) return null;
		return HEADINGS[row.kind];
	}
</script>

<!-- One suggestion's text with the typed letters picked out, by the component the settings search
     shares, so the matched letters are one colour on every screen. -->
{#snippet marked(text: string)}<MarkedText {text} typed={typing} />{/snippet}

{#if showing}
	<!-- The list comes out from under the top bar as Sort by and the Filter panel do (`fromBar`). -->
	<div class="popover" transition:fromBar>
		<!-- A heading is a `role="presentation"` item: a listbox may own options and nothing else, or
		     `aria-activedescendant` loses its chain. -->
		<Scroller>
			<!-- `hits`, not `ui-menu`: a screen-local list wears a screen-local name. -->
			<ul class="hits" id="search-suggestions" role="listbox" aria-label="Suggestions">
				<!-- Keyed by position: two people with one name is the ordinary case, and a duplicate key
				     blanks the list. -->
				{#each searchBox.rows as row, index (index)}
					{@const kind = rowKind(row)}
					{@const heading = headingAt(index)}
					{#if heading}
						<li class="head section-label" role="presentation">
							<span>{heading}</span>
							{#if heading === 'Recent'}
								<!-- `quiet`: a word that acts on a heading, as Clear all after chips is, with
								     no slab and no underline until pointed at. `link` is a run inside a
								     sentence and wears its underline always. -->
								<Button tone="quiet" size="small" onmousedown={onclearrecent}>Clear</Button>
							{/if}
						</li>
					{/if}
					<li class:recent={row.kind === 'recent'}>
						<!-- tabindex=-1: focus stays on the input, the combobox pattern. -->
						<button
							type="button"
							id="search-suggestion-{index}"
							role="option"
							tabindex="-1"
							aria-selected={index === searchBox.highlighted}
							class:on={index === searchBox.highlighted}
							class:more={row.kind === 'more'}
							onmousedown={(event) => {
								/* mousedown: the input blurs on mousedown and the list would be gone
								   before a click landed. */
								event.preventDefault();
								onchoose(row);
							}}
						>
							<!-- The kind's name on hover and as the glyph's own name. -->
							<Tooltip label={kind.name}>
								<Icon name={kind.icon} size={16} label={kind.name} />
							</Tooltip>

							{#if row.kind === 'filter'}
								<!-- The label first (whatever put the row on the list), the hint unmarked, and the
								     example carrying the token, the short thing to type. -->
								<span class="token">{@render marked(row.filter.label)}</span>
								<span class="value">{row.filter.hint}</span>
								<span class="detail">{@render marked(row.filter.example)}</span>
							{:else if row.kind === 'match'}
								<!-- A line of text rather than a chip: most suggestions are not picked, and a
								     wall of faces is the work somebody came to this box to avoid. -->
								<span class="value">{@render marked(row.match.value)}</span>
								{#if row.match.detail}<span class="detail">{row.match.detail}</span>{/if}
								<!-- The asker's own count: what picking it would give them. -->
								{#if row.match.count !== null && row.match.count !== undefined}
									<span class="count">{counted(row.match.count)}</span>
								{/if}
								<!-- A row that leaves the page says so. -->
								{#if leaves(row)}
									<span class="goes" aria-hidden="true">
										<Icon name="arrow_forward" size={16} />
									</span>
								{/if}
							{:else if row.kind === 'more'}
								<!-- How many pressing this will show, from the row itself. -->
								<span class="value">Show {counted(row.reveals)} more</span>
							{:else if row.kind === 'text'}
								<!-- Unmarked: this row IS what was typed. -->
								<span class="value">{row.query}</span>
								<span class="detail">Search for this</span>
							{:else}
								<span class="value">{@render marked(row.remembered.label)}</span>
								{#if leaves(row)}
									<span class="goes" aria-hidden="true">
										<Icon name="arrow_forward" size={16} />
									</span>
								{/if}
							{/if}
						</button>
						<!-- A sibling of the row: a button inside a button is markup browsers take apart. -->
						{#if row.kind === 'recent'}
							{@const remembered = row.remembered}
							<span class="remove">
								<Tooltip label="Remove from recent searches">
									<Button
										tone="ghost"
										size="small"
										icon="close"
										tabindex={-1}
										aria-label="Remove {remembered.label} from recent searches"
										onmousedown={(event: MouseEvent) => onremoverecent(event, remembered)}
									/>
								</Tooltip>
							</span>
						{/if}
					</li>
				{/each}
			</ul>
		</Scroller>
	</div>
{/if}

<style>
	.popover {
		position: absolute;
		z-index: var(--z-popover);
		inset-inline: 0;
		top: calc(100% + var(--space-1));
		display: grid;
		grid-template-rows: minmax(0, auto);
		/* So the lit row's ground never touches the list's edge. */
		padding: var(--space-2);
		background: var(--sift-surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius-md);
		box-shadow: var(--elev-2);
	}

	/* The ceiling is on the box that scrolls; the popover is a grid so it resolves to a height. */
	.popover :global(.scroll-root) {
		max-block-size: 320px;
	}

	/*
	 * The scrollbar held off the edge, into a gutter of its own. A margin, because the library places
	 * the bar with an inline `right`, which no stylesheet rule can beat; a margin shifts the box that
	 * `right` placed. Here rather than in `Scroller`, since each caller has its own inset.
	 */
	.popover :global(.scroll-bar) {
		margin-inline-end: var(--space-1);
	}

	.hits {
		margin: 0;
		padding: var(--space-1);
		list-style: none;
	}

	/*
	 * A gutter for the scrollbar only while there is one (`data-state="visible"` is the library's
	 * own signal), so the token at the far end of a row is not covered by it. One `:global` tail,
	 * because Svelte allows `:global` only at the start or end of a selector. The bar is 10px and
	 * held 4px off the edge, so 16 leaves daylight; if either grows this must too.
	 */
	.popover :global(.scroll-root:has(.scroll-bar[data-state='visible']) .hits) {
		padding-inline-end: var(--space-4);
	}

	.hits li {
		display: block;
	}

	.hits li.recent {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.hits li.recent > button {
		flex: 1;
		min-inline-size: 0;
	}

	.remove {
		flex: none;
	}

	/* Each result its own object, separated by the gap between them rather than a ruled line. */
	.hits li:not(.head) {
		margin-block-end: 3px;
	}

	.hits li:not(.head):last-child {
		margin-block-end: 0;
	}

	/* No ground at rest, said so the browser's white one does not show; it steps in when lit. */
	.hits li:not(.head) > button {
		background: transparent;
	}

	.head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: var(--space-2) var(--space-2) var(--space-1);
	}

	/* Not above the first group: the list's own top edge separates it from the box. */
	.head:not(:first-child) {
		margin-block-start: var(--space-2);
		border-block-start: 1px solid var(--sift-line);
	}

	/* The token as it will be typed, monospaced so it reads as something to enter. */
	.token {
		flex: none;
		color: var(--sift-accent-text);
		font: var(--text-data);
	}

	.hits li > button.on .token {
		color: inherit;
	}

	/* The row that reveals more is an offer about the list: centred, only as wide as its words. */
	.hits li:not(.head) > button.more {
		justify-content: center;
		inline-size: fit-content;
		margin-inline: auto;
	}

	/* The face of a word that acts, as `Clear all` wears it, without a slab's padding or border. */
	.hits li:not(.head) > button.more .value {
		flex: none;
		font: var(--text-label);
	}

	.hits li:not(.head) > button {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		width: 100%;
		padding: var(--space-2);
		border: 0;
		color: var(--foreground);
		font: var(--text-body);
		text-align: start;
		cursor: pointer;
	}

	/* The pointer's row and the keyboard's row look the same; the heading's Clear keeps its own. */
	.hits li:not(.head) > button:hover,
	.hits li:not(.head) > button.on {
		background: var(--sift-surface-4);
		color: var(--sift-ink);
	}

	/* The app's one menu-row corner (`--menu-row-radius`). */
	.hits li:not(.head) > button {
		border-radius: var(--menu-row-radius);
		transition: background-color var(--dur-instant) var(--ease);
	}

	:global(:root[data-motion='reduce']) .hits li:not(.head) > button {
		transition: none;
	}

	.value {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* On a lit row the accent is the ground, so the marked letters take the row's ink. `:global`,
	   because the run carries `MarkedText`'s scoping. */
	.hits li > button.on :global(.hit),
	.hits li:not(.head) > button:hover :global(.hit) {
		color: inherit;
	}

	.detail,
	.count {
		flex: none;
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	/* The mark on a row that leaves, quiet: it tells two kinds of row apart. */
	.goes {
		flex: none;
		display: grid;
		place-items: center;
		margin-inline-start: auto;
		color: var(--sift-ink-3);
	}

	.hits li > button:hover .detail,
	.hits li > button.on .detail,
	.hits li > button:hover .count,
	.hits li > button.on .count {
		color: inherit;
	}
</style>
