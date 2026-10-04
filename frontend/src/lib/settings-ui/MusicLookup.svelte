<script lang="ts">
	/*
	 * Settings > Music > Name songs: whether Sift may ask AcoustID which song a file uses.
	 *
	 * It has the four parts a stash-box has (a switch, a key, a way out and a test), drawn in the
	 * order every settings pane keeps: the switch with where it stands in the shaded box under it, then
	 * how it behaves (the key, the test), then when it runs (the `when` snippet the pane hands in:
	 * the lookup task's row and the fingerprint task's), and last the More settings page, which holds
	 * the way out. Asking AcoustID about the library is the lookup task's press, on Settings > Tasks,
	 * and nothing on this block starts it.
	 *
	 * ## The disclosure is in the status box, and it is never folded away
	 *
	 * Everywhere else a setting's disclosure is folded under "More about", because it is reference
	 * material read once. This one is what the switch sends off this device, and it is the reason
	 * the switch is off: it is the status box's second line, in the server's own words, and the
	 * switch's row does not repeat it.
	 *
	 * ## The key
	 *
	 * Typed into a password box, sent, and never shown again: the server has no answer that carries
	 * it back, so there is nothing to fill a box with. Once one is set the block says so (Key saved,
	 * or Key locked after a restart, the three states a stash-box's key has), with Delete, and the
	 * box to type one comes back only after that.
	 *
	 * ## The route
	 *
	 * On the More settings page: it is set once, if ever. Drawn exactly as a stash-box's: the same
	 * row, the same menu, the same list of ways out (this device's own connection, then every
	 * tunnel), read from the same tunnel store the Sites pane uses so the two cannot offer
	 * different lists.
	 */
	import { onMount, type Snippet } from 'svelte';
	import {
		Badge,
		Button,
		FormCard,
		Problem,
		Select,
		Skeleton,
		TextInput
	} from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import FieldRow from './FieldRow.svelte';
	import {
		jobChanges,
		libraryChanges,
		settingChanges,
		whenChanged
	} from '$lib/library/changes.svelte';
	import ActionRow from './ActionRow.svelte';
	import { drilldown } from './drilldown.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import RecognitionNote from './RecognitionNote.svelte';
	import { COPY as SITES } from './Sites.search';
	import { DIRECT, Tunnels } from './tunnels-state.svelte';
	import { COPY, LOOKUP_KEY, LOOKUP_ROUTE_KEY, MusicLookup } from './music-lookup.svelte';

	/* Handed in rather than made here, so the Unlock panel at the top of the section can re-read it:
	   unlocking is what turns Key locked back into Key saved. Defaulted, because the block is legible
	   on its own. */
	let { lookup = new MusicLookup(), when }: { lookup?: MusicLookup; when?: Snippet } = $props();

	/* Only to name the ways out. A failure leaves the list at Direct, which is honest. */
	const tunnels = new Tunnels();
	tunnels.follow();
	const ways = $derived([
		{ value: DIRECT, label: SITES.routing.direct },
		...tunnels.items.map((one) => ({ value: one.id, label: one.name }))
	]);

	let typed = $state('');

	const current = $derived(lookup.state);
	const switchEntry = $derived(lookup.entries[LOOKUP_KEY]);
	const routeEntry = $derived(lookup.entries[LOOKUP_ROUTE_KEY]);

	/* Where the switch stands, said under it. A key that is missing or locked is named here too,
	   because it is what stands between "On" and anything being sent. */
	const status = $derived(
		!current?.on
			? COPY.status.off
			: !current.key_set
				? COPY.status.noKey
				: !current.key_ready
					? COPY.status.locked
					: COPY.status.on
	);

	function openMore(): void {
		drilldown.open(COPY.more.title, morePage, COPY.more.open);
	}

	/* A deep link to the route opens the page that draws it. */
	$effect(() => drilldown.own([LOOKUP_ROUTE_KEY], openMore));

	onMount(() => {
		void lookup.load();
		void tunnels.load();
	});

	/* The switch and the route are settings: moved in another window, or by another admin, this
	   block follows. Nothing on it holds an unsent edit but the key box, which a re-read leaves
	   alone. */
	whenChanged(settingChanges, () => void lookup.load());
	/* And the counts beside the lookup task follow the work as it happens: every answer kept rings
	   the work's bell (a lookup settling, a walk ending) and a song named on a file rings the
	   library's. Without these the count would stand still through a whole run until a reload. */
	whenChanged(jobChanges, () => void lookup.load());
	whenChanged(libraryChanges, () => void lookup.load());

	async function save(event: SubmitEvent) {
		event.preventDefault();
		const key = typed.trim();
		if (!key) return;
		// Cleared whatever the answer: a refused key is typed again, and a box still holding one is
		// the key sitting on the screen.
		typed = '';
		await lookup.setKey(key);
	}
