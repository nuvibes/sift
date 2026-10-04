<script lang="ts">
	/*
	 * A Site that does not exist yet, drawn as the form its own page opens.
	 *
	 * The same shape as the person's, and the same reason for the order of the writes: the name
	 * makes the row and the details are a second write, because that is what pressing Save on a
	 * Site's own page already does. See `/people/new` for the argument written out in full.
	 */
	import { goto } from '$app/navigation';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import NewEntity from '$lib/components/entity/NewEntity.svelte';
	import { entityTags } from '$lib/entity/entity-tags.svelte';
	import { sites } from '$lib/people/people.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import { Empty } from '$lib/components/common';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';

	const crumbs = [{ label: 'Sites', href: '/sites' }, { label: 'New Site' }];

	async function create(draft: Record<string, unknown>, cover: File | null): Promise<void> {
		const name = String(draft.name);
		const links = ((draft.links as string[]) ?? []).map((one) => one.trim()).filter(Boolean);
		const notes = String(draft.details ?? '').trim() || null;
		const aliases = ((draft.aliases as string[]) ?? []).map((one) => one.trim()).filter(Boolean);
		const parent = String(draft.parent ?? '').trim() || null;
		// One request: the record lands whole or is refused whole, so a refused address or parent
		// never leaves a Site made with nothing on it.
		const made = await sites.create(name, { notes, aliases, parent, links });
		try {
			for (const tag of (draft.tags as { id: string }[] | undefined) ?? []) {
				await entityTags.set('sites', made.id, tag.id, true);
			}
			// The picture LAST, because it goes on a row and the row was made by the line above:
			// there is nothing to put a cover on until the create has answered. Inside this try with
			// the rest of the record, so a picture the server refuses says what every other unfinished
			// part of the record says and leaves the thing made.
			if (cover) await sites.uploadCover(made.id, cover);
			toasts.show([thing('site', made.id, name), ' was added'], { tone: 'success' });
		} catch {
			toasts.show([thing('site', made.id, name), " was added, but the rest couldn't be saved"], {
				tone: 'error'
			});
		}
		await goto(`/sites/${made.id}`);
	}
</script>

{#if session.isAdmin}
	<NewEntity
		subject="site"
		noun="Site"
		{crumbs}
		oncreate={create}
		oncancel={() => void leaveFor('/sites')}
	/>
{:else}
	<PageFrame {crumbs}>
		<Empty scope="page" icon="lock" title="Only an administrator can add Sites"
			>Ask whoever runs this library to add one for you.</Empty
		>
	</PageFrame>
{/if}
