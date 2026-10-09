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
		 * How many things are behind it, once that is known.
		 *
		 * `undefined` and `0` both draw no number: the first because nothing has answered yet (a
		 * zero that becomes eight a moment later is a flicker), the second because a count of
		 * nothing is not worth reading on every tab of every page. The tab stays, so the row is the
		 * same shape everywhere and nothing moves under the hand.
		 */
		count?: number;
		/**
		 * That something on this tab is WAITING on a person, as the sentence saying what.
		 *
		 * Drawn as a small mark in the warning colour after the count, and the sentence is its
		 * label and its tooltip. A string rather than a flag: this file knows what a tab is and
		 * nothing about what any particular tab holds, so the page has the words. Absent is the
		 * ordinary state and draws nothing.
		 */
		attention?: string;
		/** Cannot be opened from here. Still drawn, so the row keeps its shape. Panes only. */
		disabled?: boolean;
		/**
		 * Drawn as this glyph instead of the word. The word stays the tab's name and its tooltip, so
		 * a row of glyphs is still a row of named choices: the search box's two ways of searching.
		 * Segmented filter rows only, where each tab is a small square position of one switch.
		 */
		icon?: IconName;
		/**
		 * The glyph wears the moving wave (`--sift-wave`) while this tab is the one answering. The
		 * look of the one choice whose answer comes from the model (search by what things look
		 * like), said by the glyph rather than by a second colour on the segment.
		 */
		wave?: boolean;
		/**
		 * The shortcut that chooses this tab, by its id in `$lib/shell/shortcuts`, shown in the glyph's
		 * tooltip beside the word: the search box's Ctrl + Left and Ctrl + Right. Glyph tabs only.
		 */
		shortcut?: string;
		/**
		 * This pane scrolls, so it does not get a vote on how tall the box is. Panes only.
		 *
		 * The window is as tall as the tallest pane (see the measurement below), which is right for
		 * readings of one thing of similar length and wrong for a pane of unbounded length, such as
		 * a long history. A pane that says this is left out of the measurement and then capped at
		 * it. It cannot be all of them: a box whose every pane declines has no height to cap at, so
		 * the measurement falls back to all of them.
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
	 * Where each row of tabs last had its rule, by the row's tabs, for a row that is drawn afresh.
	 *
	 * A screen may draw its tabs again for every tab it opens (a person's page does: the tab is in
	 * the address and the strip is rebuilt with the page), so the rule a new row starts with would
	 * always be under the tab opened and there would be nothing to travel from. The copy leaving
	 * writes when it left; the copy arriving within `REMOUNT_MS` of that starts where it was.
	 * Longer ago is somebody coming back to the page later, not a tab being changed, and starts
	 * still. `at` is zero while the row is on screen.
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
	 * ONE TAB PRIMITIVE, in two sizes, used three ways.
	 *
	 * The look is the rule: a row of words, quiet until current, the current one in full ink with the
	 * accent rule under it, a count after the word, and a warning mark where something waits on a
	 * person. `size` says where the row stands: `page` beside a page's heading, at heading size,
	 * and `panel` inside a panel, at label size, in equal columns.
	 * Panes may take the `segmented` look instead (see `look`), for a strip inside a panel that is
	 * one switch's positions.
	 *
	 * What differs between the three uses is what a tab IS, and each gets the element that says so:
	 *
	 * - AN ADDRESS (every tab has `href`): a `<nav>` of links with `aria-current="page"`, so a link
	 *   opens on its tab; a press replaces the history entry, so one Back leaves the screen.
	 * - A FILTER (`onselect`): where there is no address, the same row filters a list the caller
	 *   already draws, as a tablist: `role="tab"` with `aria-selected`, one tab stop for the row,
	 *   arrow keys and Home and End moving along it, Enter or Space choosing. Arrowing moves focus
	 *   without choosing, because choosing costs the caller a request.
	 * - PANES (`pane`): several short readings of one thing in one box. The library's Tabs, which
	 *   ties each tab to the pane it opens (`aria-controls` and `aria-labelledby`, ids no caller
	 *   could know), in manual activation for the same reason as above.
	 *
	 * WHY NOT BITS-UI for the first two: an address is a link, and the library's Tabs would put
	 * `role="tab"` on anchors, take the arrow keys and announce a panel that is not there; a
	 * filtering has no panes of its own for the library's triggers to be tied to, so all it would
	 * add is roving focus, one small handler here.
	 *
	 * Which one is lit is always the caller's (`current`, or `value` for panes), never a copy kept
	 * here.
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
		 * `rule`, the row of words every page wears; `segmented`, the panes' sunk track with the one
		 * answering lifted out of it, for a filter that is one switch's positions (and the only look
		 * a row of glyphs takes: see `TabChoice.icon`).
		 */
		look?: 'rule' | 'segmented';
		/**
		 * A pointer press leaves focus where it was. For a row inside a text field (the search box's
		 * two ways of searching): pressing a mode, even the one already answering, keeps the caret
		 * in the field, so the next keystroke still lands in it. The keyboard is unaffected: arrowing
		 * along the row is still moving focus along it.
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
		 * `rule`: the words with the accent rule under the one showing, the look every tab row wears.
		 * `segmented`: a sunk track with the one showing lifted out of it on a raised segment, which
		 * reads as the positions of one switch rather than as a second heading rule inside a panel.
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

	/* The one tab stop of a filter row. The current tab, or the first when the current one is
	   not in the row: a row nobody can Tab into is a row a keyboard cannot use at all. */
	const stop = $derived(tabs.some((tab) => tab.id === current) ? current : tabs[0]?.id);

	function choose(id: string) {
		if (id !== current) onselect?.(id);
	}

	/* Along the row with the arrows, to the ends with Home and End. Focus only: Enter or Space is
	   the choice, and each is the button's own. */
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

	/*
	 * PANES: the panes sit in a row and the row travels, so going from About to History visibly
	 * passes Media. The window shows one panel and the row moves by whole panels; the direction
	 * falls out of the arithmetic rather than being remembered. A segmented filter row's lifted
	 * segment reads the same numbers off `current`, and travels the same way.
	 */
	const found = $derived(tabs.findIndex((tab) => tab.id === (pane ? value : current)));

	// A value naming no tab leaves the window at the start rather than travelling off the end.
	const at = $derived(found < 0 ? 0 : found);

	/*
	 * Whether any tab is actually the one showing, which is a different question from where the
	 * window is. The window has to be SOMEWHERE; the rule under the strip does not, and a rule
	 * under the first tab while the value names none of them would say that tab is chosen.
	 */
	const chosen = $derived(found >= 0 && tabs.length > 0);

	/*
	 * How far the row is about to travel, in panels. About to History is twice the distance of
	 * About to Media, and covering both in `--dur-fast` would make the long one twice the speed.
	 * The stylesheet turns the count into a time (`--pace`: longer for a longer move, with a
	 * ceiling). `$effect.pre` so the count is on the element before the translate that uses it.
	 */
	let cameFrom = 0;
	let crossed = $state(1);

	$effect.pre(() => {
		const going = at;
		if (going === cameFrom) return;
		crossed = Math.abs(going - cameFrom);
		cameFrom = going;
	});

	/*
	 * THE LIBRARY'S PROPS FOR A PANE, LESS `hidden`, which would take a pane out of the layout and
	 * leave the row nothing to travel past. What `hidden` was carrying is kept on the element:
	 * `inert` takes a pane that is not showing out of reach of the pointer, the tab order and find,
	 * and `aria-hidden` out of what is read out.
	 */
	function laidOut(props: Record<string, unknown>): Record<string, unknown> {
		const rest = { ...props };
		delete rest.hidden;
		return rest;
	}

	/*
	 * How tall the window is: the height of the tallest pane, measured, so a tab press never moves
	 * everything below the box. Every pane is observed, because a pane grows while off screen. A
	 * pane that says it scrolls is observed but does not count: it is capped at this number.
	 *
	 * ## The LAYOUT height, never the drawn one
	 *
	 * `getBoundingClientRect` answers the box as DRAWN, after every transform above it, and the
	 * pop-out player arrives scaled from 0.88 to 1. A record that landed while it was arriving
	 * would be measured 12% short, and nothing would measure it again: a transform changes no
	 * layout, so the observer below has nothing to report when the arrival finishes. The window
	 * would keep the short number and clip the last row of the panel at its foot.
	 *
	 * So each pane's height is the observer's `borderBoxSize`, which is the layout box whatever is
	 * scaling it, kept per pane because an observer reports only the boxes that changed. The drawn
	 * box is asked only for a pane the observer has not reported yet: that is the first frame,
	 * before its first report, which always comes and replaces it (and it is the only answer there
	 * is in a document that lays nothing out).
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
	 * ROWS (addresses and filters): the rule under the tab showing TRAVELS, as the File info tabs'
	 * segment does. Going from Files to History passes every tab in between, so the move says where
	 * you went from as well as where you are.
	 *
	 * Measured rather than worked out: a row of words is not equal columns, and it wraps. The
	 * layout box (`offset*`), never the drawn one, for the reason the panes give above: a row inside
	 * something arriving scaled would be measured short. Measured again whenever the row changes
	 * size (a count landing widens a tab), without travelling: only a change of tab moves.
	 *
	 * Until the first measurement the tab showing wears the rule itself (`.here`), so the first
	 * frame, the server's markup and a document that lays nothing out all still say which is lit.
	 */
	let row = $state<HTMLElement | null>(null);
	let under = $state<Placed | null>(null);
	let travel = $state(0);
	let lastIndex = -1;

	/* Where the lit tab is in the row, or null when no tab in it is lit. */
	function placeIn(box: HTMLElement): Placed | null {
		const lit = box.querySelector<HTMLElement>('.tab.here');
		if (lit === null) return null;
		// Relative to the row: the tab's offsets are taken against its own offset parent, which is
		// the row, or (inside a filter's button) the button, so the button's are added.
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
		/* Where the rule is headed: what the row measured when this ran, then what it measures
		   whenever the row or a tab in it changes size. */
		let aimed = here;
		if (from === index || from < 0 || index < 0 || here === null) {
			travel = 0;
			under = here;
		} else if (untrack(() => under) === null && left) {
			/* A row drawn afresh for the tab it now shows: start the rule where the last copy of
			   this row left it, let that be laid out, then send it across. */
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
		/* A row changing size (a count landing, the window narrowing) moves the rule with it immediately:
		   only a change of tab travels. A report that finds the lit tab where the rule is headed
		   (the observer's first, nearly always) changes nothing, so a travel under way goes on.
		   Every tab is watched as well as the row, because a tab can change width inside a row that
		   does not: the typeface arriving after the first measurement narrows every word, the row
		   keeps its width, and a rule measured against the fallback face would sit beside its tab,
		   past the end of the word, until something else moved. And the first report is read like
		   any other rather than passed over, because the typeface can land between the measurement
		   and that report, which then carries the only news of it. */
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

<!-- The words of one tab, whichever element carries them: the label, the count once there is one
     worth reading, and the waiting mark. -->
{#snippet words(tab: TabChoice)}
	{tab.label}{#if shows(tab.count)}<span class="count">{counted(tab.count)}</span
		>{/if}{#if tab.attention}<!--
		The mark, INSIDE the tab, and the sentence is on the mark rather than only in a tooltip, which
		is a pointer's affordance and is not read out.
	--><span
			class="attention"
			><Tooltip label={tab.attention}
				><Icon name="data_info_alert" size={16} label={tab.attention} /></Tooltip
			></span
		>{/if}
{/snippet}

<!-- The travelling rule of a row. See `under`. -->
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
				The strip and the pane, as two rows of one grid: a height given to this box bounds the
				pane by the row, so whatever scrolls inside it scrolls within the panel. `--at` and
				`--panes` are set here because the rule under the strip crosses the same tabs at the
				same moment and speed as the row of panes: one movement.
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
								The accent rule, as one line that travels rather than a mark each tab turns
								on and off: About to History passes Media, as the panes below do. Announced
								to nobody: `aria-selected` on the tab already says which one is showing.
							-->
							{#if chosen && look === 'segmented'}
								<!-- The raised segment, travelling the same way the rule does. -->
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

				<!-- The window clips and the row of panes behind it moves. `--tallest` is handed to the
				     stylesheet as a property: the policy this app is served under refuses an inline
				     `style`, and a `style:` directive compiles to a property. -->
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
	<!--
		A filter drawn as one switch: the panes' sunk track and lifted segment, with the tabs as
		`Pressable`s and the same one tab stop and arrow keys as the filter row below. A tab with a
		glyph draws the glyph, named by its word, which is also its tooltip.
	-->
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
	<!-- A filter: `Pressable`s, the shared bare button, with the words inside wearing the `.tab`
	     look. On the inner span because `Pressable`'s reset is scoped and would fight a class. -->
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
	/*
	 * A row of words that is allowed to SHRINK. Without `min-inline-size: 0` a row of words inside a
	 * flex heading refuses to wrap and widens the whole page, pushing the controls at the far end of
	 * that heading off the side of the window.
	 */
	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		/* Two words apart on a page: closer and they read as one phrase, further and they stop
		   belonging to the heading they sit beside. */
		gap: var(--space-4);
		min-inline-size: 0;
		/* What the travelling rule is placed against, from the first frame, so the tab's offsets
		   are measured from here. See `under`. */
		position: relative;
	}

	.row.panel {
		gap: var(--space-3);
	}

	/*
	 * HOW LONG A MOVE TAKES, for everything in a tab row that travels (the rule, the pill, the
	 * panes' rail): `--dur-fast` for the first tab crossed and half a `--dur-instant` for each tab
	 * after it, and never more than `--dur-slow`. A long move takes longer than a short one, so the eye can
	 * follow it, and the longest (the first tab to the eighth) is still done in the time one
	 * deliberate motion takes. One `--dur-fast` PER tab would make seven tabs nearly a second,
	 * during which the row says the old tab is still showing. A move of nothing (the
	 * row resizing) is instant: the first factor is nought.
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

	/*
	 * The travelling rule: the same 2px of accent the tab showing wore, drawn once for the row and
	 * moved to it at the row's pace (`--pace` above). Its width follows too, since words are not
	 * one width.
	 */
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
	 * A finger's reach on a phone, the word keeping its size: the invisible ring `Pressable` draws
	 * round every tab it draws, given here to a tab that is a LINK (a page's places) as well. The
	 * row keeps wrapping in whole tabs; its gap (`--space-4`) is what a 28px tab's ring reaches past
	 * it, so a ring stops where the next line's starts.
	 *
	 * The ring is measured from the tab's BORDER box. An absolute child's offsets start at the
	 * padding edge, which on a tab stops above the 2px rule, so a ring written as the other
	 * primitives write theirs would sit 1px high, and a press just under a tab's centre would go
	 * to the tab below it whenever a row of tabs wrapped.
	 *
	 * And a tab is never narrower than a finger less the gap: a short word like "All" is narrower
	 * than that, so beside another its ring would reach into its neighbour's. With this floor the
	 * centres of two neighbours are at least a finger apart, the word centred in its width.
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

	/*
	 * ONE LOOK FOR EVERY TAB: the word in the quiet ink until it is the one showing, a 2px rule
	 * held under it whether or not it is current, so nothing shifts when it becomes it.
	 */
	.tab {
		/* A box of its own in every use: a link in the row is one already (a flex item is), and the
		   words inside a narrowing's button need to be one for the rule to sit inside the button. */
		display: inline-block;
		color: var(--sift-ink-3);
		text-decoration: none;
		white-space: nowrap;
		padding-block-end: var(--space-1);
		/* The rule's width, named because the phone's ring is measured past it. */
		--tab-rule: 2px;
		border-block-end: var(--tab-rule) solid transparent;
		transition:
			color var(--dur-instant) var(--ease),
			border-color var(--dur-instant) var(--ease);
	}

	/* The page's size is the heading's own size and weight, so the row reads as alternatives to the
	   word beside it rather than as furniture underneath it. */
	.page .tab {
		font: var(--text-h2);
		letter-spacing: var(--tracking-h2);
	}

	/* The panel's size is a label's: inside a panel, under a heading that is already quiet. */
	.panel .tab {
		font: var(--text-label);
	}

	/*
	 * The rule underneath comes up as well as the ink stepping, because a one-step grey change on a
	 * word is invisible to anybody not looking straight at it. In the quiet ink rather than the
	 * accent, so pointing at a tab shows where it would take you without pretending you are there.
	 * Never on the one you are on: a hover that replaced its accent would say it is not chosen.
	 */
	.tab:not(.here):hover:not(:disabled) {
		color: var(--sift-ink-2);
		border-block-end-color: var(--sift-line-strong);
	}

	.tab:focus-visible {
		outline: none;
		border-radius: var(--radius-sm);
		box-shadow: var(--focus-ring);
	}

	/* The one you are on: full-strength ink and the accent rule under it. */
	.here {
		color: var(--sift-ink);
		border-block-end-color: var(--sift-accent);
	}

	/* The warning colour, the one colour meaning "this wants a person", as a glyph so it is said in
	   shape as well as colour. On the middle of the word rather than its baseline. */
	.attention {
		margin-inline-start: var(--space-2);
		color: var(--sift-warn);
		vertical-align: middle;
	}

	/* Quieter and smaller than the word it follows. Tabular figures so the row does not shuffle
	   sideways as counts land one after another. */
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

	/* PANES: the strip, then the window the panes travel behind. */
	.panes {
		display: grid;
		grid-template-rows: auto minmax(0, 1fr);
		gap: var(--space-2);
		min-block-size: 0;
	}

	/*
	 * Equal columns so the strip does not shuffle as counts land, and so the rule's travel is
	 * arithmetic: a `1fr` each means a tab is the width of its share of the panel whatever number
	 * turns up in it.
	 */
	.track {
		position: relative;
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: 1fr;
		gap: var(--space-3);
	}

	/* A button, so the reset a bare button needs, and the same held rule as every other tab. */
	.track .tab {
		min-inline-size: 0;
		padding: var(--space-1) 0;
		border-inline: 0;
		border-block-start: 0;
		background: transparent;
		text-align: center;
		cursor: pointer;
	}

	/* The rule is drawn by `.rule`, which travels; the one showing only takes the full ink. */
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

	/*
	 * The travelling rule, worked out rather than measured: one column is the track less the gaps,
	 * shared out, and the travel is that column plus one gap per tab. The rail's own pace (`--pace`),
	 * so the rule is under Media when Media's pane is in the window.
	 */
	.rule {
		position: absolute;
		inset-block-end: 0;
		inset-inline-start: 0;
		/* The same 2px a current tab's word wears on a page, drawn as an edge so it is that rule. */
		border-block-end: 2px solid var(--sift-accent);
		inline-size: calc((100% - (var(--tabs) - 1) * var(--space-3)) / var(--tabs));
		translate: calc(var(--at) * (100% + var(--space-3))) 0;
		transition:
			translate var(--pace) var(--ease),
			inline-size var(--dur-fast) var(--ease);
	}

	/*
	 * SEGMENTED: the strip is a sunk track and the one showing sits on a raised segment. Inside a
	 * panel, under a quiet heading, an underline reads as a second heading rule; a track with one
	 * segment lifted says these are the positions of one switch. The words keep their ink rules.
	 */
	.segmented .track {
		gap: var(--space-1);
		padding: var(--space-1);
		border-radius: var(--radius-md);
		background: var(--sift-surface-1);
		border: 1px solid var(--sift-line);
	}

	/* Positioned so the words paint over the segment travelling under them. No rule on this look.
	   A block, because in a filter row the `.tab` is the words inside a `Pressable` and has to fill
	   it for its hover ground to be the whole position. */
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

	/* A tab not showing answers the pointer with the state layer on its own clear ground. */
	.segmented .track .tab:hover:not([data-state='active']):not(.here):not(:disabled) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
	}

	.segmented .track .tab:active:not([data-state='active']):not(.here):not(:disabled) {
		background-color: color-mix(in srgb, currentColor var(--layer-pressed), transparent);
	}

	/*
	 * The raised segment, worked out rather than measured, like the rule: one column is the track's
	 * padding box less its padding and gaps, shared out; the travel is a column plus a gap per tab.
	 */
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

	/*
	 * A SEGMENTED ROW OF GLYPHS: one small control's height and only as wide as its positions, so
	 * it sits inside a field (the search box) without the field growing round it. Each position is
	 * a square glyph's room: the track's height less its padding, and the border, which is inside
	 * the height because every box here is sized border-box.
	 */
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

	/*
	 * The wave on the glyph of the one answering, where the tab asks for it (`wave`): a gradient
	 * wider than the glyph, slid across it and clipped to the glyph's shape, so the colour appears
	 * to move through the icon rather than behind it. The gradient is `--sift-wave`, the colours the
	 * enrichment marks wear standing still; what is this look's own is that it moves. A CHILD
	 * combinator, so it names this glyph and could never reach one inside something else.
	 */
	.segmented .tab.here.wave > :global(.icon) {
		background-image: var(--sift-wave);
		background-size: 300% 100%;
		background-clip: text;
		-webkit-background-clip: text;
		color: transparent;
		--pan-to: 300%;
		animation: pan var(--dur-drift) linear infinite;
	}

	/*
	 * THE WINDOW: one panel wide, as tall as the deepest of the panes, and it clips. It grows and
	 * shrinks over the travel's duration, because a pane filling in after a fetch and the row
	 * sliding are one movement.
	 */
	.window {
		overflow: hidden;
		min-block-size: 0;
		block-size: var(--tallest, auto);
		max-block-size: 100%;
		transition: block-size var(--dur-fast) var(--ease);
	}

	/*
	 * The row of panes, moved by whole panels. One panel wide with its panes overflowing it, so
	 * `-100%` on `translate` (a percentage of the box's own width) is one panel.
	 */
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

	/* A pane that scrolls: exactly as tall as the panes that have a height of their own, given as a
	   height so the `Scroller` inside resolves against it. */
	.pane.bounded {
		block-size: var(--tallest, auto);
	}

	/* Reduced motion: the travel is movement rather than a state change, so it goes altogether, and
	   the rule and the height go with it. */
	:global(:root[data-motion='reduce']) .rail,
	:global(:root[data-motion='reduce']) .window,
	:global(:root[data-motion='reduce']) .rule,
	:global(:root[data-motion='reduce']) .pill,
	:global(:root[data-motion='reduce']) .under {
		transition: none;
	}

	/* Less motion, not less meaning: the wave stops and its colour stays, because the glyph still
	   has to say it is the one answering. */
	:global(:root[data-motion='reduce']) .segmented .tab.here.wave > :global(.icon) {
		animation: none;
		background-position: 40% 50%;
	}

	.pane:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}
</style>
