<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Look a subject up in the stash-boxes and agree field by field, from the field registry.
	 * Nothing is silently overwritten: a field Sift has starts unticked beside theirs. The ticked
	 * keys go to the server's take route, which writes from what was kept with the link.
	 */
	import { untrack } from 'svelte';
	import { exactly, onRecord } from '$lib/shell/when';
	import { api } from '$lib/api/client';
	import {
		Avatar,
		Button,
		Checkbox,
		Empty,
		Modal,
		Pressable,
		Problem,
		TextInput,
		type CheckState
	} from '$lib/components/common';
	import RecordValue from '$lib/components/record/RecordValue.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
	import { fields, type FieldDescription, type RecordSubject } from '$lib/entity/records.svelte';
	import {
		forgetLink,
		keepPicture,
		link,
		linksOf,
		problemFrom,
		chooserPicture,
		refresh,
		search,
		type LinkSubject,
		type StashBoxLink
	} from '$lib/entity/enrich.svelte';
	import {
		StashBoxes,
		type BoxAnswer,
		type FoundRecord
	} from '$lib/settings-ui/stash-boxes.svelte';

	interface Props {
		open?: boolean;
		subject: LinkSubject;
		id: string;
		name: string;
		/** The left-hand side of every row. */
		values: Record<string, unknown>;
		/** Told when a link was kept, so the page can redraw the box list under the record. */
		onlinked?: () => void;
	}

	let { open = $bindable(false), subject, id, name, values, onlinked }: Props = $props();

	/* Seeded ONCE (`untrack`), re-seeded by `begin` on every open. */
	let term = $state(untrack(() => name));
	let answers = $state<BoxAnswer[]>([]);
	let looking = $state(false);

	/* Per box: each answers separately. Reset with every search. */
	let opened = $state(new Set<string>());

	const FIRST = 3;

	/* Every configured box, on or not, so the sheet can say which were NOT asked. */
	const boxes = new StashBoxes();
	boxes.follow();
	const asleep = $derived(boxes.items.filter((one) => !one.enabled));
	let asked = $state(false);
	let problem = $state<string | undefined>();

	let chosen = $state<FoundRecord | null>(null);
	let ticks = $state<Record<string, boolean>>({});
	let saving = $state(false);

	/* Their PICTURE: not a field, so its own tick, off by default. */
	let takePicture = $state(false);

	/* Forget and Ask again live here, where the link was made, not inside every record value. */
	let linked = $state<StashBoxLink[]>([]);
	let busy = $state('');

	async function reload() {
		linked = await linksOf(subject, id);
	}

	async function askAgain(boxId: string) {
		if (busy) return;
		busy = boxId;
		problem = undefined;
		try {
			await refresh(subject, id, boxId);
			await reload();
			onlinked?.();
		} catch (error) {
			problem = problemFrom(error);
		} finally {
			busy = '';
		}
	}

	async function forget(boxId: string) {
		if (busy) return;
		busy = boxId;
		problem = undefined;
		try {
			await forgetLink(subject, id, boxId);
			await reload();
			onlinked?.();
		} catch (error) {
			problem = problemFrom(error);
		} finally {
			busy = '';
		}
	}

	/*
	 * Reset on OPEN (an effect on `open`: `onOpenChange` never fires for a bound open),
	 * `untrack`ed.
	 */
	$effect(() => {
		if (open) untrack(begin);
	});

	function begin() {
		term = name;
		answers = [];
		asked = false;
		chosen = null;
		ticks = {};
		problem = undefined;
		linked = [];
		void reload();
		// Read on open, so a box switched on since shows.
		void boxes.load();
		/* And ASK immediately, with the name already in the box. */
		void look();
	}

	async function look() {
		const wanted = term.trim();
		if (!wanted || looking) return;
		looking = true;
		problem = undefined;
		opened = new Set();
		try {
			// A record kept local is refused before its name goes anywhere (`search`).
			answers = await search(subject, wanted, id);
			asked = true;
		} catch (error) {
			problem = problemFrom(error);
		} finally {
			looking = false;
		}
	}

	function held(key: string): boolean {
		const one = values[key];
		if (one === null || one === undefined || one === '') return false;
		return !Array.isArray(one) || one.length > 0;
	}

	function pick(found: FoundRecord) {
		chosen = found;
		// Ticked where Sift has nothing.
		ticks = Object.fromEntries(offered(found).map((one) => [one.key, !held(one.key)]));
		takePicture = false;
	}

	function offered(found: FoundRecord): FieldDescription[] {
		return fields
			.of(subject as RecordSubject)
			.filter((one) => one.editable && one.key in found.fields);
	}

	const rows = $derived(chosen ? offered(chosen) : []);

	/* A list MERGES, what is here first, compared without case. */
	function merged(mine: unknown, theirs: unknown): string[] {
		const text = (one: unknown) =>
			typeof one === 'string'
				? one
				: String((one as { alias?: string; url?: string; name?: string })?.alias ?? '') ||
					String((one as { url?: string })?.url ?? '') ||
					String((one as { name?: string })?.name ?? '');
		const out: string[] = [];
		for (const one of [
			...(Array.isArray(mine) ? mine : []),
			...(Array.isArray(theirs) ? theirs : [])
		]) {
			const word = text(one).trim();
			if (word && !out.some((had) => had.toLowerCase() === word.toLowerCase())) out.push(word);
		}
		return out;
	}

	function proposed(one: FieldDescription): unknown {
		const theirs = chosen?.fields[one.key];
		return isList(one) ? merged(values[one.key], theirs) : theirs;
	}

	function isList(one: FieldDescription): boolean {
		return one.kind === 'names' || one.kind === 'links';
	}

	async function apply() {
		if (!chosen || saving) return;
		saving = true;
		problem = undefined;
		try {
			// The link first, so a record never carries values with no record of their source.
			await link(subject, id, chosen.source_id, chosen.remote_id);
			/*
			 * The KEYS, never the values; sent with nothing ticked too, as a link that filled
			 * nothing.
			 */
			await api.post(`/stash-boxes/links/${subject}/${id}/${chosen.source_id}/take`, {
				body: { keys: rows.filter((one) => ticks[one.key]).map((one) => one.key) }
			});
			/* The picture last, never allowed to undo the rest. */
			if (takePicture && chosen.image_url) {
				try {
					await keepPicture(subject, id, chosen.source_id);
				} catch {
					problem = "The fields were saved. Their picture couldn't be downloaded.";
				}
			}
			onlinked?.();
			if (!problem) open = false;
		} catch (error) {
			problem = problemFrom(error);
		} finally {
			saving = false;
		}
	}

	/* The picture counts as a tick. */
	const anyTicked = $derived(rows.some((one) => ticks[one.key]) || takePicture);

	/* ONE BOX FOR ALL OF THEM, derived from the rows (`partly` for some), never the picture. */
	const takeAll = $derived<CheckState>(
		rows.length === 0 || !rows.every((one) => ticks[one.key])
			? rows.some((one) => ticks[one.key])
				? 'partly'
				: 'off'
			: 'on'
	);

	/* On means every row; anything else means none. */
	function takeEvery(next: CheckState) {
		const wanted = next === 'on';
		ticks = Object.fromEntries(rows.map((one) => [one.key, wanted]));
	}
