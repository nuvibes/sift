<script lang="ts">
	/* A song that does not exist yet. */
	import { goto } from '$app/navigation';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import { api } from '$lib/api/client';
	import NewEntity from '$lib/components/entity/NewEntity.svelte';
	import { songs, type Song } from '$lib/entity/songs.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import { Empty } from '$lib/components/common';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';

	const crumbs = [{ label: 'Music', href: '/songs' }, { label: 'New song' }];

	async function create(draft: Record<string, unknown>, cover: File | null): Promise<void> {
		const name = String(draft.name);
		const made = await songs.create(name);
		try {
			const notes = String(draft.details ?? '').trim() || null;
			if (notes !== null) {
				await api.put<Song>(`/songs/${made.id}/notes`, { body: { notes } });
			}
			// The picture LAST: there is nothing to put a cover on until the create has answered.
			if (cover) {
				const form = new FormData();
				form.set('file', cover);
				await api.post<Song>(`/songs/${made.id}/cover-picture`, { body: form });
			}
			toasts.show([thing('song', made.id, name), ' was added'], { tone: 'success' });
		} catch {
			toasts.show([thing('song', made.id, name), " was added, but the rest couldn't be saved"], {
				tone: 'error'
			});
		}
		await goto(`/songs/${made.id}`);
	}
</script>

{#if session.isAdmin}
	<NewEntity
		subject="song"
		noun="song"
		maxlength={200}
		{crumbs}
		oncreate={create}
		oncancel={() => void leaveFor('/songs')}
	/>
{:else}
	<PageFrame {crumbs}>
		<Empty scope="page" icon="lock" title="Only an administrator can add songs"
			>Ask whoever runs this library to add one for you.</Empty
		>
	</PageFrame>
{/if}
