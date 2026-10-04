<script lang="ts">
	/*
	 * A Photo Set that does not exist yet.
	 *
	 * Its record is one field (the notes), written by a route of its own, so this is the same
	 * two-step every other new screen runs and fails the same way. See `/people/new`.
	 */
	import { goto } from '$app/navigation';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import { api } from '$lib/api/client';
	import NewEntity from '$lib/components/entity/NewEntity.svelte';
	import { photoSets, type PhotoSet } from '$lib/library/photo-sets.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import { Empty } from '$lib/components/common';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';

	const crumbs = [{ label: 'Photo Sets', href: '/photo-sets' }, { label: 'New Photo Set' }];

	async function create(draft: Record<string, unknown>, cover: File | null): Promise<void> {
		const name = String(draft.name);
		const made = await photoSets.create(name);
		try {
			const notes = String(draft.details ?? '').trim() || null;
			if (notes !== null) {
				await api.put<PhotoSet>(`/photo-sets/${made.id}/notes`, { body: { notes } });
			}
			// The picture LAST, because it goes on a row and the row was made by the line above:
			// there is nothing to put a cover on until the create has answered. Inside this try with
			// the rest of the record, so a picture the server refuses says what every other unfinished
			// part of the record says and leaves the thing made.
			if (cover) {
				const form = new FormData();
				form.set('file', cover);
				await api.post<PhotoSet>(`/photo-sets/${made.id}/cover-picture`, { body: form });
			}
			toasts.show([thing('photo_set', made.id, name), ' was added'], { tone: 'success' });
		} catch {
			toasts.show(
				[thing('photo_set', made.id, name), " was added, but the rest couldn't be saved"],
				{ tone: 'error' }
			);
		}
		await goto(`/photo-sets/${made.id}`);
	}
</script>

{#if session.isAdmin}
	<NewEntity
		subject="photo_set"
		noun="Photo Set"
		{crumbs}
		oncreate={create}
		oncancel={() => void leaveFor('/photo-sets')}
	/>
{:else}
	<PageFrame {crumbs}>
		<Empty scope="page" icon="lock" title="Only an administrator can add Photo Sets"
			>Ask whoever runs this library to add one for you.</Empty
		>
	</PageFrame>
{/if}
