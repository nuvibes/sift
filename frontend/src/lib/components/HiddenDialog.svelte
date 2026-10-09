<script lang="ts">
	/*
	 * Why this is hidden and the way back out, nothing about sharing. The list is empty while the
	 * vault is shut, since its names are the concealed things.
	 */
	import { Button, Modal } from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import { libraryChanges } from '$lib/library/changes.svelte';
	import { HIDDEN_VERB } from '$lib/library/sharing-marks';
	import { loadCounts } from '$lib/entity/related.svelte';
	import {
		NOUNS,
		fetchVaultSources,
		hiddenLabel,
		includesSitesWithin,
		unhide,
		type ShareTarget,
		type VaultSource
	} from '$lib/library/sharing';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { vault } from '$lib/shell/vault.svelte';

	interface Props {
		open?: boolean;
		target: ShareTarget | null;
	}

	let { open = $bindable(false), target }: Props = $props();

	let sources = $state<VaultSource[]>([]);
	let loading = $state(false);
	let failed = $state(false);
	let taking = $state<string | null>(null);

	/* Asked again whenever the vault moves (`generation`). */
	$effect(() => {
		void vault.generation;
		const asking = open ? target : null;
		if (!asking) {
			sources = [];
			failed = false;
			return;
		}
		let current = true;
		loading = true;
		failed = false;
		void fetchVaultSources(asking)
			.then((found) => {
				if (current) sources = found;
			})
			.catch(() => {
				if (current) failed = true;
			})
			.finally(() => {
				if (current) loading = false;
			});
		return () => {
			current = false;
		};
	});

	/* Immediately: there is nothing to stage it against. */
	async function takeOut(source: VaultSource) {
		taking = source.source_id ?? 'here';
		try {
			await unhide(source);
			sources = sources.filter((each) => each.source_id !== source.source_id);
			libraryChanges.changed();
			if (sources.length === 0) open = false;
		} catch {
			toasts.show("That couldn't be unhidden", { tone: 'error' });
		} finally {
			taking = null;
		}
	}

	const noun = $derived(NOUNS[target?.type ?? 'item']?.[0] ?? 'item');

	/* The Sites within (`sites_within`), as the sharing sheet says it; its own effect. */
	let within = $state(0);

	$effect(() => {
		const only = open && target?.type === 'site' ? target.id : null;
		if (!only) {
			within = 0;
			return;
		}
		let current = true;
		void loadCounts('site', only).then((counts) => {
			if (current) within = counts.sites_within ?? 0;
		});
		return () => {
			current = false;
		};
	});
</script>

<!-- `sheetClass` carries the wrap rule into the Modal. -->
<Modal bind:open title="Hidden" description={target?.label ?? ''} sheetClass="hidden-sheet">
	<!-- What hiding IS: the opposite of the common reading of the crossed-out eye. -->
	<p class="what">
		Hidden is about your own screen. Nobody else is affected, and no share you hold overrides it.
	</p>

	{#if includesSitesWithin(within)}
		<p class="reaches">
			<Icon name="hub" size={16} />
			<span>{includesSitesWithin(within)}</span>
		</p>
	{/if}

	{#if failed}
		<p class="note" role="alert">What is hiding this couldn't be read.</p>
	{:else if loading}
		<p class="note">Looking&hellip;</p>
	{:else if sources.length === 0}
		<!-- With Hidden shut an empty list means "not telling you". -->
		<p class="note">
			{vault.unlocked
				? `Nothing you hid is concealing this ${noun}.`
				: `Unlock Hidden to see what is concealing this ${noun}.`}
		</p>
	{:else}
		<ul class="by">
			{#each sources as source, at (`${at}:${source.source_type}:${source.source_id ?? ''}`)}
				{@const words = hiddenLabel(source, target?.type ?? 'item')}
				<li>
					<!-- Solid on this very thing, hollow on something above. -->
					<Icon name="visibility_off" size={16} filled={source.here} />
					<!-- The sharing sheet's pieces, at its weights. -->
					<span class="said">
						<span class="verb">{HIDDEN_VERB}</span>
						{words.lead}
						{#if words.name}<span class="named">{words.name}</span>{/if}
					</span>
					<Button
						size="small"
						icon="visibility"
						busy={taking === (source.source_id ?? 'here')}
						onclick={() => void takeOut(source)}
					>
						Unhide
					</Button>
				</li>
			{/each}
		</ul>
	{/if}
</Modal>

<style>
	.what {
		margin: 0 0 var(--space-3);
		color: var(--sift-ink-2);
		font: var(--text-body);
		max-width: 52ch;
	}

	.reaches {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin: 0 0 var(--space-3);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.note {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body);
	}

	.by {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.by li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-md);
		background: var(--sift-surface-3);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* The sentence wraps, so the button stays at the edge. */
	.said {
		display: inline-flex;
		align-items: baseline;
		flex-wrap: wrap;
		gap: 0.25em;
		flex: 1;
		min-inline-size: 0;
		overflow-wrap: anywhere;
	}

	.verb {
		color: var(--sift-ink);
	}

	/* Weight on the NAME, not the verb that never varies. */
	.named {
		font-weight: 600;
		color: var(--sift-ink);
	}
</style>
