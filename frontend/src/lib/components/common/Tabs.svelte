<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';
	import type { IconName } from '$lib/design/icons';

	export const design = {
		name: 'Tabs',
		category: 'control',
		role: 'a row of words, one lit, each an address, a filter or a pane',
		basis: 'bits-ui:Tabs',
		states: [
			'page size, every tab an address',
			'page size, filtering a list in place, one tab waiting on a person',
			'panel size, owning its panes',
			'segmented, a sunk track with the pane showing lifted out of it',
			'segmented as a filter of glyphs, the one answering lifted, its glyph wearing the wave',
			'a tab with nothing in it, which shows no count',
			"a tab that can't be opened",
			'mid-travel between two panes, the rule passing the tab in between'
		]
	} satisfies DesignEntry;

	/** One tab: what it is called in code, what it says, and how much is behind it. */
	export interface TabChoice {
		/** Named in an address or a value, never shown. */
		id: string;
		/** The word, which is also what the heading says when this tab is the one showing. */
		label: string;
		/**
		 * How many things are behind it, once known. `undefined` and `0` draw no number: a zero
		 * that becomes eight is a flicker, and a count of nothing is not worth reading.
		 */
		count?: number;
		/**
		 * Something on this tab waits on a person, as the sentence saying what; drawn as a warning
		 * mark.
		 */
		attention?: string;
		/** Cannot be opened from here. Still drawn, so the row keeps its shape. Panes only. */
		disabled?: boolean;
		/**
		 * Drawn as this glyph instead of the word, which stays its name and tooltip. Segmented rows
		 * only.
		 */
		icon?: IconName;
		/** The glyph wears the moving wave (`--sift-wave`) while this tab answers. */
		wave?: boolean;
		/**
		 * The shortcut that chooses this tab, by its id in `$lib/shell/shortcuts`. Glyph tabs only.
		 */
		shortcut?: string;
		/**
		 * This pane scrolls, so it is left out of the measured height and capped at it. Panes only.
		 * If every pane says so, all of them are measured.
		 */
		scrolls?: boolean;
	}

	/** Where a row's travelling rule sits: against the row, in its layout pixels. */
	interface Placed {
		x: number;
		y: number;
		width: number;
	}

	/*
	 * Where each row of tabs last had its rule, for a row drawn afresh with every tab it opens: a
	 * copy arriving within `REMOUNT_MS` of the last one leaving starts where it was. `at` is zero
	 * on screen.
	 */
	const RULES = new Map<string, Placed & { index: number; at: number }>();
	const REMOUNT_MS = 600;

	/** A tab that is an address: everything a `TabChoice` is, and where pressing it goes. */
	export interface TabLink extends TabChoice {
		href: string;
	}
</script>

