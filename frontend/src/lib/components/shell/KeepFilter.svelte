<script lang="ts">
	/*
	 * Keeping what is filtering a screen under a name: a sheet with the name to type and the filters
	 * it keeps, drawn as the chips they are on the bar, so what is being named is in front of whoever
	 * names it. The kept filters are listed at the foot of the Filter panel.
	 */
	import type { Snippet } from 'svelte';
	import { Button, Modal, TextInput } from '$lib/components/common';
	import Field from '$lib/components/common/Field.svelte';
	import { savedSearches } from '$lib/search/saved-searches.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import type { Subject } from './facet-labels';

	interface Props {
		open: boolean;
		/** The query kept, the screen's own constraint included. */
		query: string;
		/** The wall it is kept on, which is where it is offered back. */
		subject: Subject;
		/** The chips in force, drawn as the bar draws them with nothing on them acting. */
		chips: Snippet;
	}

	let { open = $bindable(), query, subject, chips }: Props = $props();

	let name = $state('');
	/* The write is in flight: Save turns and refuses a second press, Cancel waits for it. */
	let keeping = $state(false);

	async function keep(event: Event) {
		event.preventDefault();
		const called = name.trim();
		if (!called || keeping) return;
		keeping = true;
		try {
			await savedSearches.save(called, query, subject);
			/* Where it is and no more: a direction in a sentence goes stale with the layout. */
			toasts.show(`Saved as "${called}" in the Filter panel`);
			open = false;
			name = '';
		} catch {
			toasts.show("That search couldn't be saved. Try again.", { tone: 'error' });
		} finally {
			keeping = false;
		}
	}
</script>

<Modal
	bind:open
	title="Add to saved filters"
	description="Saves the filters below under a name. They'll be in the Filter panel, ready to use again."
>
	<form id="keep-filter" class="keeping" onsubmit={keep}>
		<Field label="Name">
			{#snippet control({ id: fieldId, describedBy })}
				<!-- svelte-ignore a11y_autofocus: the sheet exists to be typed into -->
				<TextInput
					id={fieldId}
					bind:value={name}
					{describedBy}
					maxlength={120}
					placeholder="Beach videos"
					autocomplete="off"
					autofocus
				/>
			{/snippet}
		</Field>
		<div class="kept-chips" role="group" aria-label="The filters it keeps">
			{@render chips()}
		</div>
	</form>
	{#snippet footer()}
		<div class="decide">
			<Button type="button" onclick={() => (open = false)} disabled={keeping}>Cancel</Button>
			<Button
				type="submit"
				form="keep-filter"
				tone="primary"
				icon="save"
				busy={keeping}
				disabled={!name.trim()}
			>
				Save
			</Button>
		</div>
	{/snippet}
</Modal>

<style>
	/* The name, then what it keeps. */
	.keeping {
		display: grid;
		gap: var(--space-4);
	}

	/* The chips wrap as they do on the bar, at the bar's own spacing. */
	.kept-chips {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	/* Cancel, then Save on the trailing edge, where every decision here ends. */
	.decide {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
	}
</style>
