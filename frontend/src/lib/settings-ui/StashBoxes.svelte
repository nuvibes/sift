<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	import { onMount } from 'svelte';
	import {
		Badge,
		Button,
		ConfirmDialog,
		DataRow,
		FormCard,
		MoreAbout,
		Problem,
		SectionHeading,
		Select,
		Switch,
		TextInput
	} from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import FieldRow from './FieldRow.svelte';
	import { KNOWN_BOXES, StashBoxes, type BoxAnswer, type StashBox } from './stash-boxes.svelte';
	import { DIRECT, Tunnels } from './tunnels-state.svelte';
	import { STASH_BOX_KEYS } from './StashBoxes.search';
	import type { Verb } from '$lib/components/common/verbs';

	/* The stash-boxes, on the Stash-boxes section. A stash-box is a shared database of who is in
	 * what: StashDB, FansDB, PMVStash. */

	/* The list, handed in rather than made here, so the panel that unlocks the master key can
	   re-read it. */
	let { boxes = new StashBoxes() }: { boxes?: StashBoxes } = $props();

	/* The tunnels, read only to NAME the ways out. The same store the Routing pane uses, so the
	   two screens cannot come to offer different lists of the same tunnels. */
	const tunnels = new Tunnels();
	const ways = $derived([
		{ value: DIRECT, label: 'Direct — your own connection' },
		...tunnels.items.map((one) => ({ value: one.id, label: one.name }))
	]);

	let name = $state('');
	let endpoint = $state('');
	let key = $state('');
	let adding = $state<string | undefined>(undefined);

	let checking = $state<string | null>(null);
	let checked = $state<Record<string, string | null>>({});

	let replacing = $state<StashBox | null>(null);
	let replacement = $state('');

	let removing = $state<StashBox | null>(null);
	let removeOpen = $state(false);

	let term = $state('');
	let answers = $state<BoxAnswer[] | null>(null);
	let looking = $state(false);
	let lookProblem = $state<string | undefined>(undefined);

	onMount(() => {
		void boxes.load();
		// Only to name the ways out. A failure here leaves the list at Direct, which is honest.
		void tunnels.load();
	});

	function preset(one: (typeof KNOWN_BOXES)[number]) {
		name = one.name;
		endpoint = one.endpoint;
	}

	async function add(event: SubmitEvent) {
		event.preventDefault();
		if (!name.trim() || !endpoint.trim()) return;
		adding = await boxes.add({
			name: name.trim(),
			endpoint: endpoint.trim(),
			api_key: key.trim() || null
		});
		if (!adding) {
			name = '';
			endpoint = '';
			key = '';
		}
	}

	async function check(box: StashBox) {
		checking = box.id;
		const problem = await boxes.check(box.id);
		checked = { ...checked, [box.id]: problem ?? null };
		checking = null;
	}

	async function replaceKey(event: SubmitEvent) {
		event.preventDefault();
		if (!replacing || !replacement.trim()) return;
		await boxes.replaceKey(replacing.id, replacement.trim());
		replacement = '';
		replacing = null;
	}

	/* Auto-enriching the whole library is not a button of this list's. */

	async function look(event: SubmitEvent) {
		event.preventDefault();
		if (!term.trim()) return;
		looking = true;
		lookProblem = undefined;
		try {
			answers = await boxes.lookUp(term.trim());
		} catch {
			lookProblem = "Couldn't search the stash-boxes. Check that at least one is turned on.";
			answers = null;
		} finally {
			looking = false;
		}
	}

	/** The handful of fields worth showing in a chooser. The rest arrive when linking is built. */
	/* The acts on a box, declared once for the row's menu and its right-click: Test asks the box a
	   question, Edit key replaces the sealed key, Delete is the destructive one and is drawn last. */
	function boxVerbs(box: StashBox): Verb[] {
		return [
			{ id: 'test', label: 'Test', icon: 'sync', run: () => void check(box) },
			{
				id: 'key',
				label: 'Edit key',
				icon: 'edit',
				run: () => {
					replacing = box;
					replacement = '';
				}
			},
			{
				id: 'remove',
				label: 'Delete',
				icon: 'delete',
				filled: true,
				destructive: true,
				run: () => {
					removing = box;
					removeOpen = true;
				}
			}
		];
	}

	function summarise(fields: Record<string, unknown>): string {
		const parts = [fields.country, fields.birth_date, fields.hair_color, fields.measurements];
		return parts.filter((one) => typeof one === 'string' && one).join(' \u00b7 ');
	}
</script>

