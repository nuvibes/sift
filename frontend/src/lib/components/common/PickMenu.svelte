<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'PickMenu',
		category: 'composition',
		role: 'a menu row that opens out into the list of things a file can be put on',
		basis: 'bits-ui:ContextMenu',
		states: [
			'loading',
			'empty',
			'filtered',
			'nothing matches',
			'more than fit',
			'already on',
			'picked in this opening',
			'the box at the bottom'
		]
	} satisfies DesignEntry;
</script>

<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/* DRESSED BY: .item, .arrow, .ui-menu. `ContextMenuItem` and `ContextMenu` own the row, its
	chevron and the surface; only this file's own parts are dressed here. */
	/* WHY NOT BITS-UI, MORE OF IT: everything that behaves IS the library. The row that opens out,
	the flyout and the keyboard are `ContextMenu.Sub`; here is only which go where. */

	/* Putting a file on something without leaving the menu: one pick writes immediately; recent picks
	 * first, the rest a server page filtered by what is typed, `more` said in words. */
	import { untrack, type Snippet } from 'svelte';
	import { ContextMenu } from 'bits-ui';
	import Icon from '$lib/components/Icon.svelte';
	import Empty from './Empty.svelte';
	import MarkedText from './MarkedText.svelte';
	import NarrowBox from './NarrowBox.svelte';
	import Scroller from './Scroller.svelte';
	import Skeleton from './Skeleton.svelte';
	import Avatar from './Avatar.svelte';
	import Checkbox from './Checkbox.svelte';
	import Tooltip from './Tooltip.svelte';
	import { theMenu } from './menu-portal';
	import { givesWayToARestingPointer } from './menu-resting.svelte';
	import { phoneWidth } from './phone-width.svelte';
	import { strokes } from '$lib/components/player/swipe';
	import {
		noteUse,
		recallPicks,
		remembered,
		type FrequentKind,
		type Named
	} from '$lib/search/frequent.svelte';
	import { askNames, nameNow } from '$lib/entity/names-now.svelte';
	import { PARTLY_APPLIED } from './pick-labels';
	import type { OnAlready, PickAsk, PickChoice, PickLanded } from './verbs';
	import type { IconName } from '$lib/design/icons';

	/** What a write hands back: what landed, now or later, or nothing said (read as landed). */
	type PickReply = PickLanded | void | Promise<PickLanded | void>;

	interface Props {
		/** What the row says: the OBJECT, as the verb list declares it: "Collection", "Person". */
		label: string;
		icon: IconName;
		filled?: boolean;
		/** Which record of past picks orders this list. */
		kind: FrequentKind;
		/** The plural word for what is in it: "collections", "people", "Sites". */
		plural: string;
		/** One page of choices, asked on opening and each pause of typing. */
		ask: PickAsk;
		/** The list without the row opening into it, for a caller that is the menu. */
		inline?: boolean;
		/** Put the files on this one, answering what landed; `void` reads as landed. */
		onpick: (choice: PickChoice) => PickReply;
		/** Take the files off; a tick is drawn only where this and `already` arrived. */
		onunpick?: (choice: PickChoice) => PickReply;
		/** Which of this list the files are already on, asked on opening. */
		already?: () => Promise<Record<string, OnAlready>>;
		/** Create one under this name and pick it, where allowed. */
		oncreate?: (name: string) => Promise<PickChoice | null>;
		/** Which end the box sits at; the bottom for a menu opening upwards. */
		filterAt?: 'top' | 'bottom';
		/** A quiet note at the end of a row about that choice. */
		hint?: Snippet<[PickChoice]>;
	}

	let {
		label,
		icon,
		filled = false,
		kind,
		plural,
		ask,
		inline = false,
		onpick,
		onunpick,
		already,
		oncreate,
		filterAt = 'top',
		hint
	}: Props = $props();

	let typed = $state('');
	let making = $state(false);
	/** The page in hand, and how many the server could not fit into it. */
	let page = $state<PickChoice[]>([]);
	let more = $state(0);
	let loading = $state(false);
	/** Whether an answer has landed at all yet, so "nothing here" is never said over a blank. */
	let answered = $state(false);
	/** What the files are already on, empty until the answer lands. */
	let onAlready = $state<Record<string, OnAlready>>({});
	const marking = $derived(already !== undefined && onunpick !== undefined);
	/* Picked since this flyout opened: the only state a plain row shows; not announced. */
	let picked = $state<string[]>([]);

	/* The flyout's layer and dressing, from the owning menu. */
	const menu = theMenu();

	/* Held so Escape in the box closes only this flyout. */
	let flyoutOpen = $state(false);

	/* A resting pointer on a neighbour is answered by it (menu-resting.svelte.ts). */
	let flyoutTrigger = $state<HTMLElement | null>(null);
	givesWayToARestingPointer({
		open: () => flyoutOpen,
		close: () => (flyoutOpen = false),
		trigger: () => flyoutTrigger
	});

	/* One request per pause, a generation counter dropping stale answers. */
	let generation = 0;
	let timer: ReturnType<typeof setTimeout> | undefined;
	const DEBOUNCE_MS = 120;

	/* The order, taken once per opening, so rows do not move under the pointer. */
	let order = $state<readonly Named[]>([]);

	/* The marks' own counter, asked once per open. */
	let opening = 0;

	async function fetchPage(wanted: string): Promise<void> {
		const mine = ++generation;
		loading = true;
		try {
			const answer = await ask(wanted);
			if (mine !== generation) return;
			page = answer.choices;
			more = Math.max(0, answer.more);
			answered = true;
		} catch {
			// A page that cannot be fetched is an empty list, never an error inside a menu.
			if (mine !== generation) return;
			page = [];
			more = 0;
			answered = true;
		} finally {
			if (mine === generation) loading = false;
		}
	}

	function askLater(wanted: string): void {
		clearTimeout(timer);
		timer = setTimeout(() => void fetchPage(wanted), DEBOUNCE_MS);
	}

	/** What the files are on, asked once per opening, and again after a partial write. */
	async function recallAlready(mine: number): Promise<void> {
		if (!already) return;
		try {
			const answer = await already();
			if (mine !== opening) return;
			onAlready = answer;
		} catch {
			// No marks rather than wrong ones. Adding still works, which is what the flyout is for.
			if (mine !== opening) return;
			onAlready = {};
		}
	}

	/** Everything the flyout needs before it can draw a row in the right order. */
	function begin(): void {
		clearTimeout(timer);
		const mine = ++opening;
		typed = '';
		page = [];
		more = 0;
		answered = false;
		onAlready = {};
		picked = [];
		/* The record is READ before the first ordering, or the list would jump; neither waits on the other. */
		order = remembered(kind);
		void recallPicks().then(() => {
			if (mine !== opening) return;
			order = remembered(kind);
			/* What each remembered row is called now. */
			void askNames(
				kind,
				order.map((one) => one.id)
			);
		});
		void fetchPage('');
		void recallAlready(mine);
	}

	function opened(open: boolean): void {
		if (!open) return;
		begin();
	}

	/* Mounting is opening for the inline form; untracked, as in PickDialog. */
	$effect(() => {
		if (!inline) return;
		untrack(begin);
		return () => clearTimeout(timer);
	});

	const needle = $derived(typed.trim().toLowerCase());

	/* Recent picks first, by id, under the name each has now. */
	const recent = $derived.by(() => {
		const onPage = new Map(page.map((one) => [one.id, one]));
		const rows: PickChoice[] = [];
		for (const one of order) {
			const known = onPage.get(one.id);
			const called = known ? known.name : nameNow(kind, one.id);
			const row = known ?? (called ? { id: one.id, name: called } : undefined);
			if (!row) continue;
			if (needle !== '' && !row.name.toLowerCase().includes(needle)) continue;
			rows.push(row);
		}
		return rows;
	});

	/** The page less the rows above it, in the server's `name_az` order. */
	const rest = $derived.by(() => {
		const already = new Set(recent.map((one) => one.id));
		return page.filter((one) => !already.has(one.id));
	});

	const shown = $derived([...recent, ...rest]);
	/** The ids in front because they were picked lately, for the mark each such row wears. */
	const recentIds = $derived(new Set(recent.map((one) => one.id)));

	/* Create only where no drawn name is exactly what was typed. */
	const canMake = $derived(
		oncreate !== undefined &&
			typed.trim() !== '' &&
			!shown.some((one) => one.name.toLowerCase() === needle)
	);

	/* Presses per row this opening, so only the last press's answer settles it. */
	const pressed = new Map<string, number>();

	/** Picking toggles a row: the mark moves immediately and the write's answer settles it. */
	function choose(choice: PickChoice): void {
		const before = onAlready[choice.id] ?? 'none';
		const wasPicked = picked.includes(choice.id);
		const off = marking && before === 'all';
		onAlready = { ...onAlready, [choice.id]: off ? 'none' : 'all' };
		if (!wasPicked) picked = [...picked, choice.id];
		const turn = (pressed.get(choice.id) ?? 0) + 1;
		pressed.set(choice.id, turn);
		const mine = opening;
		const reply = off ? onunpick?.(choice) : onpick(choice);
		/* `void` is landed; a throw is refused. */
		void Promise.resolve(reply).then(
			(landed) => {
				const said = typeof landed === 'string' ? landed : 'landed';
				if (said !== 'refused') noteUse(kind, { id: choice.id, name: choice.name });
				settle(choice.id, said, { turn, mine, before, wasPicked });
			},
			() => settle(choice.id, 'refused', { turn, mine, before, wasPicked })
		);
	}

	/** Put a row's mark where the answer says: kept, put back, or asked again when partly. */
	function settle(
		id: string,
		landed: PickLanded,
		at: { turn: number; mine: number; before: OnAlready; wasPicked: boolean }
	): void {
		if (at.mine !== opening || pressed.get(id) !== at.turn) return;
		if (landed === 'landed') return;
		if (landed === 'refused') {
			if (marking) onAlready = { ...onAlready, [id]: at.before };
			if (!at.wasPicked) picked = picked.filter((one) => one !== id);
			return;
		}
		if (marking) void recallAlready(opening);
	}

	/* A left press acts and closes; a right press acts and stays open. */
	function pickAndStay(event: Event, choice: PickChoice): void {
		event.preventDefault();
		event.stopPropagation();
		choose(choice);
	}

	/** Shift and a left press, a right press for a trackpad. */
	function stayingClick(event: MouseEvent): void {
		if (!event.shiftKey) return;
		const row = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-choice-id]');
		const choice = row ? shown.find((one) => one.id === row.dataset.choiceId) : undefined;
		if (choice) pickAndStay(event, choice);
	}

	async function make(): Promise<void> {
		const name = typed.trim();
		if (!oncreate || name === '' || making) return;
		making = true;
		try {
			const made = await oncreate(name);
			if (made) choose(made);
		} finally {
			making = false;
		}
	}

	/** The box keeps text keys; navigation passes, Escape closes only this flyout. */
	const NAVIGATION = new Set([
		'Escape',
		'Tab',
		'ArrowUp',
		'ArrowDown',
		'ArrowLeft',
		'ArrowRight',
		'Home',
		'End',
		'PageUp',
		'PageDown'
	]);

	/* Into the rows by handing the menu its own key. */
	function intoTheRows(from: EventTarget | null): void {
		if (!(from instanceof HTMLElement)) return;
		/* Towards the rows, which is UP when the box is under them. */
		const key = filterAt === 'bottom' ? 'ArrowUp' : 'ArrowDown';
		from.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
	}

	function keys(event: KeyboardEvent): void {
		if (event.key === 'Escape' && !inline) {
			event.preventDefault();
			event.stopPropagation();
			flyoutOpen = false;
			return;
		}
		if (NAVIGATION.has(event.key)) return;
		event.stopPropagation();
		if (event.key !== 'Enter') return;
		/* Enter acts only on a single answer and creates only where nothing came back. */
		event.preventDefault();
		if (shown.length === 1 && more === 0) {
			choose(shown[0]);
			return;
		}
		if (shown.length === 0 && canMake) {
			void make();
			return;
		}
		intoTheRows(event.currentTarget);
	}
