<script lang="ts">
	/* Somebody who does not exist yet. */
	import { goto } from '$app/navigation';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import NewEntity from '$lib/components/entity/NewEntity.svelte';
	import { recordFrom } from '$lib/components/entity/record-draft';
	import { people } from '$lib/people/people.svelte';
	import { entityTags } from '$lib/entity/entity-tags.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import { Empty } from '$lib/components/common';
	import { counted } from '$lib/entity/entity-counts';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';

	const crumbs = [{ label: 'People', href: '/people' }, { label: 'New person' }];

	async function create(draft: Record<string, unknown>, cover: File | null): Promise<void> {
		const name = String(draft.name);
		const details = String(draft.details ?? '').trim();
		// One request: the record lands whole or is refused whole, so a refused address never
		// leaves a person made with nothing on them.
		const made = await people.create(name, false, {
			notes: details || null,
			record: recordFrom('person', draft),
			aliases: ((draft.aliases as string[]) ?? []).filter(Boolean),
			links: ((draft.links as string[]) ?? []).filter(Boolean)
		});
		try {
			for (const tag of (draft.tags as { id: string }[] | undefined) ?? []) {
				await entityTags.set('people', made.id, tag.id, true);
			}
			// The picture LAST, because it goes on a row and the row was made by the line above:
			// there is nothing to put a cover on until the create has answered.
			if (cover) await people.uploadCover(made.id, cover);
			toasts.show([thing('person', made.id, name), ' was added'], { tone: 'success' });
			// A pack imported earlier may hold this person's photos under the name: the create
			// claims them, and the one place to say so is here.
			const claimed = made.faces_claimed ?? 0;
			if (claimed > 0) {
				toasts.show(
					[
						`Sift already had ${counted(claimed)} ${claimed === 1 ? 'photo' : 'photos'} of `,
						thing('person', made.id, name),
						' from a file or folder you added. They can be recognized now.'
					],
					{ tone: 'info' }
				);
			}
		} catch {
			// The person exists by this point, so this is not a failed create: it is a record that
			// did not finish.
			toasts.show([thing('person', made.id, name), " was added, but the rest couldn't be saved"], {
				tone: 'error'
			});
		}
		await goto(`/people/${made.id}`);
	}
</script>

{#if session.isAdmin}
	<NewEntity
		subject="person"
		noun="person"
		{crumbs}
		oncreate={create}
		oncancel={() => void leaveFor('/people')}
	/>
{:else}
	<PageFrame {crumbs}>
		<Empty scope="page" icon="lock" title="Only an administrator can add people"
			>Ask whoever runs this library to add them for you.</Empty
		>
	</PageFrame>
{/if}
