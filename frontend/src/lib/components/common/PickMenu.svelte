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
	   chevron and the surface this opens onto, and those rules are `:global` there precisely so a
	   second flyout can wear them. Only the parts this file adds (the box at the top, the two
	   arrows, the region they scroll) are dressed here. */
	/* WHY NOT BITS-UI, MORE OF IT: everything that behaves IS the library. The row that opens out,
	   the flyout, the keyboard, the hover delay and the flip to the other side are
	   `ContextMenu.Sub`; every choice is a `ContextMenu.Item`; the box is `NarrowBox` and the
	   scrolling region is `Scroller`. What is written here is which of those go where, and the one
	   thing no library has an opinion about: an arrow that scrolls while it is held. */

	/*
	 * Putting a file on something, without leaving the menu.
	 *
	 * The row opens out into the list and one pick writes immediately, by the host's `pick` and its
	 * toast; the action bar's sheet stays the tool for forty files. What this account picked lately
	 * goes in front (`$lib/search/frequent`), the rest alphabetically from a server page filtered
	 * by what is typed (`PickAsk`), with `more` said as the last line in words, never a silent
	 * ceiling. The arrows are `Scroller`'s.
	 */
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
		/** Draw the glyph solid. For the ones whose outline is mostly empty space. */
		filled?: boolean;
		/** Which record of past picks orders this list. */
		kind: FrequentKind;
		/** The plural word for what is in it: "collections", "people", "Sites". */
		plural: string;
		/**
		 * One page of what can be picked, filtered by what is typed: asked when the flyout opens and on
		 * each pause of typing (`PickAsk`).
		 */
		ask: PickAsk;
		/**
		 * Draw the list without the row that opens out into it, for a caller that IS the menu (a
		 * person's or a site's Tag button), so it takes one gesture rather than two.
		 */
		inline?: boolean;
		/**
		 * Put the files on this one: the whole write and its words, answering what LANDED, which puts
		 * a refused tick back (`PickLanded`, `choose`). `void` reads as landed, for the pickers that do
		 * not answer yet (naming a face, joining a username).
		 */
		onpick: (choice: PickChoice) => PickReply;
		/**
		 * Take the files off this one. Absent leaves every row adding only; a tick is drawn only where
		 * this and `already` both arrived, or it could not be cleared.
		 */
		onunpick?: (choice: PickChoice) => PickReply;
		/**
		 * Which of this list the files are ALREADY on, asked when the flyout opens: a question about the
		 * set (`OnAlready`), anything unnamed `none`. Absent draws no marks (the header's Tag button
		 * opens on a person, not on files).
		 */
		already?: () => Promise<Record<string, OnAlready>>;
		/**
		 * Create one under this name and pick it, where the caller allows, so filing a clip under a new
		 * collection needs no detour.
		 */
		oncreate?: (name: string) => Promise<PickChoice | null>;
		/**
		 * Which end the box somebody types in sits at. The top unless a caller says otherwise: a menu
		 * opening UPWARDS (the faces bar at the window's foot) wants the box by the trigger. One prop,
		 * not a second picker, and the markup keeps reading order, so eye and screen reader agree.
		 */
		filterAt?: 'top' | 'bottom';
		/**
		 * Something quiet at the end of a row, about that choice: the faces screen's reference-photo
		 * count, which decides between two names that read alike. A snippet the caller fills, drawn
		 * after the name and before the membership mark.
		 */
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
	/**
	 * What the files are already on, by id, and whether it was ever asked: empty until the answer lands,
	 * so no tick appears then clears. `marking` is its own flag, since the request may fail and the
	 * flyout must still add.
	 */
	let onAlready = $state<Record<string, OnAlready>>({});
	const marking = $derived(already !== undefined && onunpick !== undefined);
	/*
	 * WHAT HAS BEEN PICKED SINCE THIS FLYOUT OPENED, which is the only state a plain row can show, so a
	 * right-press run of picks is visible. Not announced: a plain row is a `menuitem`, with no checked
	 * state, and making every row a checkbox would announce "unchecked" on a list with no such state;
	 * a caller with memberships passes `already` and gets them announced.
	 */
	let picked = $state<string[]>([]);

	/*
	 * The flyout's layer and dressing are those `ContextMenuItem` gives its own, from the owning menu:
	 * without the portal it would be clipped inside the parent's scrolling box.
	 */
	const menu = theMenu();

	/*
	 * Held here so Escape in the box closes this flyout and nothing above it (the library would shut
	 * every level).
	 */
	let flyoutOpen = $state(false);

	/* A pointer resting on a neighbouring row is answered by that row even while this flyout is
	   open, as for every row that opens out. See `menu-resting.svelte.ts`. */
	let flyoutTrigger = $state<HTMLElement | null>(null);
	givesWayToARestingPointer({
		open: () => flyoutOpen,
		close: () => (flyoutOpen = false),
		trigger: () => flyoutTrigger
	});

	/*
	 * One request per pause, as `SuggestInput` asks, and a generation counter so a slow answer for
	 * "nor" never lands under "northl". Opening does not wait: it is one event, and an empty flyout
	 * reads as slow.
	 */
	let generation = 0;
	let timer: ReturnType<typeof setTimeout> | undefined;
	const DEBOUNCE_MS = 120;

	/*
	 * The order, taken once per opening and then held still, since `$lib/search/frequent` re-ranks on
	 * every pick and the list would move under the pointer while the flyout stays open.
	 */
	let order = $state<readonly Named[]>([]);

	/*
	 * A counter of its own for the marks, asked once per open, or a slow membership answer would be
	 * discarded as stale after the first keystroke.
	 */
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

	/**
	 * What the files are already on, asked once each time the flyout opens. Not after a pick, whose
	 * row this file already knows, except a write that landed only PARTLY (`settle`).
	 */
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
			/* What each remembered row is called NOW, asked beside the page: the record keeps the
			   id, and its copy of the name is not what is drawn (see `recent`). */
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

	/* The inline form exists only while its menu is open, so mounting IS opening. UNTRACKED, as in
	   `PickDialog`: `begin` reads `remembered(kind)`, which a press writes, so a tracked effect would
	   empty the box and refetch on every press. */
	$effect(() => {
		if (!inline) return;
		untrack(begin);
		return () => clearTimeout(timer);
	});

	const needle = $derived(typed.trim().toLowerCase());

	/*
	 * The rows this account has picked before, in front of everything else: filtered here (`includes`),
	 * since the record is per account, kept by ID and drawn under the name the thing has NOW (the
	 * page's row, or `$lib/entity/names-now`). A row the server does not name is not drawn.
	 */
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

	/**
	 * The page, less anything already drawn above it, in the server's `name_az` order: a window onto
	 * the whole list's order, which a local sort could only contradict.
	 */
	const rest = $derived.by(() => {
		const already = new Set(recent.map((one) => one.id));
		return page.filter((one) => !already.has(one.id));
	});

	const shown = $derived([...recent, ...rest]);
	/** The ids in front because they were picked lately, for the mark each such row wears. */
	const recentIds = $derived(new Set(recent.map((one) => one.id)));

	/* The row that makes one: where allowed, once something is typed, and only where no drawn name is
   exactly that (the page is filtered by it), or there would be two Holidays. */
	const canMake = $derived(
		oncreate !== undefined &&
			typed.trim() !== '' &&
			!shown.some((one) => one.name.toLowerCase() === needle)
	);

	/*
	 * How many times each row has been pressed in this opening, so a slow answer settles only the
	 * row's LAST press. Not reactive: nothing is drawn from it.
	 */
	const pressed = new Map<string, number>();

	/**
	 * Picking a row, which means toggling it: a full tick comes off, anything else (a half tick too) goes
	 * on. The mark moves immediately and the write's answer settles it (`settle`). The pick is
	 * recorded for the ordering only once it landed or partly landed (`verbs.landedOf`).
	 */
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
		/* A caller that says nothing (`void`) is read as landed. See `onpick`. A write that throws
		   rather than answering is refused: nothing it promised can be counted on. */
		void Promise.resolve(reply).then(
			(landed) => {
				const said = typeof landed === 'string' ? landed : 'landed';
				if (said !== 'refused') noteUse(kind, { id: choice.id, name: choice.name });
				settle(choice.id, said, { turn, mine, before, wasPicked });
			},
			() => settle(choice.id, 'refused', { turn, mine, before, wasPicked })
		);
	}

	/**
	 * Put a row's mark where the write's answer says it is: `landed` keeps it, `refused` puts back what
	 * was there, `partly` asks the host again (the one exact answer; a plain row keeps its pick). An
	 * answer for an earlier opening or press does nothing.
	 */
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

	/*
	 * Two gestures: a left press (or Enter) acts and closes, as a menu row does; a right press acts and
	 * keeps the flyout open for more. `preventDefault` on contextmenu keeps the browser's menu away and
	 * stops the library's handlers.
	 */
	function pickAndStay(event: Event, choice: PickChoice): void {
		event.preventDefault();
		event.stopPropagation();
		choose(choice);
	}

	/**
	 * Shift and a left press: a right press for a trackpad, caught in the capture phase on the list
	 * before the row's handler selects and closes.
	 */
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

	/**
	 * Which keys belong to the box, and which belong to the menu around it: text is stopped (the menu's
	 * typeahead), navigation passes except Escape, which closes only this flyout.
	 */
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

	/*
	 * Walk the highlight down into the rows by handing the menu its OWN key, so "the first row" is the
	 * menu's answer, never a second copy of it. Where the menu does not take it, nothing moves.
	 */
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
		/*
		 * Enter acts only where there is exactly one answer, never guessing (a wrong row files a clip
		 * under the wrong person). It creates only where nothing came back, preferring an existing
		 * thing. Otherwise it moves the highlight onto the rows. It keeps the flyout up: whoever is
		 * still typing filters to the next one.
		 */
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