</script>

{#snippet morePage()}
	<SettingGroup help={COPY.more.help}>
		{#if routeEntry && current}
			<LabelledRow id="music.lookup_route" label={routeEntry.label} help={routeEntry.help}>
				<Select
					label={routeEntry.label}
					value={current.route ?? DIRECT}
					options={ways}
					onValueChange={(next: string) => void lookup.route(next === DIRECT ? null : next)}
				/>
			</LabelledRow>
		{/if}
	</SettingGroup>
{/snippet}

{#if lookup.failed}
	<SettingGroup id="music.acoustid" heading={COPY.heading}>
		<Problem message={COPY.loadFailed} />
	</SettingGroup>
{:else if !current || !switchEntry}
	<Skeleton lines={3} />
{:else}
	<!-- The switch first, and where it stands in the shaded box under it, as every Recognition
	     feature's is. What it sends off this device is the box's second line, never folded away. -->
	<SettingGroup id="music.acoustid">
		<SettingRow
			entry={switchEntry}
			value={current.on}
			showDisclosure={false}
			onchange={(next: unknown) => void lookup.turn(next === true)}
		/>
		<RecognitionNote
			{status}
			ready={current.on && current.key_set && current.key_ready}
			caution={current.on && !(current.key_set && current.key_ready)}
		>
			{#if switchEntry.disclosure}<p>{switchEntry.disclosure}</p>{/if}
		</RecognitionNote>

		{#if current.key_set}
			<LabelledRow id="music.acoustid-key" label={COPY.key.label} help={COPY.key.kept}>
				<div class="marks">
					{#if current.key_ready}
						<Badge state="done" label={COPY.key.saved} />
					{:else}
						<Badge state="failed" label={COPY.key.locked} />
					{/if}
					<Button
						tone="ghost"
						icon="delete"
						busy={lookup.busy}
						onclick={() => void lookup.deleteKey()}>{COPY.key.delete}</Button
					>
				</div>
			</LabelledRow>
			{#if !current.key_ready}
				<Problem message={COPY.key.lockedSays} />
			{/if}
		{:else}
			<!-- The same row, at the same address, as the key once it is saved: its name and what
			     happens to it on the left, the box and its Save on the right. -->
			<FormCard onsubmit={save}>
				<FieldRow id="music.acoustid-key" label={COPY.key.label} help={COPY.key.help}>
					{#snippet control({ id, describedBy })}
						<TextInput {id} {describedBy} type="password" autocomplete="off" bind:value={typed} />
					{/snippet}
					{#snippet press()}
						<Button
							type="submit"
							tone="primary"
							icon="save"
							busy={lookup.busy}
							disabled={!typed.trim()}>{COPY.key.save}</Button
						>
					{/snippet}
				</FieldRow>
			</FormCard>
		{/if}
		<Problem message={lookup.problem} />

		{#if current.key_set}
			<ActionRow
				id="music.acoustid-test"
				label={COPY.test.label}
				help={COPY.test.help}
				action={COPY.test.action}
				busy={lookup.testing}
				onclick={() => void lookup.test()}
			/>
			{#if lookup.tested?.ok}
				<p class="said"><Badge state="done" label={COPY.test.worked} /> {lookup.tested.said}</p>
			{:else if lookup.tested}
				<Problem message={lookup.tested.said} />
			{/if}
		{/if}
	</SettingGroup>
{/if}

<!-- When the songs are looked up and when the fingerprints are made: the pane's two task rows,
     between how the lookup behaves and the page that holds the rest. -->
{@render when?.()}

{#if current && routeEntry}
	<!-- No heading: it continues the group above it, as the last row of the section. -->
	<SettingGroup>
		<ActionRow
			id="music.more"
			label={COPY.more.label}
			help={COPY.more.help}
			action={COPY.more.open}
			onclick={openMore}
		/>
	</SettingGroup>
{/if}

<style>
	.marks {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: var(--row-pack, flex-end);
		gap: var(--space-2);
	}

	/* What the test came back with, when it worked. Quiet: it is a reading, not something to act on. */
	.said {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		margin: var(--space-2) 0 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