<script lang="ts">
	/*
	 * ONE TAB PRIMITIVE, in two sizes (`page` beside a heading, `panel` inside one), used three
	 * ways:
	 *
	 * - AN ADDRESS (every tab has `href`): a `<nav>` of links with `aria-current="page"`; a press
	 *   replaces the history entry, so one Back leaves the screen.
	 * - A FILTER (`onselect`): a tablist with one tab stop, arrows moving focus without choosing,
	 *   because choosing costs the caller a request.
	 * - PANES (`pane`): the library's Tabs, in manual activation for the same reason.
	 *
	 * Not bits-ui for the first two: it would put `role="tab"` on links and announce panels that
	 * are not there. Which one is lit is always the caller's.
	 */
	import { tick, untrack, type Snippet } from 'svelte';
	import { Tabs } from 'bits-ui';
	import Icon from '$lib/components/Icon.svelte';
	import { counted } from '$lib/entity/entity-counts';
	import Pressable from './Pressable.svelte';
	import Tooltip from './Tooltip.svelte';

	interface Shared {
		/** Beside a page's heading, or inside a panel. */
		size?: 'page' | 'panel';
		/** What the row is, for anybody who cannot see that the words belong together. */
		label?: string;
	}

	/** Every tab is an address. */
	interface Links extends Shared {
		tabs: readonly TabLink[];
		/** The `id` of the tab showing. */
		current: string;
		look?: undefined;
		onselect?: undefined;
		controls?: undefined;
		keepFocus?: undefined;
		value?: undefined;
		pane?: undefined;
		onchange?: undefined;
	}

	/** No address to go to: the row filters a list in place and says which was pressed. */
	interface Values extends Shared {
		tabs: readonly TabChoice[];
		current: string;
		/** Told which tab was pressed. A request: the row moves when `current` does. */
		onselect: (id: string) => void;
		/** The id of the list the row filters, so each tab can say what it controls. */
		controls?: string;
		/**
		 * `rule`, the row of words; `segmented`, a sunk track with the one answering lifted out of
		 * it.
		 */
		look?: 'rule' | 'segmented';
		/**
		 * A pointer press leaves focus where it was, so a row inside a text field keeps the caret.
		 */
		keepFocus?: boolean;
		value?: undefined;
		pane?: undefined;
		onchange?: undefined;
	}

	/** The row owns its panes. */
	interface Panes extends Shared {
		tabs: readonly TabChoice[];
		/** Which one is showing, by `id`. Bindable. */
		value: string;
		/** Drawn once per tab, inside that tab's pane. Given the tab's id. */
		pane: Snippet<[string]>;
		/** Told which tab was opened, for a caller that has something to do about it. */
		onchange?: (id: string) => void;
		/**
		 * `rule`: the accent rule under the one showing. `segmented`: the positions of one switch.
		 */
		look?: 'rule' | 'segmented';
		current?: undefined;
		onselect?: undefined;
		controls?: undefined;
		keepFocus?: undefined;
	}

	type Props = Links | Values | Panes;

	let {
		tabs,
		size = 'page',
		label = 'What to show',
		current,
		onselect,
		controls,
		value = $bindable(),
		pane,
		onchange,
		look = 'rule',
		keepFocus = false
	}: Props = $props();

	/* See `keepFocus`: the press still lands, only the focus stays put. */
	const holdFocus = (event: MouseEvent) => {
		if (keepFocus) event.preventDefault();
	};

	/** Whether the count is worth drawing: known, and more than nothing. */
	const shows = (count: number | undefined): count is number => count !== undefined && count > 0;

	const hrefOf = (tab: TabChoice) => ('href' in tab ? (tab as TabLink).href : undefined);

	/* The current tab, or the first: a row nobody can Tab into cannot be used from a keyboard. */
	const stop = $derived(tabs.some((tab) => tab.id === current) ? current : tabs[0]?.id);

	function choose(id: string) {
		if (id !== current) onselect?.(id);
	}

	/* Focus only: Enter or Space is the choice, and each is the button's own. */
	function along(event: KeyboardEvent) {
		const row = [
			...(event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[role="tab"]')
		];
		const at = row.indexOf(document.activeElement as HTMLElement);
		if (at === -1) return;
		const to =
			event.key === 'ArrowRight'
				? (at + 1) % row.length
				: event.key === 'ArrowLeft'
					? (at - 1 + row.length) % row.length
					: event.key === 'Home'
						? 0
						: event.key === 'End'
							? row.length - 1
							: -1;
		if (to === -1) return;
		event.preventDefault();
		row[to].focus();
	}

	/* PANES travel as a row, so going from About to History visibly passes Media. */
	const found = $derived(tabs.findIndex((tab) => tab.id === (pane ? value : current)));

	// A value naming no tab leaves the window at the start rather than travelling off the end.
	const at = $derived(found < 0 ? 0 : found);

	/* A rule under the first tab while the value names none would say that tab is chosen. */
	const chosen = $derived(found >= 0 && tabs.length > 0);

	/*
	 * How far the row is about to travel, in panels, so a long move is not twice the speed of a
	 * short one (`--pace`). `$effect.pre` so the count is on the element before the translate.
	 */
	let cameFrom = 0;
	let crossed = $state(1);

	$effect.pre(() => {
		const going = at;
		if (going === cameFrom) return;
		crossed = Math.abs(going - cameFrom);
		cameFrom = going;
	});

	/* The library's props less `hidden`, which would take a pane out of the layout; `inert` and
	   `aria-hidden` carry what it meant. */
	function laidOut(props: Record<string, unknown>): Record<string, unknown> {
		const rest = { ...props };
		delete rest.hidden;
		return rest;
	}

	/*
	 * The window is as tall as the tallest pane, so a tab press never moves what is below it. Each
	 * height is the observer's `borderBoxSize`, the LAYOUT box: the drawn box of a pane arriving
	 * scaled (the pop-out player) would be measured short and never measured again.
	 */
	let panes = $state<(HTMLElement | null)[]>([]);
	let tallest = $state<number | null>(null);

	$effect(() => {
		const boxes = panes.filter((one): one is HTMLElement => one !== null);
		if (boxes.length === 0) return;
		const measured = boxes.filter((_, index) => !tabs[index]?.scrolls);
		const counting = measured.length > 0 ? measured : boxes;
		const laidOutHeight = new Map<Element, number>();
		const measure = () => {
			let most = 0;
			for (const box of counting) {
				most = Math.max(most, laidOutHeight.get(box) ?? box.getBoundingClientRect().height);
			}
			tallest = most;
		};
		measure();
		const watch = new ResizeObserver((entries) => {
			for (const entry of entries) {
				const size = entry.borderBoxSize?.[0]?.blockSize;
				if (size !== undefined) laidOutHeight.set(entry.target, size);
			}
			measure();
		});
		for (const box of boxes) watch.observe(box);
		return () => watch.disconnect();
	});

	/*
	 * ROWS: the rule under the tab showing travels. Measured from the layout box (`offset*`), again
	 * whenever the row changes size, without travelling. Until then the tab wears the rule
	 * (`.here`).
	 */
	let row = $state<HTMLElement | null>(null);
	let under = $state<Placed | null>(null);
	let travel = $state(0);
	let lastIndex = -1;

	/* Where the lit tab is in the row, or null when no tab in it is lit. */
	function placeIn(box: HTMLElement): Placed | null {
		const lit = box.querySelector<HTMLElement>('.tab.here');
		if (lit === null) return null;
		// Relative to the row; inside a filter's button the button's offsets are added.
		let x = lit.offsetLeft;
		let y = lit.offsetTop;
		let from = lit.offsetParent as HTMLElement | null;
		while (from !== null && from !== box && box.contains(from)) {
			x += from.offsetLeft;
			y += from.offsetTop;
			from = from.offsetParent as HTMLElement | null;
		}
		return { x, y: y + lit.offsetHeight, width: lit.offsetWidth };
	}

	/* The row's name in `RULES`: the same tabs are the same row, whichever screen draws it. */
	const rowKey = $derived(tabs.map((tab) => tab.id).join('|'));

	$effect(() => {
		const box = row;
		const index = tabs.findIndex((tab) => tab.id === current);
		const key = rowKey;
		if (box === null || pane) return;
		const here = placeIn(box);
		const left = lastIndex < 0 ? RULES.get(key) : undefined;
		const from =
			lastIndex >= 0 ? lastIndex : left && Date.now() - left.at < REMOUNT_MS ? left.index : index;
		lastIndex = index;
		if (here !== null) RULES.set(key, { ...here, index, at: 0 });
		let aimed = here;
		if (from === index || from < 0 || index < 0 || here === null) {
			travel = 0;
			under = here;
		} else if (untrack(() => under) === null && left) {
			/* A row drawn afresh: start where the last copy left the rule, then send it across. */
			travel = 0;
			under = left;
			void tick().then(() => {
				void box.offsetWidth;
				travel = Math.abs(index - from);
				aimed = placeIn(box);
				under = aimed;
			});
		} else {
			travel = Math.abs(index - from);
			under = here;
		}
		/* A size change moves the rule immediately; only a change of tab travels. Every tab is
		   watched
		   too: a typeface landing late narrows the words inside a row of the same width. */
		const watch = new ResizeObserver(() => {
			const now = placeIn(box);
			if (
				now === null ||
				(aimed && now.x === aimed.x && now.y === aimed.y && now.width === aimed.width)
			)
				return;
			aimed = now;
			RULES.set(key, { ...now, index, at: 0 });
			travel = 0;
			under = now;
		});
		watch.observe(box);
		for (const tab of box.querySelectorAll('.tab')) watch.observe(tab);
		return () => {
			watch.disconnect();
			const was = RULES.get(key);
			if (was) was.at = Date.now();
		};
	});
</script>

{#snippet words(tab: TabChoice)}
	{tab.label}{#if shows(tab.count)}<span class="count">{counted(tab.count)}</span
		>{/if}{#if tab.attention}<!--
		The sentence is on the mark, not only in a tooltip, which is not read out.
	--><span
			class="attention"
			><Tooltip label={tab.attention}
				><Icon name="data_info_alert" size={16} label={tab.attention} /></Tooltip
			></span
		>{/if}
{/snippet}

{#snippet rule()}
	{#if under !== null}
		<span
			class="under"
			aria-hidden="true"
			style:--x="{under.x}px"
			style:--y="{under.y}px"
			style:--w="{under.width}px"
			style:--crossed={travel}
		></span>
	{/if}
{/snippet}

{#if pane}
	<Tabs.Root bind:value onValueChange={(next: string) => onchange?.(next)} activationMode="manual">
		{#snippet child({ props: root })}
			<!--
			`--at` and `--panes` are set here because the rule crosses the tabs with the row of
			panes.
			-->
			<div
				{...root}
				class="panes"
				class:page={size === 'page'}
				class:panel={size === 'panel'}
				class:segmented={look === 'segmented'}
				style:--at={at}
				style:--panes={crossed}
			>
				<Tabs.List aria-label={label}>
					{#snippet child({ props: list })}
						<div {...list} class="track" style:--tabs={tabs.length}>
							<!--
							One rule that travels; `aria-selected` already says which one is
							showing.
							-->
							{#if chosen && look === 'segmented'}
								<span class="pill" aria-hidden="true"></span>
							{:else if chosen}
								<span class="rule" aria-hidden="true"></span>
							{/if}
							{#each tabs as tab (tab.id)}
								<Tabs.Trigger value={tab.id} disabled={tab.disabled}>
									{#snippet child({ props: trigger })}
										<button {...trigger} class="tab">{@render words(tab)}</button>
									{/snippet}
								</Tabs.Trigger>
							{/each}
						</div>
					{/snippet}
				</Tabs.List>

				<!-- `--tallest` as a property: the content policy refuses an inline `style`. -->
				<div class="window" style:--tallest={tallest === null ? null : `${tallest}px`}>
					<div class="rail">
						{#each tabs as tab, index (tab.id)}
							<Tabs.Content value={tab.id}>
								{#snippet child({ props: content })}
									<div
										{...laidOut(content)}
										class="pane"
										class:bounded={tab.scrolls}
										inert={index === at ? undefined : true}
										aria-hidden={index === at ? undefined : 'true'}
										bind:this={panes[index]}
									>
										{@render pane(tab.id)}
									</div>
								{/snippet}
							</Tabs.Content>
						{/each}
					</div>
				</div>
			</div>
		{/snippet}
	</Tabs.Root>
{:else if onselect && look === 'segmented'}
	<!-- A filter drawn as one switch, with the filter row's tab stop and arrow keys. -->
	<div
		class="segmented"
		class:page={size === 'page'}
		class:panel={size === 'panel'}
		class:glyphs={tabs.some((tab) => tab.icon)}
		style:--at={at}
		style:--panes={crossed}
	>
		<div
			class="track"
			role="tablist"
			aria-label={label}
			tabindex="-1"
			onkeydown={along}
			style:--tabs={tabs.length}
		>
			{#if chosen}
				<span class="pill" aria-hidden="true"></span>
			{/if}
			{#each tabs as tab (tab.id)}
				{#if tab.icon}
					<Tooltip label={tab.label} shortcut={tab.shortcut} placement="bottom">
						<Pressable
							feedback="none"
							radius="sm"
							role="tab"
							aria-label={tab.label}
							aria-selected={tab.id === current}
							aria-controls={controls}
							tabindex={tab.id === stop ? 0 : -1}
							onmousedown={holdFocus}
							onclick={() => choose(tab.id)}
						>
							<span class="tab" class:here={tab.id === current} class:wave={tab.wave}
								><Icon name={tab.icon} size={18} /></span
							>
						</Pressable>
					</Tooltip>
				{:else}
					<Pressable
						feedback="none"
						radius="sm"
						role="tab"
						aria-selected={tab.id === current}
						aria-controls={controls}
						tabindex={tab.id === stop ? 0 : -1}
						onmousedown={holdFocus}
						onclick={() => choose(tab.id)}
					>
						<span class="tab" class:here={tab.id === current}>{@render words(tab)}</span>
					</Pressable>
				{/if}
			{/each}
		</div>
	</div>
{:else if onselect}
	<!-- On the inner span: `Pressable`'s reset is scoped and would fight a class. -->
	<div
		class="row"
		class:page={size === 'page'}
		class:panel={size === 'panel'}
		class:travels={under !== null}
		bind:this={row}
		role="tablist"
		aria-label={label}
		tabindex="-1"
		onkeydown={along}
	>
		{@render rule()}
		{#each tabs as tab (tab.id)}
			<Pressable
				feedback="none"
				radius="sm"
				role="tab"
				aria-selected={tab.id === current}
				aria-controls={controls}
				tabindex={tab.id === stop ? 0 : -1}
				onclick={() => choose(tab.id)}
			>
				<span class="tab" class:here={tab.id === current}>{@render words(tab)}</span>
			</Pressable>
		{/each}
	</div>
{:else}
	<nav
		class="row"
		class:page={size === 'page'}
		class:panel={size === 'panel'}
		class:travels={under !== null}
		bind:this={row}
		aria-label={label}
	>
		{@render rule()}
		{#each tabs as tab (tab.id)}
			<a
				class="tab"
				class:here={tab.id === current}
				href={hrefOf(tab)}
				aria-current={tab.id === current ? 'page' : undefined}
				data-sveltekit-replacestate
			>
				{@render words(tab)}
			</a>
		{/each}
	</nav>
{/if}

<style>
	/* Allowed to shrink: without `min-inline-size: 0` the row widens the whole page. */
	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--space-4);
		min-inline-size: 0;
		position: relative;
	}

	.row.panel {
		gap: var(--space-3);
	}

	/*
	 * HOW LONG A MOVE TAKES: `--dur-fast` for the first tab crossed, half a `--dur-instant` for
	 * each after it, never more than `--dur-slow`. A move of nothing is instant.
	 */
	.under,
	.rule,
	.pill,
	.rail {
		--pace: calc(
			min(var(--crossed-tabs), 1) *
				min(var(--dur-fast) + (var(--crossed-tabs) - 1) * var(--dur-instant) / 2, var(--dur-slow))
		);
	}

	.under {
		--crossed-tabs: var(--crossed);
	}

	.rule,
	.pill,
	.rail {
		--crossed-tabs: var(--panes, 1);
	}

	.under {
		position: absolute;
		inset-block-start: 0;
		inset-inline-start: 0;
		inline-size: var(--w);
		border-block-end: 2px solid var(--sift-accent);
		translate: var(--x) calc(var(--y) - 2px);
		transition:
			translate var(--pace) var(--ease),
			inline-size var(--pace) var(--ease);
		pointer-events: none;
	}

	/* The rule is the travelling one; the tab showing keeps its full ink and gives up its own. */
	.travels .here {
		border-block-end-color: transparent;
	}

	/*
	 * A finger's reach on a phone: the ring `Pressable` draws, given to a LINK tab too, measured
	 * from the border box so it does not sit above the rule, and a floor on width so two short
	 * neighbours' rings do not overlap.
	 */
	@media (max-width: 767px) {
		:where(.tab) {
			position: relative;
			min-inline-size: calc(var(--touch-target) - var(--space-4));
			text-align: center;
		}

		.tab::after {
			--tab-short: min(0px, calc((100% + var(--tab-rule) - var(--touch-target)) / 2));
			content: '';
			position: absolute;
			inset-block-start: var(--tab-short);
			inset-block-end: calc(var(--tab-short) - var(--tab-rule));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}

	/* ONE LOOK: a 2px rule held under every tab, so nothing shifts when one becomes current. */
	.tab {
		display: inline-block;
		color: var(--sift-ink-3);
		text-decoration: none;
		white-space: nowrap;
		padding-block-end: var(--space-1);
		--tab-rule: 2px;
		border-block-end: var(--tab-rule) solid transparent;
		transition:
			color var(--dur-instant) var(--ease),
			border-color var(--dur-instant) var(--ease);
	}

	.page .tab {
		font: var(--text-h2);
		letter-spacing: var(--tracking-h2);
	}

	.panel .tab {
		font: var(--text-label);
	}

	/* Hover raises the rule in the quiet ink, never on the tab you are on. */
	.tab:not(.here):hover:not(:disabled) {
		color: var(--sift-ink-2);
		border-block-end-color: var(--sift-line-strong);
	}

	.tab:focus-visible {
		outline: none;
		border-radius: var(--radius-sm);
		box-shadow: var(--focus-ring);
	}

	.here {
		color: var(--sift-ink);
		border-block-end-color: var(--sift-accent);
	}

	.attention {
		margin-inline-start: var(--space-2);
		color: var(--sift-warn);
		vertical-align: middle;
	}

	/* Tabular figures so the row does not shuffle as counts land. */
	.count {
		margin-inline-start: var(--space-2);
		font: var(--text-label);
		color: var(--sift-ink-3);
		font-variant-numeric: tabular-nums;
	}

	.panel .count {
		margin-inline-start: var(--space-1);
		font: var(--text-micro);
	}

	.panes {
		display: grid;
		grid-template-rows: auto minmax(0, 1fr);
		gap: var(--space-2);
		min-block-size: 0;
	}

	/* Equal columns, so the rule's travel is arithmetic. */
	.track {
		position: relative;
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: 1fr;
		gap: var(--space-3);
	}

	.track .tab {
		min-inline-size: 0;
		padding: var(--space-1) 0;
		border-inline: 0;
		border-block-start: 0;
		background: transparent;
		text-align: center;
		cursor: pointer;
	}

	.track .tab[data-state='active'] {
		color: var(--sift-ink);
	}

	.track .tab[data-state='active']:hover {
		border-block-end-color: transparent;
	}

	.tab:disabled {
		cursor: not-allowed;
		opacity: 0.5;
	}

	.tab[data-state='active'] .count {
		color: var(--sift-ink-2);
	}

	/* Worked out rather than measured: a column plus one gap per tab. */
	.rule {
		position: absolute;
		inset-block-end: 0;
		inset-inline-start: 0;
		border-block-end: 2px solid var(--sift-accent);
		inline-size: calc((100% - (var(--tabs) - 1) * var(--space-3)) / var(--tabs));
		translate: calc(var(--at) * (100% + var(--space-3))) 0;
		transition:
			translate var(--pace) var(--ease),
			inline-size var(--dur-fast) var(--ease);
	}

	/* SEGMENTED: inside a panel an underline reads as a second heading rule. */
	.segmented .track {
		gap: var(--space-1);
		padding: var(--space-1);
		border-radius: var(--radius-md);
		background: var(--sift-surface-1);
		border: 1px solid var(--sift-line);
	}

	/* A block, so in a filter row the hover ground is the whole position. */
	.segmented .track .tab {
		display: block;
		position: relative;
		padding: var(--space-1) var(--space-2);
		border-block-end: 0;
		border-radius: var(--radius-sm);
		transition:
			background-color var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	.segmented .track .tab:hover:not([data-state='active']):not(.here):not(:disabled) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
	}

	.segmented .track .tab:active:not([data-state='active']):not(.here):not(:disabled) {
		background-color: color-mix(in srgb, currentColor var(--layer-pressed), transparent);
	}

	.pill {
		position: absolute;
		inset-block: var(--space-1);
		inset-inline-start: var(--space-1);
		inline-size: calc(
			(100% - 2 * var(--space-1) - (var(--tabs) - 1) * var(--space-1)) / var(--tabs)
		);
		border-radius: var(--radius-sm);
		background: var(--sift-surface-4);
		translate: calc(var(--at) * (100% + var(--space-1))) 0;
		transition:
			translate var(--pace) var(--ease),
			inline-size var(--dur-fast) var(--ease);
	}

	/* A SEGMENTED ROW OF GLYPHS, sized to sit inside a field without growing it. */
	.segmented.glyphs .track {
		display: inline-grid;
		block-size: var(--control-height-sm);
		vertical-align: middle;
	}

	.segmented.glyphs .track .tab {
		display: grid;
		place-items: center;
		inline-size: calc(var(--control-height-sm) - 2 * var(--space-1));
		block-size: 100%;
		padding: 0;
	}

	/* The wave slides through the glyph's own shape; a child combinator reaches this glyph only. */
	.segmented .tab.here.wave > :global(.icon) {
		background-image: var(--sift-wave);
		background-size: 300% 100%;
		background-clip: text;
		-webkit-background-clip: text;
		color: transparent;
		--pan-to: 300%;
		animation: pan var(--dur-drift) linear infinite;
	}

	/* THE WINDOW clips, and its height changes over the travel's duration. */
	.window {
		overflow: hidden;
		min-block-size: 0;
		block-size: var(--tallest, auto);
		max-block-size: 100%;
		transition: block-size var(--dur-fast) var(--ease);
	}

	/* `-100%` on `translate` is one panel. */
	.rail {
		display: flex;
		align-items: flex-start;
		inline-size: 100%;
		translate: calc(var(--at) * -100%) 0;
		transition: translate var(--pace) var(--ease);
	}

	.pane {
		flex: none;
		inline-size: 100%;
		min-inline-size: 0;
		min-block-size: 0;
	}

	/* As a height, so the `Scroller` inside resolves against it. */
	.pane.bounded {
		block-size: var(--tallest, auto);
	}

	:global(:root[data-motion='reduce']) .rail,
	:global(:root[data-motion='reduce']) .window,
	:global(:root[data-motion='reduce']) .rule,
	:global(:root[data-motion='reduce']) .pill,
	:global(:root[data-motion='reduce']) .under {
		transition: none;
	}

	/* Less motion, not less meaning: the wave stops and its colour stays. */
	:global(:root[data-motion='reduce']) .segmented .tab.here.wave > :global(.icon) {
		animation: none;
		background-position: 40% 50%;
	}

	.pane:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}
</style>