<!-- The list itself, written once for a row's flyout and for a menu whose whole content it is. -->
<!-- Everything inside one row, written once for both kinds of row (with or without a state). -->
{#snippet body(choice: PickChoice)}
	<!-- A tag filed under the row above it is drawn a step in per level, so a branch reads as one
	     group under its parent. -->
	{#if choice.depth}
		<span class="step-in" style:--depth={choice.depth} aria-hidden="true"></span>
	{/if}
	<!-- A face, a cover, a mark, or the kind's glyph (a tag's), read before the name; a row with no
	     cover still fills the column. The address comes with the choice; nothing fetches per row. -->
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
		<!-- What it is filed under, where that is not the row above it: a tag found by typing still
		     says which branch it is on. -->
		<span class="within"><span class="unseen">in </span>{choice.within}</span>
	{/if}
	{#if recentIds.has(choice.id)}
		<!-- Why this row is in front: it was picked lately. The mark says so on hover, so the order
		     reads as a rule rather than the list being out of order. -->
		<Tooltip label="Chosen recently">
			<span class="recent" role="img" aria-label="Chosen recently"
				><Icon name="history" size={14} /></span
			>
		</Tooltip>
	{/if}
	<!-- Whatever the caller knows about this choice that the name does not say. See `hint`. -->
	{@render hint?.(choice)}
	<!-- What this selection is already on, in a column held on every row so names do not shift. Only
	     the half tick has a tooltip, on a row-height cell around the tiny bar. A plain row fills it for
	     what this opening picked (`picked`). The bare `Checkbox`: a bordered square would read as a
	     second control. -->
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
							<!-- A checkbox row, with `aria-checked` true, false or mixed from the library; a
							     left press and Enter close the menu (`pickAndStay` for the right press). -->
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
							<!-- A PLAIN row where there is no set to compare against (the header pickers):
							     no "unchecked" on a list without that state. The same two gestures. -->
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

		<!-- WHAT THE SECOND GESTURE IS, said once at the foot, with the keyboard's form: an affordance
		     nothing mentions is one nobody has. Outside the scroll, text not a row, as `.rest`. -->
		<p class="hint">Right-click or Shift+click to select more than one</p>
	</div>
{/snippet}

{#if inline}
	{@render list()}
{:else}
	<ContextMenu.Sub bind:open={flyoutOpen} onOpenChange={opened}>
		<ContextMenu.SubTrigger class="item" bind:ref={flyoutTrigger}>
			<!-- Only when there is one, as `ContextMenuItem` draws its rows: an empty glyph box put this
			     row's words a glyph's width in from every row beside it. -->
			{#if icon}<Icon name={icon} {filled} />{/if}
			<span>{label}</span>
			<!-- The chevron that says this row opens out: `ContextMenuItem`'s, the same one its own
			     rows that open out wear. -->
			<span class="arrow"><Icon name="chevron_right" size={16} /></span>
		</ContextMenu.SubTrigger>

		<ContextMenu.Portal to={menu.where() ?? undefined}>
			{#if phoneWidth.yes}
				<!-- AT A PHONE'S WIDTH THE ROWS IT OPENS ONTO ARE A SHEET TOO, over the sheet it came from:
			     a flyout beside a sheet the width of the screen opens past the screen's edge. Headed with
			     the row that opened it; a finger drawn down the head puts it back.
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

	/* The same gap, on the other side of it. The box is between the rows and the edge of the surface
	   either way round, so the spacing follows the box rather than being written per end. */
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

	/*
	 * The picture at the head of a row, one box whatever is in it: a circle, as a person is drawn,
	 * smaller than `--control-height-sm` so no row grows; the radius is here, since `Avatar` fills
	 * whatever clips it.
	 */
	.face {
		display: block;
		flex: none;
		inline-size: var(--space-6);
		block-size: var(--space-6);
		border-radius: 50%;
		overflow: hidden;
	}

	/* A row whose thing has no cover chosen. The glyph sits in the same box so the names line up,
	   and it wears the quieter ink because it says what KIND a row is rather than which one. */
	.glyph {
		display: grid;
		place-items: center;
		color: var(--sift-ink-3);
	}

	/*
	 * The last line, which says what is not on the list, in the quiet ink and the row's inset, so it
	 * reads as about the list rather than a row to pick.
	 */
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

	/* Not on a phone: a finger has no right press and no Shift, so the line names two gestures the
	   screen cannot make. */
	@media (max-width: 767px) {
		.hint {
			display: none;
		}
	}

	/*
	 * What this selection is already on, drawn by the bare `Checkbox`, in a row-height cell so the
	 * tiny bar can be pointed at. `.name` takes the slack, since the tooltip's wrapper is the flex item.
	 */
	.on-cell {
		display: grid;
		place-items: center;
		flex: none;
		inline-size: var(--space-4);
		block-size: var(--space-5);
	}

	/* A long name is cut rather than widen the flyout, and takes the slack so what follows sits at the
	   end; `min-inline-size: 0` lets it shrink below its content. */
	.name {
		flex: 1 1 auto;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	/* One step in per level of the tree: the width of the picture column, so a child's picture sits
	   under its parent's name. */
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