</script>

<!-- Everything inside one row, written once for both kinds of row (with or without a state). -->
{#snippet body(choice: PickChoice)}
	<!-- A tag a step in per level. -->
	{#if choice.depth}
		<span class="step-in" style:--depth={choice.depth} aria-hidden="true"></span>
	{/if}
	<!-- The picture before the name; nothing fetches per row. -->
	{#if choice.picture}
		<span class="face">
			<Avatar
				src={choice.picture.src}
				instead={choice.picture.instead}
				name={choice.name}
				mark={choice.picture.mark ?? false}
				lazy
			/>
		</span>
	{:else}
		<span class="face glyph"><Icon name={icon} {filled} size={16} /></span>
	{/if}
	<span class="name"><MarkedText text={choice.name} {typed} /></span>
	{#if choice.within}
		<!-- Its branch, where that is not the row above. -->
		<span class="within"><span class="unseen">in </span>{choice.within}</span>
	{/if}
	{#if recentIds.has(choice.id)}
		<!-- Picked lately, said on hover. -->
		<Tooltip label="Chosen recently">
			<span class="recent" role="img" aria-label="Chosen recently"
				><Icon name="history" size={14} /></span
			>
		</Tooltip>
	{/if}
	<!-- Whatever the caller knows about this choice that the name does not say. See `hint`. -->
	{@render hint?.(choice)}
	<!-- The membership mark, a column held on every row; the bare Checkbox. -->
	{#if marking}
		{@const on = onAlready[choice.id] ?? 'none'}
		{#if on === 'some'}
			<Tooltip label={PARTLY_APPLIED}>
				<span class="on-cell"><Checkbox bare state="partly" /></span>
			</Tooltip>
		{:else}
			<span class="on-cell">
				<Checkbox bare state={on === 'all' ? 'on' : 'off'} />
			</span>
		{/if}
	{:else}
		<span class="on-cell">
			<Checkbox bare state={picked.includes(choice.id) ? 'on' : 'off'} />
		</span>
	{/if}
{/snippet}

<!-- The box somebody types in, written once and mounted at whichever end `filterAt` asks. -->
{#snippet narrowing()}
	<div class="narrowing" class:under={filterAt === 'bottom'}>
		<NarrowBox
			bind:value={typed}
			label="Filter the {plural} list"
			onkeydown={keys}
			oninput={(event) => askLater(event.currentTarget.value)}
		/>
	</div>
{/snippet}

{#snippet list()}
	<!-- ONE child of the menu surface, whose single bounded track (`ContextMenu`) must hold it all. -->
	<div class="pick">
		{#if filterAt === 'top'}{@render narrowing()}{/if}

		<div class="list" onclickcapture={stayingClick}>
			<Scroller arrows>
				{#if !answered && shown.length === 0}
					<div class="waiting"><Skeleton lines={4} /></div>
				{:else if answered && shown.length === 0 && needle === ''}
					<Empty scope="block">No {plural} yet.</Empty>
				{:else}
					{#each shown as choice (choice.id)}
						{#if marking}
							<!-- A checkbox row; left press and Enter close. -->
							<ContextMenu.CheckboxItem
								class="item"
								checked={(onAlready[choice.id] ?? 'none') === 'all'}
								indeterminate={(onAlready[choice.id] ?? 'none') === 'some'}
								onSelect={() => choose(choice)}
								oncontextmenu={(event: MouseEvent) => pickAndStay(event, choice)}
								data-choice-id={choice.id}
								textValue={choice.name}
							>
								{@render body(choice)}
							</ContextMenu.CheckboxItem>
						{:else}
							<!-- A plain row where there is no set to compare against. -->
							<ContextMenu.Item
								class="item"
								onSelect={() => choose(choice)}
								oncontextmenu={(event: MouseEvent) => pickAndStay(event, choice)}
								data-choice-id={choice.id}
								textValue={choice.name}
							>
								{@render body(choice)}
							</ContextMenu.Item>
						{/if}
					{/each}

					<!-- What did not fit, said as text, not a row: the answer is to keep typing. -->
					{#if more > 0}
						<p class="rest">{counted(more)} more &#8212; keep typing to filter</p>
					{/if}

					{#if canMake}
						<!-- "Create", never "Make". The same two gestures as every row above. -->
						<ContextMenu.Item
							class="item"
							onSelect={() => void make()}
							oncontextmenu={(event: MouseEvent) => {
								event.preventDefault();
								event.stopPropagation();
								void make();
							}}
						>
							<Icon name="add" size={16} />
							<span class="name">Create {typed.trim()}</span>
						</ContextMenu.Item>
					{:else if shown.length === 0}
						<Empty scope="block">Nothing here is called that.</Empty>
					{/if}
				{/if}
			</Scroller>
		</div>

		{#if filterAt === 'bottom'}{@render narrowing()}{/if}

		<!-- The second gesture, said once at the foot. -->
		<p class="hint">Right-click or Shift+click to select more than one</p>
	</div>
{/snippet}

{#if inline}
	{@render list()}
{:else}
	<ContextMenu.Sub bind:open={flyoutOpen} onOpenChange={opened}>
		<ContextMenu.SubTrigger class="item" bind:ref={flyoutTrigger}>
			<!-- The glyph only when there is one. -->
			{#if icon}<Icon name={icon} {filled} />{/if}
			<span>{label}</span>
			<!-- ContextMenuItem's chevron. -->
			<span class="arrow"><Icon name="chevron_right" size={16} /></span>
		</ContextMenu.SubTrigger>

		<ContextMenu.Portal to={menu.where() ?? undefined}>
			{#if phoneWidth.yes}
				<!-- At a phone's width its rows are a sheet over the sheet, put back by a stroke down its head.
				DRESSED BY: .menu-sheet (ContextMenu styles the sheet every menu is at a phone width)
				DRESSED BY: .menu-sheet-head (ContextMenu styles the sheet's head beside the sheet) -->
				<ContextMenu.SubContentStatic class="ui-menu menu-sheet">
					<p
						class="menu-sheet-head"
						aria-hidden="true"
						{@attach strokes(() => ({
							live: flyoutOpen,
							on: { down: () => (flyoutOpen = false) }
						}))}
					>
						{label}
					</p>
					{@render list()}
				</ContextMenu.SubContentStatic>
			{:else}
				<ContextMenu.SubContent class="ui-menu">
					{@render list()}
				</ContextMenu.SubContent>
			{/if}
		</ContextMenu.Portal>
	</ContextMenu.Sub>
{/if}

<style>
	/* A column: the box, the arrows, and the rows taking the rest; flex, since two children come and go. */
	.pick {
		display: flex;
		flex-direction: column;
		min-block-size: 0;
	}

	/* The box wears the surface's own inset; only the gap to the first row is added. */
	.narrowing {
		padding-block-end: var(--space-1);
	}

	/* The same gap on the other side. */
	.narrowing.under {
		padding-block: var(--space-1) 0;
	}

	/* The one part that scrolls, allowed to shrink below its content so the ceiling bounds it. */
	.list {
		flex: 1 1 auto;
		min-block-size: 0;
	}

	/* Eight rows at most, so a roster of hundreds never pushes the box off the window. */
	.list :global(.scroll-root) {
		max-block-size: calc(var(--control-height-sm) * 8);
	}

	/* The rows on their way. The inset is the row's, so the bones land where the names will. */
	.waiting {
		padding: var(--menu-row-padding);
	}

	/* The picture, a circle smaller than a small control so no row grows. */
	.face {
		display: block;
		flex: none;
		inline-size: var(--space-6);
		block-size: var(--space-6);
		border-radius: 50%;
		overflow: hidden;
	}

	/* No cover: the kind's glyph in the same box, quieter. */
	.glyph {
		display: grid;
		place-items: center;
		color: var(--sift-ink-3);
	}

	/* What is not on the list, quiet. */
	.rest {
		margin: 0;
		padding: var(--menu-row-padding);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* The line at the foot that says what a right press does, as `.rest`. */
	.hint {
		margin: 0;
		padding: var(--menu-row-padding);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* Not on a phone, which has no right press or Shift. */
	@media (max-width: 767px) {
		.hint {
			display: none;
		}
	}

	/* The membership mark in a row-height cell, so the tiny bar can be pointed at. */
	.on-cell {
		display: grid;
		place-items: center;
		flex: none;
		inline-size: var(--space-4);
		block-size: var(--space-5);
	}

	/* A long name is cut, taking the slack. */
	.name {
		flex: 1 1 auto;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	/* One step in per level: the picture column's width. */
	.step-in {
		flex: none;
		inline-size: calc(var(--depth) * var(--space-5));
	}
	/* The branch a row is on, quiet and after the name, cut before the name is. */
	.within {
		flex: 0 1 auto;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		margin-inline-start: var(--space-2);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}
	/* The recency mark, quiet and beside the name, never in the way of the name's ellipsis. */
	.recent {
		display: inline-flex;
		flex: 0 0 auto;
		margin-inline-start: var(--space-1);
		color: var(--sift-ink-3);
	}
</style>
