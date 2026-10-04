<script lang="ts">
	/*
	 * Files in your own folders that Sift read, would not take, and left exactly where they were.
	 *
	 * The other half of the same refusal that fills Quarantine, and the two are apart because they
	 * have opposite answers to "where is my file". A quarantined file is in a folder of Sift's own
	 * and Sift may move it. One of these is untouched on somebody's own disk: Sift reads a library
	 * and does not rearrange it, so all that exists is a note saying it walked past.
	 *
	 * Which is why the only action here is Try again. There is nothing to delete (the file is
	 * not Sift's) and nothing to import, because letting one through does not push it past the
	 * gate. It forgets the refusal, and the next scan reads the file properly. A file that really is
	 * what Sift thought is refused again, which is the right answer for somebody who pressed this
	 * on a hunch.
	 *
	 * Grouped by folder, because the first thing somebody asks of a list of paths is which drive
	 * they are on.
	 */
	import {
		Button,
		DataRow,
		DataRows,
		Empty,
		Problem,
		SectionHeading,
		Skeleton
	} from '$lib/components/common';
	import { onRecord } from '$lib/shell/when';
	import { libraryChanges } from '$lib/library/changes.svelte';
	import { answered } from '$lib/organize/organize.svelte';
	import { formatBytes } from '$lib/settings-ui/maintenance-state.svelte';
	import { Refused, explain, type Skipped } from '$lib/settings-ui/refused.svelte';

	const refused = new Refused();

	let problem = $state<string | undefined>(undefined);

	$effect(() => {
		void libraryChanges.generation;
		void refused.loadRefused();
	});

	async function letThrough(rootId: string, one: Skipped) {
		problem = await refused.allow(rootId, one.rel_path);
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

	<!-- No sentence here: the screen above draws the card's own, which says exactly this (see
	     `SkippedQueue.survey`), and one fact gets one wording. -->
	{#if refused.loading && !refused.loaded}
		<Skeleton lines={3} />
	{:else if !refused.loaded}
		<Problem message="Skipped files couldn't be loaded. Refresh the page to try again." />
	{:else if refused.skippedCount === 0}
		<Empty scope="page" icon="rule_folder" title="Nothing skipped">
			Files in your folders that Sift can't import appear here.
		</Empty>
	{:else}
		{#each refused.leftAlone as root (root.root_id)}
			<div class="in-root"><SectionHeading band level={2}>{root.root_name}</SectionHeading></div>
			<DataRows
				items={root.rejections}
				key={(one: Skipped) => one.rel_path}
				label="Skipped in {root.root_name}"
			>
				{#snippet row(one: Skipped)}
					<DataRow compact>
						<span class="path">{one.rel_path}</span>
						<span class="why">
							{explain(one.reason, one.detected, one.rel_path)}, last seen {on(one.last_seen_at)}
						</span>
						{#snippet trailing()}
							<span class="data">{formatBytes(one.size_bytes)}</span>
						{/snippet}
						{#snippet actions()}
							<Button
								tone="ghost"
								disabled={refused.busy}
								onclick={() => letThrough(root.root_id, one)}
								aria-label="Try again with {one.rel_path} in the next scan"
							>
								Try again
							</Button>
						{/snippet}
					</DataRow>
				{/snippet}
			</DataRows>
		{/each}
	{/if}
</section>

<style>
	.in-root {
		margin: var(--space-2) 0 var(--space-2);
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
