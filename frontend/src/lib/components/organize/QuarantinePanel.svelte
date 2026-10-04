<script lang="ts">
	/*
	 * Files Sift downloaded or was handed, refused on the way in, and moved into a folder of its own.
	 *
	 * The pile beside this one (Skipped Files) holds the other half of the same refusal, and the
	 * two are apart because they have opposite answers to "where is my file". These are in Sift's
	 * keeping and Sift moved them, so they can be deleted from here. Those are untouched in
	 * somebody's own folder and Sift will not move them.
	 *
	 * There are no pictures on this screen and there cannot be. A refused file was never imported,
	 * so there is no asset and no thumbnail: what a row can show is the name it arrived under,
	 * what the bytes turned out to be, and when.
	 *
	 * Deleting one is final and says so, in a confirm that names the file.
	 */
	import { onRecord } from '$lib/shell/when';
	import {
		Button,
		ConfirmDialog,
		DataRow,
		DataRows,
		Empty,
		Problem,
		Skeleton
	} from '$lib/components/common';
	import { libraryChanges } from '$lib/library/changes.svelte';
	import { openSettings } from '$lib/settings-ui/settings-view';
	import { answered } from '$lib/organize/organize.svelte';
	import { formatBytes } from '$lib/settings-ui/maintenance-state.svelte';
	import { Refused, aboutQuarantined, type Quarantined } from '$lib/settings-ui/refused.svelte';

	const refused = new Refused();

	let problem = $state<string | undefined>(undefined);
	let discarding = $state<Quarantined | null>(null);
	let discardOpen = $state(false);

	$effect(() => {
		void libraryChanges.generation;
		void refused.loadRefused();
	});

	function askToDiscard(one: Quarantined) {
		discarding = one;
		discardOpen = true;
	}

	async function doDiscard() {
		if (!discarding) return;
		problem = await refused.remove(discarding);
		// The card's count and the record behind it both changed. Nothing else announces it.
		answered.changed();
	}

	/** When something happened, the way somebody reads a date. */
	function on(seconds: number): string {
		return onRecord(seconds);
	}
</script>

<section>
	<Problem message={problem ?? refused.problem} />

	<!-- The screen above already carries the card's own sentence, retention rule and all. See
	     `QuarantineQueue.survey`. Repeating it here would say the same thing twice in two wordings,
	     one under the other. What is left is the one thing that sentence cannot be: a way to change the
	     rule it quotes. -->
	<p class="rule">
		<Button tone="link" onclick={() => openSettings('maintenance', 'quarantine.keep_days')}
			>Choose how long to keep them</Button
		>
	</p>

	{#if refused.loading && !refused.loaded}
		<Skeleton lines={3} />
	{:else if !refused.loaded}
		<Problem message="Quarantined files couldn't be loaded. Refresh the page to try again." />
	{:else if refused.moved.length === 0}
		<Empty scope="page" icon="shield" title="Nothing quarantined">
			Downloads whose bytes weren't the file the page promised appear here.
		</Empty>
	{:else}
		<DataRows items={refused.moved} key={(one: Quarantined) => one.id} label="Quarantined files">
			{#snippet row(one: Quarantined)}
				<DataRow compact>
					<span class="path">{one.original_name}</span>
					<span class="why">{aboutQuarantined(one, on(one.quarantined_at))}</span>
					{#snippet trailing()}
						<span class="data">{formatBytes(one.size_bytes)}</span>
					{/snippet}
					{#snippet actions()}
						<Button
							tone="danger-quiet"
							icon="delete"
							disabled={refused.busy}
							onclick={() => askToDiscard(one)}
							aria-label="Delete {one.original_name} permanently"
						>
							Delete
						</Button>
					{/snippet}
				</DataRow>
			{/snippet}
		</DataRows>
	{/if}
</section>

<ConfirmDialog
	bind:open={discardOpen}
	title="Delete this file?"
	consequence={discarding
		? `${discarding.original_name} is deleted from this device. It was never imported, so your library doesn't change. This is the only copy Sift has, and deleting it can't be undone.`
		: ''}
	confirmLabel="Delete permanently"
	destructive
	onconfirm={doDiscard}
/>

<style>
	.rule {
		margin: 0 0 var(--space-4);
	}

	.data {
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}

	.path {
		overflow-wrap: anywhere;
	}

	/* The sentence under a row's name. Inside the row's own subject rather than a snippet of its
	   own, because a `DataRow` has one subject and everything else about it is trailing or an
	   action, and a second line here is part of what the row IS about, not a figure beside it. */
	.why {
		display: block;
		margin-block-start: var(--space-1);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
		overflow-wrap: anywhere;
	}
</style>