<section>
	<SectionHeading id="stash-boxes.list">{STASH_BOX_KEYS.name}</SectionHeading>
	<p class="lede">
		Choose one of the three stash-boxes under Add a stash-box, then enter your API key.
	</p>
	<!-- The reassurance stays, because it is the question anybody connecting a public service asks
	     first, and it is folded because it is read once. -->
	<MoreAbout>
		<p>Sift only ever reads from a stash-box. It never edits, votes or submits anything.</p>
	</MoreAbout>

	<Problem message={boxes.problem} />

	{#if boxes.items.length > 0}
		<ul class="boxes">
			{#each boxes.items as box (box.id)}
				<li>
					<!-- The record's head, a row of the pane: what the box is on the left, what can be
					     done to it on the right, ending at the pane's edge, and whether it is used at
					     all as the switch last, as a tunnel's row says it. -->
					<!-- The shared row, not a hand-written one: `DataRow` opens the same declared list
					     of verbs from the three-dot menu and from a right-click, Delete last as the one
					     destructive verb, and the switch that says whether the box is used at all sits
					     at the row's end, as a tunnel's row has it. -->
					<DataRow verbs={boxVerbs(box)} ids={[box.id]} menuLabel="More for {box.name}">
						<div class="what">
							<span class="name">{box.name}</span>
							<span class="endpoint">{box.endpoint}</span>
						</div>
						<!-- In the row's trailing slot, so the switch stands at the pane's right edge
						     with the three dots before it, not under the name. -->
						{#snippet trailing()}
							<Switch
								checked={box.enabled}
								label={`Use ${box.name}`}
								onCheckedChange={(on: boolean) => void boxes.setEnabled(box.id, on)}
							/>
						{/snippet}
					</DataRow>
					<div class="marks">
						<!-- THREE states, not two. -->
						{#if !box.has_key}
							<Badge state="blocked" label="No key" />
						{:else if box.key_ready}
							<Badge state="done" label="Key saved" />
						{:else}
							<Badge state="failed" label="Key locked" />
						{/if}
						{#if checked[box.id] === null}<Badge state="done" label="Connected" />{/if}
					</div>
					{#if box.has_key && !box.key_ready}
						<Problem
							message="This key is locked because Sift restarted. Enter your password in Unlock at
								the top of this page to use it again. Nothing was lost."
						/>
					{/if}
					<Problem message={checked[box.id]} />

					<!-- How this box files its creators, said rather than asked. -->
					<p class="filing">
						{box.sites_are === 'person'
							? `On ${box.name} the creators are filed as studios, so Sift searches for a person among them.`
							: `On ${box.name} a performer is a person and a studio is a Site.`}
					</p>

					<!--
						Where its traffic goes out. A stash-box question carries a key and a name to
						somebody else's service, which is exactly the kind of traffic somebody sets a
						tunnel up for.
					-->
					<div class="pair">
						<!-- A settings row, so it lines up with every control on the pane rather than being
						     a stacked form field in the middle of a list. -->
						<LabelledRow
							label="Connect through"
							help="Requests to this stash-box include your key. Choose a tunnel to send them from a different address."
						>
							<Select
								label="Connect {box.name} through"
								value={box.route ?? DIRECT}
								options={ways}
								onValueChange={(next: string) =>
									void boxes.setRoute(box.id, next === DIRECT ? null : next)}
							/>
						</LabelledRow>
					</div>

					{#if replacing?.id === box.id}
						<FormCard title="New key for {box.name}" onsubmit={replaceKey}>
							<FieldRow label="New key" help="Sift encrypts it and never shows it again.">
								{#snippet control({ id, describedBy })}
									<TextInput
										{id}
										{describedBy}
										type="password"
										autocomplete="off"
										bind:value={replacement}
									/>
								{/snippet}
								{#snippet press()}
									<Button type="submit" tone="primary" icon="save" disabled={!replacement.trim()}
										>Save</Button
									>
								{/snippet}
							</FieldRow>
						</FormCard>
					{/if}
				</li>
			{/each}
		</ul>
	{:else}
		<p class="lede">None yet. Add one below.</p>
	{/if}

	<FormCard
		title="Add a stash-box"
		help="Your key is encrypted as soon as you save it and is never shown again."
		onsubmit={add}
	>
		<!-- A row like the fields under it: what it is on the left, the three answers on the right,
		     ending where every box ends. -->
		<div class="presets">
			<span class="hint">Start from a tested stash-box</span>
			<span class="picks" role="group" aria-label="Start from a tested stash-box">
				{#each KNOWN_BOXES as one (one.endpoint)}
					<Button type="button" onclick={() => preset(one)}>
						{one.name}
					</Button>
				{/each}
			</span>
		</div>

		<FieldRow label="Name" help="The name Sift shows for it.">
			{#snippet control({ id, describedBy })}
				<TextInput {id} {describedBy} autocomplete="off" bind:value={name} />
			{/snippet}
		</FieldRow>

		<FieldRow label="Address" help="The GraphQL address of the stash-box.">
			{#snippet control({ id, describedBy })}
				<TextInput
					{id}
					{describedBy}
					type="text"
					inputmode="url"
					placeholder={'https://\u2026'}
					autocomplete="off"
					bind:value={endpoint}
				/>
			{/snippet}
		</FieldRow>

		<FieldRow
			label="Key"
			help="Your API key for this stash-box. Sift encrypts it and never shows it again."
		>
			{#snippet control({ id, describedBy })}
				<TextInput {id} {describedBy} type="password" autocomplete="off" bind:value={key} />
			{/snippet}
			{#snippet press()}
				<Button
					type="submit"
					tone="primary"
					icon="add"
					busy={boxes.busy}
					disabled={!name.trim() || !endpoint.trim()}>Add stash-box</Button
				>
			{/snippet}
		</FieldRow>

		<Problem message={adding} />
	</FormCard>
</section>

<section>
	<SectionHeading>Stash-box search</SectionHeading>
	<p class="lede">
		Search every stash-box that's turned on for a name. Nothing here is saved to your library, so
		use it to check that a key works and what a stash-box has.
	</p>

	<FormCard title="Search by name" onsubmit={look}>
		<FieldRow label="Name" help="A person's name, as a stash-box would have it.">
			{#snippet control({ id, describedBy })}
				<TextInput {id} {describedBy} autocomplete="off" bind:value={term} />
			{/snippet}
			{#snippet press()}
				<Button type="submit" tone="primary" icon="search" busy={looking} disabled={!term.trim()}
					>Search</Button
				>
			{/snippet}
		</FieldRow>
	</FormCard>

	<Problem message={lookProblem} />

	{#if answers}
		{#each answers as answer (answer.box_id)}
			<div class="answer">
				<SectionHeading level={3}>
					{answer.box_name}
					{#if !answer.fresh && !answer.problem}
						<span class="cached">from an earlier search</span>
					{/if}
				</SectionHeading>
				{#if answer.problem}
					<Problem message={answer.problem} />
				{:else if answer.records.length === 0}
					<p class="lede">No results for that name.</p>
				{:else}
					<ul class="found">
						{#each answer.records as one (one.remote_id)}
							<li>
								{#if one.image_url}
									<img src={one.image_url} alt="" loading="lazy" referrerpolicy="no-referrer" />
								{/if}
								<div class="who">
									<span class="name">
										{one.name}
										{#if one.disambiguation}<span class="tell">({one.disambiguation})</span>{/if}
									</span>
									<span class="detail">{summarise(one.fields)}</span>
									{#if one.file_count !== null}
										<span class="detail">{counted(one.file_count)} files</span>
									{/if}
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		{/each}
	{/if}
</section>

<ConfirmDialog
	bind:open={removeOpen}
	title="Delete {removing?.name ?? 'this stash-box'}?"
	consequence="Its key and its saved answers are deleted. Nothing in your library changes."
	confirmLabel="Delete stash-box"
	destructive={true}
	onconfirm={async () => {
		if (removing) await boxes.forget(removing.id);
	}}
/>

<style>
	/* Side by side where there is room, stacked where there is not. */
	.pair {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
		gap: var(--space-4);
	}

	/* How this box files its creators, stated rather than asked. */
	.filing {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.boxes,
	.found {
		list-style: none;
		margin: var(--space-4) 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	/* Records as the pane's rows: no box around each, the pane's edges for its edges, and a
	   hairline between two, drawn by the lower one. */
	.boxes {
		gap: 0;
		margin-block-end: 0;
	}

	.boxes > li {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding-block: var(--space-4);
	}

	/* The record's head row on the pane's edges, as a list with `edges` stands (`DataRows`): the
	   row is pulled out by the room its hover ground needs and pads its name back in by the
	   same, so the name starts where the badge, the sentence and every other row's name start. */
	.boxes > li > :global(.line) {
		margin-inline: calc(-1 * var(--space-2)) calc(-1 * var(--space-3));
	}

	.boxes > li > :global(.line > .row-trigger > .row) {
		padding-inline-start: var(--space-2);
	}

	.boxes > li + li {
		border-block-start: 1px solid var(--sift-line);
	}

	.what {
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
	}

	.name {
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.endpoint {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		overflow-wrap: anywhere;
	}

	.marks,
	.picks {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	/* The same two columns as a field's row in the form, the answers ending at the right edge. */
	.presets {
		display: grid;
		grid-template-columns: minmax(0, 1fr) var(--settings-control-col-wide, min(26rem, 60%));
		column-gap: var(--space-6);
		align-items: center;
		padding-block: var(--space-4);
	}

	.picks {
		justify-content: var(--row-pack, flex-end);
	}

	/* Stacked on a phone, as the fields under it stack (`FormCard`): the name, then the three
	   answers under it from the row's start. */
	@media (max-width: 767px) {
		.presets {
			grid-template-columns: minmax(0, 1fr);
			row-gap: var(--space-2);
		}
	}

	/* A row's name, as the fields under it name theirs. */
	.presets > .hint {
		font: var(--text-body);
		font-weight: 600;
		color: var(--sift-ink);
	}

	/* The line between the last record and the form under it, drawn by the lower one. */
	.boxes + :global(form.card) {
		border-block-start: 1px solid var(--sift-line);
	}

	.hint,
	.cached,
	.tell,
	.detail {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.answer {
		margin-block-start: var(--space-5);
	}

	.found > li {
		display: flex;
		gap: var(--space-3);
		align-items: center;
	}

	.found img {
		inline-size: 48px;
		block-size: 64px;
		object-fit: cover;
		border-radius: var(--radius-sm);
	}

	.who {
		display: flex;
		flex-direction: column;
	}
</style>
