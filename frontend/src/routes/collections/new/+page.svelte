<script lang="ts">
	/* A collection that does not exist yet. */
	import { goto } from '$app/navigation';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import NewEntity from '$lib/components/entity/NewEntity.svelte';
	import { collections } from '$lib/library/collections.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import { Empty } from '$lib/components/common';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';

	const crumbs = [{ label: 'Collections', href: '/collections' }, { label: 'New collection' }];

	async function create(draft: Record<string, unknown>, cover: File | null): Promise<void> {
		const name = String(draft.name);
		const made = await collections.create(name);
		try {
			// The picture LAST, because it goes on a row and the row was made by the line above.
			if (cover) await collections.uploadCover(made.id, cover);
			toasts.show([thing('collection', made.id, name), ' was added'], { tone: 'success' });
		} catch {
			toasts.show(
				[thing('collection', made.id, name), " was added, but the picture couldn't be saved"],
				{ tone: 'error' }
			);
		}
		await goto(`/collections/${made.id}`);
	}
</script>

{#if session.isAdmin}
	<NewEntity
		noun="collection"
		{crumbs}
		oncreate={create}
		oncancel={() => void leaveFor('/collections')}
	/>
{:else}
	<PageFrame {crumbs}>
		<Empty scope="page" icon="lock" title="Only an administrator can add collections"
			>Ask whoever runs this library to add one for you.</Empty
		>
	</PageFrame>
{/if}
