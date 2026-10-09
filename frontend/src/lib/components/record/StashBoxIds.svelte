<script lang="ts">
	/*
	 * WHICH ENTRY in each stash-box this is: the box, the id, and a link to the server's `page_url`
	 * for it. Read by the page with the header's marks (`sourcesOf`) and handed in; Remove is an
	 * admin's.
	 */
	import { FACT_COLUMNS } from '$lib/components/entity/RecordFacts.svelte';
	import {
		Button,
		ConfirmDialog,
		DataRow,
		DataRows,
		SectionHeading,
		Tooltip
	} from '$lib/components/common';
	import {
		forgetLink,
		problemFrom,
		type LinkSubject,
		type StashBoxLink
	} from '$lib/entity/enrich.svelte';
	import { fields } from '$lib/entity/records.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';

	interface Props {
		subject: LinkSubject;
		id: string;
		name: string;
		links: readonly StashBoxLink[];
		mayForget?: boolean;
		onforgot?: () => void | Promise<void>;
	}

	let { subject, id, name, links, mayForget = false, onforgot }: Props = $props();

	const ACTIONS = 'var(--control-height-sm)';

	let asking = $state<StashBoxLink | null>(null);
	let open = $state(false);
	let busy = $state('');

	/** The same `gave` the record's hover reads, so the two agree. */
	function filledIn(one: StashBoxLink): string {
		const named = (one.gave ?? [])
			.map((key) => fields.one(subject, key)?.label.toLowerCase())
			.filter((label): label is string => Boolean(label));
		if (named.length === 0) return '';
		const list =
			named.length === 1
				? named[0]
				: `${named.slice(0, -1).join(', ')} and ${named[named.length - 1]}`;
		return `Filled in ${list}`;
	}

	async function forget(one: StashBoxLink): Promise<void> {
		if (busy) return;
		busy = one.box_id;
		try {
			await forgetLink(subject, id, one.box_id);
			await onforgot?.();
		} catch (error) {
			toasts.show(problemFrom(error), { tone: 'error' });
		} finally {
			busy = '';
		}
	}
</script>

{#if links.length > 0}
	<div class="ids">
		<SectionHeading band>Stash-boxes</SectionHeading>
		<DataRows
			items={links}
			key={(one) => one.box_id}
			label="Stash-boxes that know {name}"
			columns={FACT_COLUMNS}
			actions={mayForget ? ACTIONS : undefined}
			edges
		>
			{#snippet row(one)}
				<DataRow
					compact
					cells={{ label: box, value: entry }}
					actions={mayForget ? forgetting : undefined}
				/>
				{#snippet box()}<span class="box">{one.box_name}</span>{/snippet}
				{#snippet entry()}
					{#if one.page_url}
						<!-- The page it opens is on the hover label. -->
						<Tooltip label={one.page_url} placement="top" shrinks>
							<a class="id" href={one.page_url} target="_blank" rel="noopener noreferrer external"
								>{one.remote_id}</a
							>
						</Tooltip>
					{:else}
						<span class="id">{one.remote_id}</span>
					{/if}
					{#if filledIn(one)}
						<span class="gave">{filledIn(one)}</span>
					{/if}
				{/snippet}
				{#snippet forgetting()}
					<Tooltip label="Remove">
						<Button
							tone="ghost"
							size="small"
							icon="close"
							busy={busy === one.box_id}
							aria-label="Remove the link to {one.box_name}"
							onclick={() => {
								asking = one;
								open = true;
							}}
						/>
					</Tooltip>
				{/snippet}
			{/snippet}
		</DataRows>
	</div>
{/if}

<!-- Remove asks first: a link taken back is made again only by looking it up. -->
{#if mayForget}
	<ConfirmDialog
		bind:open
		title="Remove the {asking?.box_name ?? 'stash-box'} link from {name}?"
		consequence="Everything it filled in stays. To link it again, use Enrich."
		confirmLabel="Remove"
		onconfirm={() => {
			if (asking) void forget(asking);
			asking = null;
		}}
	/>
{/if}

<style>
	.ids {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.box {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	/* The data face; it breaks anywhere. */
	.id {
		font: var(--text-data);
		overflow-wrap: anywhere;
	}

	span.id {
		color: var(--sift-ink-2);
	}

	.gave {
		display: block;
		font: var(--text-label);
		color: var(--sift-ink-3);
	}
</style>
