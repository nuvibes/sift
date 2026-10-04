<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'PickDialog',
		category: 'surface',
		role: 'a dialog that offers a list to pick from and one finishing act',
		basis: 'composes:Modal,NarrowBox',
		states: ['open']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/* WHY NOT BITS-UI: the dialog behaviour IS bits-ui's: one layer down, in Modal, which this composes. */
	/*
	 * Pick things out of a list, to put a selection of files onto them.
	 *
	 * Three verbs want exactly this and differ only in what the list holds: Tag, and the two under
	 * Add to: a collection and a person. Written three times it would be three boxes that filter slightly
	 * differently, three answers to what Enter does, and three places to fix the one that scrolls
	 * badly with four hundred rows.
	 *
	 * SEVERAL, not one. A sheet that applied a single answer the moment a row was pressed would
	 * make filing a clip under two people: open, pick, wait, find the clip again, open, pick. So a
	 * row is ticked and the sheet is finished with a button, and the number of things being put
	 * on is the caller's business and not the box's, and all three of them can take a list, which
	 * every one of the three endpoints behind them already could.
	 *
	 * Making a new one is offered where making one is the caller's to allow. Typing a name nothing
	 * matches and pressing Enter creates it and adds it to what is ticked, which is what the tag
	 * editor already does: somebody filing a clip under a person who is not in the library yet
	 * should not have to leave, make the person, come back and find the clip again.
	 */
	import { untrack, type Snippet } from 'svelte';
	import { SvelteMap } from 'svelte/reactivity';
	import { ApiError } from '$lib/api/client';
	import { toasts } from '$lib/shell/toasts.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Modal from './Modal.svelte';
	import NarrowBox from './NarrowBox.svelte';
	import Scroller from './Scroller.svelte';
	import Avatar from './Avatar.svelte';
	import Checkbox from './Checkbox.svelte';
	import Tooltip from './Tooltip.svelte';
	import {
		PICK_PAGE,
		noteUse,
		recallPicks,
		remembered,
		type FrequentKind,
		type Named
	} from '$lib/search/frequent.svelte';
	import { byName } from '$lib/search/name-order';
	import { PARTLY_APPLIED } from './pick-labels';
	import type { OnAlready, PickChoice } from './verbs';

	export interface Choice {
		id: string;
		name: string;
	}

	interface Props {
		open?: boolean;
		/** The question, named. "Tag 12 files", never a bare "Pick one". */
		title: string;
		/** What is being acted on, in a sentence under the title. */
		subject: string;
		/**
		 * Everything that can be picked. Filtered here as somebody types; not re-fetched.
		 *
		 * A row may carry the picture the thing is drawn by everywhere else. See `PickChoice`, and
		 * see the rows below for when it is drawn. A caller with nothing but names may go on handing
		 * over `Choice`s, which is what the same type extending the other is for: a list built for
		 * the flyout is a list this can draw, and now the other way round as well.
		 */
		choices: readonly PickChoice[];
		/** The word for the button that makes a new one. Absent means no making. */
		createLabel?: string;
		placeholder?: string;
		/**
		 * The word on the button that finishes, given how many rows are being put ON and how many
		 * are being taken OFF.
		 *
		 * Written by the caller because only the caller knows the verb: "Tag them", "Add to 2
		 * collections". A generic OK would be the one control on the sheet that does not say what it
		 * is about to do.
		 *
		 * The second number goes with the marks (see `already`). A sheet that can clear a tick can
		 * finish by taking something off, and a button that said "Add to 2 collections" while it was
		 * about to remove one would be the one control on the sheet that LIES about what it is about
		 * to do, which is worse than the generic OK the first sentence refuses. Callers that never
		 * pass `already` are handed a zero and may go on taking one argument.
		 */
		confirmLabel: (on: number, off: number) => string;
		/** Everything being put on, once. Never called with an empty list. */
		onpick: (choices: Choice[]) => void;
		/**
		 * Which record of past picks this list is ordered by, and written to as rows are pressed.
		 *
		 * Absent means the sheet neither reads nor writes the memory: for the callers whose list is
		 * not one of the five kinds `$lib/search/frequent` counts (merging two people, naming a face).
		 */
		kind?: FrequentKind;
		/**
		 * Which of this list the files are already on, asked each time the sheet opens.
		 *
		 * The same question `PickMenu` asks, answered by the caller because it is about the whole
		 * selection. Any row the answer does not name is `none`. Absent means no marks: every row
		 * starts blank and the sheet only adds, which suits a sheet opened on one thing rather than
		 * a selection.
		 */
		already?: () => Promise<Record<string, OnAlready>>;
		/**
		 * Take the files off these, once.
		 *
		 * Read together with `already`: a row that can show a tick and cannot clear it is a control
		 * that lies about what pressing it does, so the marks are only ever drawn where BOTH of these
		 * arrived. That is `PickMenu`'s rule, applied here because it is the same rule and not
		 * because the two files happen to look alike.
		 */
		onunpick?: (choices: Choice[]) => void;
		/**
		 * More choices for what was typed, from wherever the whole list lives.
		 *
		 * `choices` is what the screen already holds, and for people that is one page of hundreds.
		 * Given, this is asked as the text changes and its answers are offered beside the held
		 * ones, so a name past the first page is found by typing it. It answers rows with their
		 * pictures, like every other list here, so a row found by typing has its face.
		 */
		onsearch?: (typed: string) => Promise<PickChoice[]>;
		/**
		 * Exactly one row may be ticked: ticking another MOVES the tick rather than adding to it.
		 *
		 * For the sheets whose question has one answer: which of these people to keep, who to
		 * merge somebody into. Without it the sheet takes ticks like any other and the caller reads
		 * the first of them, so ticking two says one thing on screen and does another; there is no
		 * sentence that describes folding a person into two people.
		 */
		single?: boolean;
		/**
		 * The row that is already ticked when the sheet opens.
		 *
		 * A default answer, not a suggestion: the sheet opens ready to be confirmed and the press is
		 * a correction. Only where the caller has a reason to prefer one: the merge sheet defaults
		 * to whoever holds the most files, because keeping the biggest is what moves the least.
		 */
		preset?: string;
		/** Make one under this name. Only called when `createLabel` is set; it comes back ticked. */
		oncreate?: (name: string) => Promise<Choice | null> | void;
		/** A choice above the box that changes what a tick MEANS; the caller moves `restart` with it. */
		head?: Snippet;
		/** Changed to start the sheet over while it is up: box emptied, `already` asked again. */
		restart?: unknown;
	}

	let {
		open = $bindable(false),
		title,
		subject,
		choices,
		createLabel,
		placeholder = 'Search',
		confirmLabel,
		onpick,
		oncreate,
		onsearch,
		kind,
		already,
		onunpick,
		single = false,
		preset,
		head,
		restart
	}: Props = $props();

	let typed = $state('');
	/*
	 * What each row says, in two halves: what was true when the sheet opened, and what a press has
	 * said it should become.
	 *
	 * The sheet draws "some of them" for a row that carries part of the selection, as the menu's
	 * flyout does. Two halves because the difference is what is written: a row already on and still
	 * on is not sent again, and a row whose full tick is cleared is taken off. A single set of
	 * ticks cannot tell those apart.
	 *
	 * A `SvelteMap` rather than a plain one in `$state`: a plain Map is not deeply reactive, so
	 * writes to it would change nothing on screen.
	 */
	let marks = $state<Record<string, OnAlready>>({});
	const wanted = new SvelteMap<string, OnAlready>();

	/* Marks are drawn only where the caller can answer BOTH questions. See `onunpick`. */
	const marking = $derived(already !== undefined && onunpick !== undefined);

	/** What a row says right now: what a press made it, or what it was found to be. */
	function stateOf(id: string): OnAlready {
		return wanted.get(id) ?? marks[id] ?? 'none';
	}

	/* THE ORDER, TAKEN ONCE PER OPENING AND THEN HELD STILL: the same snapshot `PickMenu` takes,
	   for the same reason: read live, the list would rearrange itself under the pointer as rows were
	   ticked, and the second click would land on whatever slid into the first one's place. */
	let order = $state<readonly Named[]>([]);
	/** Anything made from here, so a brand-new tag can be ticked before it is in `choices`. */
	let made = $state<PickChoice[]>([]);
	let making = $state(false);

	/** Which opening an answer belongs to, so a slow one cannot land on the next sitting. */
	let opening = 0;

	/* Cleared on the way in rather than on the way out. A box still holding the last search while
	   the sheet fades away is the previous question visibly hanging around, and clearing it on close
	   fights the exit animation.

	   And ASKED on the way in, for the same reason `PickMenu` asks on each open: what the files are
	   on is the one thing that can have changed since the sheet was last up, and it is the only
	   moment anything else could have changed it.

	   Named `begin`, and called the way `PickMenu` calls its own: one shape for both pickers, so the
	   rule below is read once and applies to each. */
	function begin(): void {
		const mine = ++opening;
		typed = '';
		wanted.clear();
		made = [];
		marks = {};
		/* Asked only where the marks will be DRAWN. See `marking`. Asked regardless, a caller that
		   handed in `already` and no way back would open the sheet with a full tick on a row nobody
		   could clear, which is the exact control-that-lies this pairing exists to refuse. */
		if (marking && already) {
			void already()
				.then((answer) => {
					if (mine === opening) marks = answer;
				})
				.catch(() => {
					// No marks rather than wrong ones. Ticking still works, which is what the sheet is
					// for, and a row nothing is known about simply starts blank.
					if (mine === opening) marks = {};
				});
		}
		/* The default answer, ticked before anything is drawn so the sheet opens ready to be
		   confirmed. After the clear above, or the reset would take it straight off again. */
		if (preset) wanted.set(preset, 'all');
		if (kind) {
			/* The record has to have been READ before the first ordering or the list draws in the
			   caller's order and then jumps. Both are asked for on the way in. */
			order = remembered(kind);
			void recallPicks().then(() => {
				if (mine === opening) order = remembered(kind);
			});
		}
	}

	/*
	 * One opening, one reset, and `open` is the only thing that may decide there has been one.
	 *
	 * Everything `begin` reads would otherwise be a dependency of this effect, and one of those
	 * reads is `remembered(kind)`, state in `$lib/search/frequent` that a press writes (`toggle` records
	 * the pick before it moves the tick). Tracked, every press would re-run the reset, emptying the
	 * box and clearing the ticks.
	 *
	 * `untrack` over the whole of `begin`, because `open` is a prop the caller writes and this
	 * effect is the opening; untracking all of it states the rule, so nothing added to the reset
	 * later can bring it back round. The record in `$lib/search/frequent` stays reactive for its other
	 * readers.
	 */
	$effect(() => {
		if (!open) return;
		/* The one other thing that may decide there has been an opening. See `restart`. */
		void restart;
		untrack(begin);
	});

	/**
	 * Everything that can be ticked: what the caller supplied, plus anything made here.
	 *
	 * Anything made here that has SINCE arrived in the caller's own list is dropped from this side
	 * of the join, and that is not tidiness. Every store behind this sheet puts a newly made row
	 * straight into the list it hands back, so for the moment after a make, the same row is in
	 * both halves. The list is keyed by id, and two rows under one key is not a duplicate on screen:
	 * it is an error that takes the sheet down with it, and the pick about to be confirmed with it.
	 */
	/* What the server answered for the text as it stands. Asked a beat after typing stops, and an
	   answer to an older question is dropped rather than drawn over a newer one. */
	let found = $state<PickChoice[]>([]);
	$effect(() => {
		const asked = typed.trim();
		if (!onsearch || !asked) {
			found = [];
			return;
		}
		const timer = setTimeout(() => {
			void onsearch(asked).then((answer) => {
				if (typed.trim() === asked) found = answer;
			});
		}, 150);
		return () => clearTimeout(timer);
	});

	const everything = $derived([
		...made.filter((one) => !choices.some((choice) => choice.id === one.id)),
		...choices,
		...found.filter(
			(one) => !choices.some((choice) => choice.id === one.id) && !made.some((m) => m.id === one.id)
		)
	]);

	/*
	 * What is drawn: everything that matches what was typed, with the rows this account reaches for
	 * in front.
	 *
	 * The same memory the menu's flyout orders by, so picking in one is felt in the other. The
	 * remembered rows keep the memory's order (the last five picked, newest first) and everything
	 * under them is alphabetical, by the one comparator `$lib/search/name-order` holds. Decided here
	 * rather than taken from the caller, whose list is in the wall's order (People, Sites and Tags
	 * open largest first). A sheet without a `kind` (merging two people) keeps the caller's order.
	 */
	const matching = $derived.by(() => {
		const needle = typed.trim().toLowerCase();
		const pool =
			needle === ''
				? everything
				: everything.filter((choice) => choice.name.toLowerCase().includes(needle));
		const rank = new Map(order.map((one, at) => [one.id, at] as const));
		const recent = pool
			.filter((choice) => rank.has(choice.id))
			.sort((one, other) => rank.get(one.id)! - rank.get(other.id)!);
		const rest = pool.filter((choice) => !rank.has(choice.id));
		return [...recent, ...(kind ? [...rest].sort(byName) : rest)];
	});

	/** The ids in front because they were picked lately, for the mark each such row wears. */
	const recentIds = $derived(new Set(order.map((one) => one.id)));

	/**
	 * Whether these rows are drawn with a picture at the head of each.
	 *
	 * Either answer is about the WHOLE list, never the row: a column where some rows have a face
	 * and some begin at the name is a ragged list nobody can scan. So one picture anywhere turns
	 * the column on, and the rows without one fall to a letter.
	 *
	 * `kind` still turns it on by itself, for the five sheets that name one: their rows are
	 * things the app keeps walls of, and a wall is read by its
	 * pictures.
	 */
	const faces = $derived(kind !== undefined || everything.some((one) => one.picture !== undefined));

	/* Whether what was typed is a name nothing already has. Compared case-insensitively, or typing a
	   name that differs only in capitals makes a second one nobody can tell from the first. */
	const isNew = $derived(
		typed.trim() !== '' &&
			!everything.some((choice) => choice.name.toLowerCase() === typed.trim().toLowerCase())
	);

	/*
	 * Pressing a row toggles it, the same rule the flyout applies.
	 *
	 * A row showing a full tick is cleared; anything else is filled, including a half tick, since
	 * pressing it means "all of them"; the other direction would take some files out while leaving
	 * the rest in, which nobody pressed for.
	 *
	 * The pick is remembered either way, immediately: clearing a row is reaching for it as
	 * deliberately as filling one, often to put it back a moment later. Recorded on the press and
	 * not on the confirm, because a sheet opened, ticked and cancelled is still one where somebody
	 * went looking for that row.
	 */
	function toggle(choice: PickChoice) {
		// A row that cannot be chosen here says why on itself, and a press ticks nothing.
		if (choice.refused) return;
		if (kind) noteUse(kind, choice);
		const next = stateOf(choice.id) === 'all' ? 'none' : 'all';
		/* One answer only: the tick MOVES. Anything else showing a full tick is cleared first, so
		   what the sheet shows and what `finish` sends are the same single row. See `single`. */
		if (single && next === 'all')
			for (const other of everything)
				if (other.id !== choice.id && stateOf(other.id) === 'all') wanted.set(other.id, 'none');
		wanted.set(choice.id, next);
	}

	/**
	 * How many rows would be put on and how many taken off, if the button were pressed now.
	 *
	 * The difference from what was found, not a count of ticks: a row already on every file and
	 * still ticked is not a change, and counting it would offer to do something that will not
	 * happen.
	 *
	 * Asked of `everything` rather than `wanted`, so the number the button shows and the list
	 * `finish` sends are the same list: a row ticked and then lost from the list (typing a name,
	 * picking it from what the server found, then emptying the box) must be both counted and sent,
	 * or neither.
	 */
	const changing = $derived.by(() => {
		let on = 0;
		let off = 0;
		for (const choice of everything) {
			const was = marks[choice.id] ?? 'none';
			const now = stateOf(choice.id);
			if (now === 'all' && was !== 'all') on += 1;
			else if (now === 'none' && was !== 'none') off += 1;
		}
		return { on, off };
	});

	function finish(justMade?: Choice) {
		/*
		 * `justMade` is not a convenience: without it the make goes unapplied.
		 *
		 * `everything` is `$derived` from `made`, and a derived is not recomputed synchronously the
		 * instant its source is assigned, so `create` assigning `made` and then calling this in
		 * the same tick would filter a list that does not hold the new row yet, find nothing ticked
		 * that it can see, and return quietly: the sheet would close, no request would be sent,
		 * and nothing anywhere would say so.
		 */
		const pool = justMade ? [justMade, ...everything] : everything;
		// By id, because `justMade` is very often in `everything` as well: every store behind this
		// sheet puts a newly made row straight into the list it hands back. Two of one row is not a
		// duplicate in a list of ticks, it is the same thing applied twice. The FIRST occurrence is
		// kept, which is why these are guarded rather than written over: `justMade` leads the pool
		// and is the copy that is certainly current.
		const on = new Map<string, Choice>();
		const off = new Map<string, Choice>();
		for (const choice of pool) {
			const was = marks[choice.id] ?? 'none';
			const now = stateOf(choice.id);
			if (now === 'all' && was !== 'all' && !on.has(choice.id)) on.set(choice.id, choice);
			if (now === 'none' && was !== 'none' && !off.has(choice.id)) off.set(choice.id, choice);
		}
		const chosen = [...on.values()];
		const dropped = [...off.values()];
		if (chosen.length === 0 && dropped.length === 0) return;
		// Told BEFORE closed, and the ORDER is the whole of it. Closing runs the caller's `open`
		// setter, and a caller whose setter clears the thing it is acting on (the mark being
		// tagged, the files being filed) has that state pulled out from under the callback. The
		// callback would then find nothing to act on and return quietly, which looks exactly like
		// success: the sheet shuts, no request is sent, and nothing anywhere says so.
		if (chosen.length > 0) onpick(chosen);
		// Both halves before the sheet closes, for the reason above: closing runs the caller's `open`
		// setter and a caller that clears what it is acting on takes the removal's subject with it.
		if (dropped.length > 0) onunpick?.(dropped);
		open = false;
	}

	async function create() {
		const name = typed.trim();
		if (!name || !oncreate || making) return;
		making = true;
		try {
			/*
			 * Made and finished, with everything already ticked coming along.
			 *
			 * Making something is the answer to the sheet's question: somebody who typed a name
			 * nothing matched and pressed Make has said which one they mean, so waiting for a
			 * confirm press would leave the new thing made but never applied (a person created with
			 * no faces, files or cover).
			 *
			 * `finish` sends everything ticked, so a tag made after two were ticked applies all
			 * three. What is lost is making two new ones in one visit; the second can be made from
			 * the sheet reopened.
			 */
			const fresh = await oncreate(name);
			if (fresh) {
				made = [fresh, ...made];
				// Made IS reached for, so it is remembered like any other press. See `toggle`.
				if (kind) noteUse(kind, fresh);
				wanted.set(fresh.id, 'all');
				typed = '';
				finish(fresh);
			}
		} catch (failure) {
			/* A refused make has to be said. Otherwise the button goes back to its resting state,
			   the name stays in the box, and it reads as a press that did not register, so the
			   next thing somebody does is press it again. The server's own words where it gave any:
			   it is the half that knows a name is already taken. */
			toasts.show(
				failure instanceof ApiError && failure.detail
					? failure.detail
					: `${name} couldn't be created`,
				{ tone: 'error' }
			);
		} finally {
			making = false;
		}
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key !== 'Enter') return;
		event.preventDefault();
		// The obvious row first: Enter on a search that matched something ticks the match, and only
		// makes a new one when nothing matched. The other way round, typing a name that already
		// exists would quietly make a second.
		const first = matching[0];
		if (first && !isNew) {
			toggle(first);
			typed = '';
			return;
		}
		if (isNew && createLabel) void create();
	}
