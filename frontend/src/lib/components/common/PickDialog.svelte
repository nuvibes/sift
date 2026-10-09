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
	/* Pick several things out of a list to put a selection of files on (Tag, Add to a collection or
	 * a person), finished with a button; typing a new name and Enter creates it, where allowed. */
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
		/** Everything that can be picked, filtered as somebody types; rows may carry a picture. */
		choices: readonly PickChoice[];
		/** The word for the button that makes a new one. Absent means no making. */
		createLabel?: string;
		placeholder?: string;
		/**
		 * The finishing button's words for how many go on and come off, so it never misstates the
		 * act.
		 */
		confirmLabel: (on: number, off: number) => string;
		/** Everything being put on, once. Never called with an empty list. */
		onpick: (choices: Choice[]) => void;
		/** Which record of past picks orders this list and is written as rows are pressed. */
		kind?: FrequentKind;
		/** Which of this list the files are already on, asked on each opening. */
		already?: () => Promise<Record<string, OnAlready>>;
		/** Take the files off these; marks are drawn only where this and `already` both arrived. */
		onunpick?: (choices: Choice[]) => void;
		/** More choices for what was typed, from where the whole list lives. */
		onsearch?: (typed: string) => Promise<PickChoice[]>;
		/** Exactly one row may be ticked; ticking another moves the tick. */
		single?: boolean;
		/** The row ticked on opening: a default answer the press corrects. */
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
	/* What each row was found to be and what a press made it, so only changes are sent. */
	let marks = $state<Record<string, OnAlready>>({});
	const wanted = new SvelteMap<string, OnAlready>();

	/* Marks are drawn only where the caller can answer BOTH questions. See `onunpick`. */
	const marking = $derived(already !== undefined && onunpick !== undefined);

	/** What a row says right now: what a press made it, or what it was found to be. */
	function stateOf(id: string): OnAlready {
		return wanted.get(id) ?? marks[id] ?? 'none';
	}

	/* The order, taken once per opening and held, so rows do not slide under the pointer. */
	let order = $state<readonly Named[]>([]);
	/** Anything made from here, so a brand-new tag can be ticked before it is in `choices`. */
	let made = $state<PickChoice[]>([]);
	let making = $state(false);

	/** Which opening an answer belongs to, so a slow one cannot land on the next sitting. */
	let opening = 0;

	/* Cleared and asked on the way in, the shape PickMenu uses. */

	function begin(): void {
		const mine = ++opening;
		typed = '';
		wanted.clear();
		made = [];
		marks = {};
		/* Asked only where the marks will be drawn. */
		if (marking && already) {
			void already()
				.then((answer) => {
					if (mine === opening) marks = answer;
				})
				.catch(() => {
					// No marks rather than wrong ones.
					if (mine === opening) marks = {};
				});
		}
		/* The default answer, after the clear. */
		if (preset) wanted.set(preset, 'all');
		if (kind) {
			/* The record is read before the first ordering, or the list jumps. */
			order = remembered(kind);
			void recallPicks().then(() => {
				if (mine === opening) order = remembered(kind);
			});
		}
	}

	/*
	 * One opening, one reset: `begin` untracked, or every press (which writes the record) resets.
	 */
	$effect(() => {
		if (!open) return;
		/* The one other thing that may decide there has been an opening. See `restart`. */
		void restart;
		untrack(begin);
	});

	/*
	 * Everything that can be ticked drops a created row once it is in `choices`: one key, one row.
	 */
	/*
	 * What the server answered for the text, a beat after typing stops; an older answer is dropped.
	 */
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
	 * What is drawn: matches, recent picks first, the rest alphabetical; no `kind`, the caller's
	 * order.
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

	/** Pictures on every row or none: one picture or a `kind` turns the column on. */
	const faces = $derived(kind !== undefined || everything.some((one) => one.picture !== undefined));

	/* A name nothing has, compared ignoring case. */
	const isNew = $derived(
		typed.trim() !== '' &&
			!everything.some((choice) => choice.name.toLowerCase() === typed.trim().toLowerCase())
	);

	/* A full tick clears, anything else fills; remembered on the press, cleared or not. */
	function toggle(choice: PickChoice) {
		// A row that cannot be chosen here says why on itself, and a press ticks nothing.
		if (choice.refused) return;
		if (kind) noteUse(kind, choice);
		const next = stateOf(choice.id) === 'all' ? 'none' : 'all';
		/* One answer only: the tick moves. */
		if (single && next === 'all')
			for (const other of everything)
				if (other.id !== choice.id && stateOf(other.id) === 'all') wanted.set(other.id, 'none');
		wanted.set(choice.id, next);
	}

	/* How many would go on and come off now, from `everything`, as `finish` sends. */
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
		 * `justMade`: `everything` has not recomputed in this tick, so the new row would be missed.
		 */
		const pool = justMade ? [justMade, ...everything] : everything;
		// By id, the first kept: `justMade` leads and is certainly current.
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
		// Told before closing: the caller's `open` setter may clear what this acts on.
		if (chosen.length > 0) onpick(chosen);
		// Both halves, before closing.
		if (dropped.length > 0) onunpick?.(dropped);
		open = false;
	}

	async function create() {
		const name = typed.trim();
		if (!name || !oncreate || making) return;
		making = true;
		try {
			/* Creating answers the question, so it finishes with everything ticked. */
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
			/* A refused create is said, in the server's words where it gave any. */
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
		// The match first; Enter creates only when nothing matched.
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
	<!-- NarrowBox: the first Escape empties it, the second closes the sheet. -->
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
		<!-- The box carries the ceiling the scroller cannot be styled with. -->
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
								The app's checkbox as a picture: the row is the target. The half
								tick's tooltip explains it.
								-->

								<span class="tick">
									{#if choice.refused}
										<!--
										Cannot be chosen: the danger mark in the tick's place.
										-->
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
								The caller's resolved picture, as the flyout draws it; a thing with
								no cover gets a letter.
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
									<!--
									A person with thin fingerprints: the name and their page's band.
									-->
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
									<!-- The branch the row is filed on. -->
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
				<!-- What did not fit, said as a line of text, as PickMenu does. -->
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
		<!-- Disabled until something is ticked, never hidden. -->
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
	/* About the list, not a member of it: quiet text. */
	.rest {
		margin: 0;
		padding: var(--space-2) var(--space-3);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* Global: the sheet is portalled; the shared chrome is in app.css. */
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

	/* One minmax(0, 1fr) row, so the scroller gets a definite height and a short list shrinks. */
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

	/* The name in a box of its own: tick, picture, name. */
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

	/* The band's colours as a person's page draws them, held equal by thin-fingerprints.test.ts. */
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

	/* Refused: no hover ground, the mark and reason in the danger tone. */
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

	/* A fixed tick box, so names line up; a press on the mark bubbles to the row. */
	.tick {
		display: inline-flex;
		flex: none;
	}

	/* The flyout's circle off the space scale, not the row's height. */
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
