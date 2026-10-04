<script lang="ts">
	/*
	 * What the other device offered, and the guest's answer to it: Take or Skip, per person.
	 *
	 * Take, the word for accepting what another source offers (Take theirs on a stash-box's
	 * disagreement, Take what is ticked on its fields), which is what an offer from another device
	 * is. Add is the word for something already yours going somewhere (Add to a Collection).
	 *
	 * ## Two shapes, and the server chooses
	 *
	 * Up to ten offered people get a row each ("rows"). Above ten a row each is a wall nobody reads,
	 * so the screen is the whole offer ("whole"): the totals, the five largest people with Take and
	 * Skip, and everybody else in a list that stays folded until it is opened and can be searched.
	 * Which one is `layout`, decided where the offer was read: this draws what it is given.
	 *
	 * ## Skipping is per person, and the counts say what that costs
	 *
	 * A file under two offered people counts under both, so each row's figure is what taking that
	 * person would bring and the rows do not add up to the total. The screen says how many files that
	 * is, in the server's words for it, rather than leave the difference to be worked out. Skipping
	 * somebody drops their files unless another person who was taken is on them too: that is the
	 * server's rule and it is applied there; what leaves here is only who was skipped.
	 *
	 * ## What is already here is shown, and never taken
	 *
	 * A file this library already has is listed unticked with why, so "38 files" beside a person who
	 * had 41 is not a mystery. It cannot be ticked back on: sending a file already here is the one
	 * thing a swap exists not to do.
	 *
	 * ## What was offered, and what taking these would bring
	 *
	 * The lede says everything offered, as files and a size, the ones already here included. Above
	 * Take these, what the answer as it stands would bring: every offered file not already here at
	 * first, then, after each Take or Skip, the server's own weighing of that answer
	 * (`weighAnswer`), since which files a skip drops is the server's rule and not this screen's.
	 */
	import { SvelteSet } from 'svelte/reactivity';
	import { Button, Checkbox, NarrowBox, Panel } from '$lib/components/common';
	import { filesAndSize, weighAnswer, type OfferScreen, type PersonRow } from './swap';

	interface Props {
		screen: OfferScreen;
		/** The session the offer belongs to, for weighing an answer before it is sent. */
		sessionId?: string;
		busy?: boolean;
		/** Take these: the offered people skipped, by their place in the offer. */
		ontake: (skipped: number[]) => void;
	}

	let { screen, sessionId, busy = false, ontake }: Props = $props();

	const skipped = new SvelteSet<number>();

	/* What the answer as it stands would bring. Everything not already here until somebody skips;
	   then the server's weighing of the answer, the last question's answer kept. */
	let weighed = $state<{ files: number; bytes: number } | null>(null);
	let asked = 0;
	$effect(() => {
		const answer = [...skipped].sort((a, b) => a - b);
		const mine = ++asked;
		if (answer.length === 0 || !sessionId) {
			weighed = null;
			return;
		}
		weighAnswer(sessionId, answer).then(
			(weight) => {
				if (mine === asked) weighed = weight;
			},
			() => {
				if (mine === asked) weighed = null;
			}
		);
	});

	const offered = $derived(
		filesAndSize(screen.offered_files ?? screen.files, screen.offered_bytes ?? screen.bytes)
	);
	const bringing = $derived(
		weighed && skipped.size > 0
			? filesAndSize(weighed.files, weighed.bytes)
			: filesAndSize(screen.files, screen.bytes)
	);

	/** How many held files are listed before the list says how many more there are. */
	const HELD_SHOWN = 100;

	let everyoneOpen = $state(false);
	let heldOpen = $state(false);
	let find = $state('');

	const everyone = $derived(
		screen.everyone.filter((one) => one.name.toLowerCase().includes(find.trim().toLowerCase()))
	);

	function take(row: PersonRow): void {
		skipped.delete(row.index);
	}

	function skip(row: PersonRow): void {
		skipped.add(row.index);
	}

	function detail(row: PersonRow): string {
		// Offered for their facial fingerprints alone: what comes is those, and no files, with how
		// many confirmed faces the other side has of them where it said, since the best 64 of 300
		// and all 3 of 3 are not the same promise.
		if (row.files === 0 && row.held === 0 && row.faces > 0) {
			const prints =
				row.faces === 1
					? '1 facial fingerprint'
					: `${row.faces.toLocaleString()} facial fingerprints`;
			const from =
				row.confirmed === null || row.confirmed === undefined
					? ''
					: ` from ${row.confirmed === 1 ? '1 confirmed face' : `${row.confirmed.toLocaleString()} confirmed faces`}`;
			return `${prints}${from}, no files`;
		}
		const bringing = filesAndSize(row.files, row.bytes);
		return row.held > 0
			? `${bringing} \u00b7 you have ${row.held.toLocaleString()} already`
			: bringing;
	}

	function here(row: PersonRow): string {
		if (!row.match_id || !row.match_name) return 'New to your library';
		if (row.matched_by === 'box') return `${row.match_name} here, by stash-box`;
		if (row.matched_by === 'alias') return `${row.match_name} here, by another name`;
		return `${row.match_name} here`;
	}

	const sharedWords = $derived(
		screen.shared === 1
			? '1 file appears under two of these people.'
			: `${screen.shared.toLocaleString()} files appear under two of these people.`
	);

	const heldWords = (reason: string) =>
		reason === 'same' ? 'You have this one' : 'You have one that looks the same';