</script>

<Modal bind:open {title} description={subject} sheetClass="pick-sheet" scrolls={false}>
	{#if head}
		<div class="head">{@render head()}</div>
	{/if}
	<!-- `NarrowBox`, the one box in Sift that filters a list as you type, at the height a sheet's
	     header row stands at: the first Escape empties the box and the sheet stays up with every
	     row back, the second closes the sheet. -->
	<!-- svelte-ignore a11y_autofocus: the sheet exists to be typed into -->
	<NarrowBox
		class="pick-box"
		size="medium"
		bind:value={typed}
		{placeholder}
		label={placeholder}
		maxlength={120}
		autofocus
		onkeydown={onKeydown}
	/>

	{#if matching.length === 0 && !isNew}
		<p class="none">Nothing matched.</p>
	{:else}
		<!-- The list scrolls the way every other region in the app does. The box outside it carries
		     the ceiling, because the scroller cannot be styled from here. See `Scroller`. -->
		<div class="rows-box">
			<Scroller>
				<ul class="rows">
					{#each matching.slice(0, PICK_PAGE) as choice (choice.id)}
						<li>
							<button
								type="button"
								class:ticked={stateOf(choice.id) === 'all'}
								class:refused={Boolean(choice.refused)}
								aria-disabled={choice.refused ? 'true' : undefined}
								aria-describedby={choice.refused ? `pick-refused-${choice.id}` : undefined}
								aria-pressed={stateOf(choice.id) === 'all'
									? 'true'
									: marking && stateOf(choice.id) === 'some'
										? 'mixed'
										: 'false'}
								onclick={() => toggle(choice)}
							>
								<!--
									The app's checkbox rather than a tick drawn here, because this
									is a multi-select list. A picture rather than a control: the
									whole row is the target, and two nested controls a pixel apart
									are two answers to one click.

									The half tick carries a tooltip, because it is the one state
									nobody can read off the drawing. It sits on the box rather than
									the row, so pointing at the mark is what explains the mark.
								-->
								<span class="tick">
									{#if choice.refused}
										<!-- Where the tick would be: the mark that says it cannot be
										     chosen, in the danger tone. -->
										<Icon name="do_not_disturb_on" filled size={18} />
									{:else if marking && stateOf(choice.id) === 'some'}
										<Tooltip label={PARTLY_APPLIED}>
											<Checkbox state="partly" mark label={choice.name} />
										</Tooltip>
									{:else}
										<Checkbox
											state={stateOf(choice.id) === 'all' ? 'on' : 'off'}
											mark
											label={choice.name}
										/>
									{/if}
								</span>
								<!--
									The same picture the flyout and the faces box draw, on all five
									sheets: a person by their face, a collection and a Photo Set by
									their cover, a Site by its own mark. The picture is the
									caller's, already resolved (see `PickChoice`); nothing here
									fetches per row.

									Drawn where the sheet is one of the five kinds the app keeps
									walls of, or where the rows arrived carrying pictures (see
									`faces`). `kind` is only the key of the memory a sheet orders
									by, so it cannot decide this alone: the merge sheet keeps no
									memory and asks which of these people is which, about an act
									that cannot be undone, and a face is how anybody answers that.

									A row whose thing has no cover falls to `Avatar`'s letter rather
									than a verb's glyph, which is why a tag row wears a letter here:
									the sheet is opened by several verbs and screens and has no
									single glyph to take, and the letter needs nothing but the name.
								-->
								{#if faces}
									<span class="face">
										<Avatar
											src={choice.picture?.src}
											instead={choice.picture?.instead}
											name={choice.name}
											mark={choice.picture?.mark ?? false}
											lazy
										/>
									</span>
								{/if}
								{#if choice.strength && !choice.refused}
									<!-- A person whose facial fingerprints are thin: the name, and under it
									     the band their page draws with the count in words. -->
									<span class="named">
										<span class="name">{choice.name}</span>
										<span class="thin" data-band={choice.strength.band}>
											<span class="band" aria-hidden="true"></span>
											{choice.strength.said}
										</span>
									</span>
								{:else}
									<span class="name">{choice.name}</span>
								{/if}
								{#if choice.refused}
									<!-- Why, in the row's own words, where the branch would be said. -->
									<span class="why-not" id="pick-refused-{choice.id}">{choice.refused}</span>
								{:else if choice.within}
									<!-- The branch the row is filed on, so a tag in this alphabetical
									     sheet still says where it sits. -->
									<span class="within"><span class="unseen">in </span>{choice.within}</span>
								{/if}
								{#if recentIds.has(choice.id)}
									<!-- Why this row is in front: picked lately. -->
									<Tooltip label="Chosen recently">
										<span class="recent" role="img" aria-label="Chosen recently"
											><Icon name="history" size={14} /></span
										>
									</Tooltip>
								{/if}
							</button>
						</li>
					{/each}
				</ul>
				<!-- What did not fit, said out loud, as `PickMenu` does: a sheet showing sixty of
				     ninety rows and saying nothing is a sheet where thirty things do not exist.
				     There is nothing to press (the answer is to keep typing), so it is a line of
				     text under the last row rather than a row somebody could aim at. -->
				{#if matching.length > PICK_PAGE}
					<p class="rest">
						{counted(matching.length - PICK_PAGE)} more &#8212; keep typing to filter
					</p>
				{/if}
			</Scroller>
		</div>
	{/if}

	{#if createLabel && isNew}
		<button type="button" class="make" disabled={making} onclick={() => void create()}>
			{createLabel} "{typed.trim()}"
		</button>
	{/if}

	<div class="buttons">
		<!-- The way out first and the act last, like every sheet's foot. -->
		<button type="button" class="cancel" onclick={() => (open = false)}>Cancel</button>
		<!-- Disabled until something is ticked rather than hidden. A button that appears when you
			     are half way through tells you nothing about what it was waiting for. -->
		<button
			type="button"
			class="confirm"
			disabled={changing.on + changing.off === 0}
			onclick={() => finish()}
		>
			{confirmLabel(changing.on, changing.off)}
		</button>
	</div>
</Modal>

<style>
	/* The last line, which says what is not on the list. Drawn as text rather than as a row and in
	   the quiet ink, for the reason `PickMenu` draws the identical line: it is ABOUT the list rather
	   than a member of it, and at the weight of a name it would read as something to press. */
	.rest {
		margin: 0;
		padding: var(--space-2) var(--space-3);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* Portalled out of this component's markup, so its own rules have to reach it globally. The
	   sheet, the veil, the title and the two buttons are dressed once in `app.css`; what is here is
	   only this sheet's own list and box. */
	:global(.pick-sheet) {
		/* The width only. `.sheet` clamps it to the window. See app.css. */
		--sheet-inline: 420px;
	}

	/* The caller's choice at the head, set off from the box under it by the box's own gap. */
	.head {
		margin-block-start: var(--space-3);
	}

	/* Only where it sits. The box (its edge, its focus, its cross) is `NarrowBox`'s. */
	:global(.pick-sheet .pick-box) {
		margin-block: var(--space-3) var(--space-2);
	}

	/*
	 * A grid with one `minmax(0, 1fr)` row, so the scroller inside resolves to a definite height.
	 *
	 * A grid whose only row is `auto` sizes it to its content, so `max-block-size` caps the
	 * container while the row, and the scroller stretched to it, stays at content height and paints
	 * straight out of the sheet over the Add button. `minmax(0, 1fr)` turns the cap into a track
	 * the child is bounded by: the scroller takes the box's height and scrolls its rows, and a
	 * short list still shrink-wraps rather than reserving 40vh of empty space. `EntityHeader` uses
	 * the same shape for the same reason.
	 */
	.rows-box {
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		max-block-size: 40vh;
		margin: 0 0 var(--space-3);
	}

	.rows {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.rows button {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		inline-size: 100%;
		padding: var(--space-2) var(--space-3);
		border: 0;
		border-radius: var(--radius-md);
		background: transparent;
		color: var(--sift-ink);
		font: var(--text-body);
		text-align: start;
		cursor: pointer;
	}

	.rows button {
		transition: background var(--dur-instant) var(--ease);
	}

	/* A finger's height for a row on a phone, as a menu's rows are there. */
	@media (max-width: 767px) {
		.rows button {
			min-block-size: var(--touch-target);
		}
	}

	/* The name, in a box of its own so the row is three things in a line rather than a picture and a
	   run of text: a tick that holds its column, the picture, and whatever is left for the name. */
	.name {
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* The branch a row is on, quiet and after the name, cut before the name is. */
	.within {
		flex: 0 1 auto;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* A name with the line under it, both cut before they push the row wider than the sheet. */
	.named {
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
	}

	.thin {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/*
	 * The band's colour as a person's page draws its bar (`RecognitionStrength.svelte`'s
	 * `.spectrum`): red into orange under five confirmed faces, orange into yellow under ten,
	 * yellow into green under twenty. The same three gradients, held equal to the page's by
	 * `thin-fingerprints.test.ts`, so the row and the page cannot drift into two colours.
	 */
	.band {
		--band-orange: color-mix(in oklch, var(--sift-bad-text), var(--sift-warn));
		flex: none;
		inline-size: var(--space-3);
		block-size: var(--space-1);
		border-radius: var(--radius-full);
		background: linear-gradient(90deg, var(--sift-bad-text), var(--band-orange));
	}

	.thin[data-band='fair'] .band {
		background: linear-gradient(90deg, var(--band-orange), var(--sift-warn));
	}

	.thin[data-band='good'] .band {
		background: linear-gradient(90deg, var(--sift-warn), var(--sift-ok));
	}

	.rows button:hover {
		background: var(--sift-surface-4);
	}

	/* A row that cannot be chosen: no hover ground (nothing happens on a press), and the mark and
	   the reason in the danger tone. */
	.rows button.refused {
		cursor: default;
	}

	.rows button.refused:hover {
		background: transparent;
	}

	.refused .tick {
		color: var(--sift-bad-text);
	}

	.why-not {
		flex: 0 1 auto;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: var(--sift-bad-text);
		font: var(--text-body-sm);
	}

	.rows button:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/*
	 * A fixed box whether or not there is a tick in it, so ticking a row does not shove its name
	 * sideways and unticked rows line up with ticked ones. Pointer events stay on so the half
	 * tick's tooltip can open; a `mark` Checkbox renders a span, not a control, so a press on it
	 * bubbles to the row, which takes every click.
	 */
	.tick {
		display: inline-flex;
		flex: none;
	}

	/* The picture at the head of a row, and it is the flyout's box exactly: a circle off the space
	   scale, holding whatever `Avatar` falls through to. Sized from the scale rather than from the
	   row's own height for the reason `PickMenu` gives: at a control's height the row grows past
	   everything else in the list, and this is to be recognised at a glance rather than looked at. */
	.face {
		display: block;
		flex: none;
		inline-size: var(--space-6);
		block-size: var(--space-6);
		border-radius: 50%;
		overflow: hidden;
	}

	.rows button.ticked {
		background: var(--sift-accent-bg);
	}

	.make {
		align-self: flex-start;
		margin-block-end: var(--space-3);
		padding: var(--space-2) var(--space-3);
		border: 1px dashed var(--sift-line-strong);
		border-radius: var(--radius-md);
		background: transparent;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
		cursor: pointer;
	}

	.make {
		transition:
			color var(--dur-instant) var(--ease),
			border-color var(--dur-instant) var(--ease);
	}

	.make:hover:not(:disabled) {
		color: var(--sift-ink);
		border-color: var(--sift-ink-3);
	}

	.make:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.none {
		margin: 0 0 var(--space-3);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}
	/* The recency mark, quiet and beside the name. */
	.recent {
		display: inline-flex;
		flex: 0 0 auto;
		margin-inline-start: var(--space-1);
		color: var(--sift-ink-3);
	}
</style>