</script>

<Modal
	bind:open
	title="Look up in a stash-box"
	description={chosen
		? `Tick a field to take ${chosen.source_name}'s answer for it. Anything left unticked stays` +
			' exactly as it is here.'
		: "Search the stash-boxes you have switched on, and pick the entry that's really this one."}
	sheetClass="stash-look"
>
	{#snippet children()}
		{#if !chosen}
			{#if linked.length > 0}
				<!-- What is already agreed, first: "have I done this already". -->
				<ul class="linked" aria-label="Already linked">
					{#each linked as one (one.box_id)}
						<li>
							<span class="who">
								<span class="name">{one.box_name}</span>
								<Tooltip label={exactly(one.fetched_at)}>
									<span class="quiet"
										>{one.record.name}
										{'\u00b7'} asked {onRecord(one.fetched_at, {
											inline: true
										})}</span
									>
								</Tooltip>
							</span>
							<Button
								type="button"
								size="small"
								busy={busy === one.box_id}
								onclick={() => askAgain(one.box_id)}>Ask again</Button
							>
							<Button
								type="button"
								size="small"
								tone="ghost"
								icon="close"
								disabled={busy === one.box_id}
								onclick={() => forget(one.box_id)}>Remove</Button
							>
						</li>
					{/each}
				</ul>
			{/if}

			<form
				class="asking"
				onsubmit={(event) => {
					event.preventDefault();
					void look();
				}}
			>
				<TextInput
					value={term}
					oninput={(event) => (term = event.currentTarget.value)}
					aria-label="A name"
					autocomplete="off"
				/>
				<Button type="submit" tone="primary" busy={looking}>Look up</Button>
			</form>

			{#if looking}
				<Empty scope="block" busy>Asking each switched-on stash-box in turn.</Empty>
			{:else if asked}
				<!--
				The boxes NOT asked, named under the answers, or one box looks like all of them.
				-->
				<ul class="answers">
					{#each answers as answer (answer.box_id)}
						<li>
							<SectionHeading band>{answer.box_name}</SectionHeading>
							{#if answer.problem}
								<Problem message={answer.problem} />
							{:else if answer.records.length === 0}
								<Empty scope="block">Nothing by that name.</Empty>
							{:else}
								<!--
								Three from each box, those matching EVERY word first; the rest
								folded, never dropped.
								-->
								{@const close = answer.records.filter((one) => one.every_word)}
								{@const best = (close.length > 0 ? close : answer.records).slice(0, FIRST)}
								{@const showing = opened.has(answer.box_id)}
								{@const more = answer.records.length - best.length}
								<ul class="found">
									{#each showing ? answer.records : best as found (found.remote_id)}
										<!--
										The pack's logo first, the box's picture behind it
										(`chooserPicture`).
										-->
										{@const drawn = chooserPicture(found)}
										<li>
											<Pressable feedback="wash" class="candidate" onclick={() => pick(found)}>
												<!--
												A row of this file's own inside the pressable: a
												`:global` rule on it loses to the scoped one.
												-->
												<span class="row">
													<Avatar
														src={drawn.src}
														instead={drawn.instead}
														name={found.name}
														decorative
													/>
													<span class="who">
														<span class="name">{found.name}</span>
														{#if found.disambiguation}
															<span class="quiet">{found.disambiguation}</span>
														{/if}
													</span>
													{#if found.file_count !== null}
														<span class="quiet">{counted(found.file_count)} files</span>
													{/if}
												</span>
											</Pressable>
										</li>
									{/each}
								</ul>
								{#if more > 0 && !showing}
									<Button
										type="button"
										tone="ghost"
										size="small"
										onclick={() => (opened = new Set([...opened, answer.box_id]))}
									>
										Show {counted(more)} more {more === 1 ? 'answer' : 'answers'} from this box
									</Button>
								{/if}
							{/if}
						</li>
					{/each}
				</ul>
				{#if asleep.length > 0}
					<p class="quiet skipped">
						{asleep.map((one) => one.name).join(', ')}
						{asleep.length === 1 ? 'was' : 'were'} not asked &mdash; switched off under Settings, Stash-boxes.
					</p>
				{/if}
			{/if}
		{:else}
			<p class="picked">
				Keeping <strong>{chosen.name}</strong> from <strong>{chosen.source_name}</strong>.
			</p>

			<!--
			Their picture, above the fields: it lands in the picture cache, never over a chosen
			cover.
			-->
			{#if chosen.image_url}
				<div class="picture">
					<Checkbox
						state={takePicture ? 'on' : 'off'}
						onchange={(next) => (takePicture = next === 'on')}
						label="Also use their picture from {chosen.source_name}"
					/>
					<Avatar src={chosen.image_url} name={chosen.name} decorative />
					<!-- Said in words, not only in the box's label. -->
					<span class="offer">Also use their picture from {chosen.source_name}</span>
				</div>
			{/if}

			{#if rows.length === 0}
				<Empty scope="block">That entry carries nothing Sift has a field for.</Empty>
			{:else}
				<!--
				The tick sits in the column it TAKES from, headed by the verb; the take-all box
				above the headings.
				-->
				<div class="take-all">
					<Checkbox
						state={takeAll}
						onchange={takeEvery}
						label="Take all from {chosen.source_name}"
					/>
					<span class="offer">Take all from {chosen.source_name}</span>
				</div>

				<div class="heads" aria-hidden="true">
					<span class="head-name">Field</span>
					<span>Here now</span>
					<span class="head-take">Take from {chosen.source_name}</span>
				</div>
				<ul class="rows">
					{#each rows as one (one.key)}
						<li class:conflict={held(one.key)}>
							<!-- The field's name, drawn. -->
							<span class="what">{one.label}</span>

							<div class="mine">
								<RecordValue kind={one.kind} value={values[one.key]} />
							</div>

							<!-- Their value and its tick in one cell: one decision. -->
							<div class="theirs">
								<!--
								The shared box from a boolean: only `on` is read back, so `out` is
								unreachable.
								-->
								<Checkbox
									state={ticks[one.key] ? 'on' : 'off'}
									onchange={(next) => (ticks[one.key] = next === 'on')}
									label={held(one.key)
										? `Replace ${one.label} with what ${chosen.source_name} says`
										: `Fill in ${one.label} from ${chosen.source_name}`}
								/>
								<div class="value">
									<RecordValue kind={one.kind} value={proposed(one)} />
								</div>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	{/snippet}

	<!-- Out of the scroll, so what to press next stays put. -->
	{#snippet footer()}
		<Problem message={problem} />

		<div class="buttons">
			{#if chosen}
				<Button type="button" onclick={() => (chosen = null)} disabled={saving}>Back</Button>
				<Button type="button" tone="primary" busy={saving} disabled={!anyTicked} onclick={apply}>
					Take what is ticked
				</Button>
			{:else}
				<Button type="button" onclick={() => (open = false)}>Close</Button>
			{/if}
		</div>
	{/snippet}
</Modal>

<style>
	/* Wider, for two values side by side; the height is the shared sheet's. */
	:global(.stash-look) {
		/* The width only; `.sheet` clamps it (app.css). */
		--sheet-inline: 46rem;
	}

	.linked {
		list-style: none;
		margin: var(--space-4) 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.linked li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-2);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
	}

	.linked .who {
		display: flex;
		flex-direction: column;
		flex: 1;
		min-inline-size: 0;
	}

	.asking {
		display: flex;
		gap: var(--space-2);
		margin-block: var(--space-4) var(--space-3);
	}

	.asking :global(.text-input) {
		flex: 1;
		min-inline-size: 0;
	}

	/* The picture offer, set apart from the field rows. */
	.picture {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		margin-block-end: var(--space-3);
		padding-block-end: var(--space-3);
		border-block-end: 1px solid var(--sift-line);
	}

	/* `Avatar` takes whatever it is given. */
	.picture :global(.avatar) {
		flex: none;
		inline-size: 5.5rem;
		border-radius: var(--radius-sm);
	}

	.offer {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.take-all {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin-block-end: var(--space-2);
	}

	.skipped {
		margin-block-start: var(--space-3);
		padding-block-start: var(--space-3);
		border-block-start: 1px solid var(--sift-line);
	}

	.answers,
	.found,
	.rows {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.answers > li + li {
		margin-block-start: var(--space-4);
	}

	.answers > li {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.found > li + li {
		margin-block-start: var(--space-1);
	}

	/* `:global` inside a scoped parent: the pressable's own rule is two classes. */
	.found :global(.candidate) {
		inline-size: 100%;
		padding: var(--space-2);
		text-align: start;
	}

	.row {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		inline-size: 100%;
	}

	/*
	 * Big enough to tell two people apart, `flex: none`; `Avatar` would otherwise fill the sheet.
	 */
	.row :global(.avatar) {
		flex: none;
		inline-size: 5.5rem;
		border-radius: var(--radius-sm);
	}

	.who {
		display: flex;
		flex-direction: column;
		flex: 1;
		min-inline-size: 0;
	}

	.name {
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.picked {
		margin: var(--space-4) 0 var(--space-3);
		font: var(--text-body);
		color: var(--sift-ink-2);
	}

	.rows > li {
		padding: var(--space-3) 0;
		border-block-start: 1px solid var(--sift-line);
	}

	/* Both sides have something: ticking throws one away. */
	.rows > li.conflict .what {
		color: var(--sift-accent-text);
	}

	/* One grid for headings and rows, through one custom property. */
	.heads,
	.rows > li {
		display: grid;
		grid-template-columns: minmax(7rem, 1fr) minmax(8rem, 1.2fr) minmax(10rem, 1.4fr);
		gap: var(--space-3);
		align-items: start;
	}

	.heads {
		padding-block-end: var(--space-2);
		font: var(--text-micro);
		color: var(--sift-ink-3);
	}

	/* The only heading that describes an ACTION. */
	.head-take {
		color: var(--sift-ink-2);
	}

	.head-name {
		color: var(--sift-ink-3);
	}

	.what {
		display: block;
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.mine {
		color: var(--sift-ink-3);
	}

	.theirs {
		display: flex;
		align-items: start;
		gap: var(--space-2);
	}

	.theirs .value {
		min-inline-size: 0;
		flex: 1;
	}

	.buttons {
		margin-block-start: var(--space-5);
	}
</style>