</script>

{#snippet person(row: PersonRow)}
	<li class="person">
		<span class="who">
			<span>{row.name}: {detail(row)}</span>
			<span class="here">{here(row)}</span>
		</span>
		<span class="choice">
			<Button size="small" tone="quiet" pressed={!skipped.has(row.index)} onclick={() => take(row)}
				>Take</Button
			>
			<Button size="small" tone="quiet" pressed={skipped.has(row.index)} onclick={() => skip(row)}
				>Skip</Button
			>
		</span>
	</li>
{/snippet}

<Panel label="What they offered">
	{#if screen.layout === 'whole'}
		<p class="lede">
			They offered {offered} under {screen.everyone.length.toLocaleString()}
			people. The five with the most:
		</p>
	{:else}
		<p class="lede">They offered {offered}.</p>
	{/if}

	<ul class="people">
		{#each screen.rows as row (row.index)}
			{@render person(row)}
		{/each}
	</ul>

	{#if screen.shared > 0}
		<p class="note">{sharedWords}</p>
	{/if}
	{#if screen.unfiled_files > 0}
		<p class="note">
			Also {filesAndSize(screen.unfiled_files, screen.unfiled_bytes)} under nobody listed here.
		</p>
	{/if}

	{#if screen.layout === 'whole'}
		<div class="fold">
			<Button
				size="small"
				tone="quiet"
				trailing={everyoneOpen ? 'expand_less' : 'expand_more'}
				onclick={() => (everyoneOpen = !everyoneOpen)}
			>
				Show everyone ({screen.everyone.length.toLocaleString()})
			</Button>
		</div>
		{#if everyoneOpen}
			<NarrowBox bind:value={find} label="Search the people offered" placeholder="Search people" />
			<ul class="people">
				{#each everyone as row (row.index)}
					<li class="ticked">
						<Checkbox
							state={skipped.has(row.index) ? 'off' : 'on'}
							label="Take {row.name}"
							onchange={(next) => (next === 'on' ? take(row) : skip(row))}
						/>
						<span>{row.name}: {detail(row)}</span>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}

	{#if screen.held.length > 0}
		<div class="fold">
			<Button
				size="small"
				tone="quiet"
				trailing={heldOpen ? 'expand_less' : 'expand_more'}
				onclick={() => (heldOpen = !heldOpen)}
			>
				Show the {screen.held.length.toLocaleString()} you have
			</Button>
		</div>
		{#if heldOpen}
			<ul class="held">
				{#each screen.held.slice(0, HELD_SHOWN) as file (file.key)}
					<li class="ticked">
						<Checkbox state="off" disabled label="{file.title}, not taken" />
						<span>{file.title}</span>
						<span class="here">{heldWords(file.reason)}</span>
					</li>
				{/each}
			</ul>
			{#if screen.held.length > HELD_SHOWN}
				<p class="note">And {(screen.held.length - HELD_SHOWN).toLocaleString()} more.</p>
			{/if}
		{/if}
	{/if}

	<p class="bringing">You would receive {bringing}.</p>

	<div class="finish">
		<Button tone="primary" {busy} onclick={() => ontake([...skipped].sort((a, b) => a - b))}
			>Take these</Button
		>
	</div>
</Panel>

<style>
	.lede,
	.note {
		margin: 0;
	}

	.note {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.bringing {
		margin: 0;
		font-variant-numeric: tabular-nums;
	}

	.people,
	.held {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* What a row is about on the left, the choice on the right. */
	.person {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
	}

	/* A tick and what it is about, side by side. */
	.ticked {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.who {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}

	.here {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.choice,
	.fold,
	.finish {
		display: flex;
		gap: var(--space-2);
	}

	.finish {
		justify-content: flex-end;
	}
</style>
