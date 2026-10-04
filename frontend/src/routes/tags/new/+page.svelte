<script lang="ts">
	/*
	 * A tag that does not exist yet.
	 *
	 * A tag is the one kind whose whole record is on its own row, so the create and the record are
	 * two writes to one table rather than writes to several. But the order is the same as every
	 * other new screen's, and so is what happens when the second one is refused. See `/people/new`.
	 */
	import { goto } from '$app/navigation';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import NewEntity from '$lib/components/entity/NewEntity.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { tags } from '$lib/entity/tags.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import { Empty } from '$lib/components/common';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';

	const crumbs = [{ label: 'Tags', href: '/tags' }, { label: 'New tag' }];

	async function create(draft: Record<string, unknown>, cover: File | null): Promise<void> {
		const name = String(draft.name);
		const made = await tags.create(name);
		try {
			await tags.update(made.id, {
				name,
				description: String(draft.description ?? '').trim(),
				category: String(draft.category ?? '').trim(),
				aliases: ((draft.aliases as string[]) ?? []).map((one) => one.trim()).filter(Boolean),
				parent: String(draft.parent ?? '').trim()
			});
			// The picture LAST, because it goes on a row and the row was made by the line above:
			// there is nothing to put a cover on until the create has answered. Inside this try with
			// the rest of the record, so a picture the server refuses says what every other unfinished
			// part of the record says and leaves the thing made.
			if (cover) await tags.uploadCover(made.id, cover);
			toasts.show([thing('tag', made.id, name), ' was added'], { tone: 'success' });
		} catch {
			toasts.show([thing('tag', made.id, name), " was added, but the rest couldn't be saved"], {
				tone: 'error'
			});
		}
		await goto(`/tags/${made.id}`);
	}
</script>

{#if session.isAdmin}
	<NewEntity
		subject="tag"
		noun="tag"
		{crumbs}
		oncreate={create}
		oncancel={() => void leaveFor('/tags')}
	/>
{:else}
	<PageFrame {crumbs}>
		<Empty scope="page" icon="lock" title="Only an administrator can add tags"
			>Ask whoever runs this library to add one for you.</Empty
		>
	</PageFrame>
{/if}
