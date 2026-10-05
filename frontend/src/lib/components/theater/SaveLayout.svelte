<script lang="ts">
	/*
	 * Saving the wall on screen as a Saved Layout, or over one: the name, then everything loading it
	 * will give back, and what it will not. Held by the screen, since the panel shuts under it.
	 */
	import { Button, Modal, TextInput } from '$lib/components/common';
	import Field from '$lib/components/common/Field.svelte';
	import LayoutSnapshot from './LayoutSnapshot.svelte';
	import { presets, NameTaken } from '$lib/theater/presets.svelte';
	import { showing } from '$lib/theater/wall.svelte';
	import { stage } from '$lib/components/shell/stage.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';

	const over = $derived(presets.asking?.over ?? null);
	const wall = $derived(showing.wall?.preset ?? null);

	let name = $state('');
	let keeping = $state(false);

	// The name starts as the one being updated, and empty for a new one, each time it opens.
	$effect(() => {
		if (presets.asking) name = presets.asking.over?.name ?? '';
	});

	async function keep(event: Event) {
		event.preventDefault();
		const called = name.trim();
		if (!called || keeping || wall === null) return;
		keeping = true;
		try {
			if (over) {
				await presets.update(over.id, called, wall);
				toasts.show(`${called} updated`, { tone: 'success' });
			} else {
				await presets.save(called, wall);
				toasts.show(`Saved as ${called}`, { tone: 'success' });
			}
			presets.asking = null;
		} catch (failure) {
			// A name already used is an answer somebody acts on, in the server's own sentence.
			if (failure instanceof NameTaken) toasts.show(failure.message, { tone: 'error' });
			else if (over) toasts.show("That Saved Layout couldn't be updated", { tone: 'error' });
			else toasts.show("This wall couldn't be saved", { tone: 'error' });
		} finally {
			keeping = false;
		}
	}
</script>

<Modal
	bind:open={
		() => presets.asking !== null,
		(next) => {
			if (!next) presets.asking = null;
		}
	}
	portalTo={stage.whatFillsTheWindow}
	title={over ? `Update ${over.name}` : 'Add to Saved Layouts'}
	description={over
		? 'Saves this wall in its place. It stays in Saved Layouts, ready to load again.'
		: "Saves this wall under a name. It'll be in Saved Layouts, ready to load again."}
>
	<form id="save-layout" class="keeping" onsubmit={keep}>
		<Field label="Name">
			{#snippet control({ id: fieldId, describedBy })}
				<!-- svelte-ignore a11y_autofocus: the sheet exists to be typed into -->
				<TextInput
					id={fieldId}
					bind:value={name}
					{describedBy}
					maxlength={80}
					placeholder="Evening wall"
					autocomplete="off"
					autofocus
				/>
			{/snippet}
		</Field>
		{#if wall}
			<div role="group" aria-label="What it keeps">
				<LayoutSnapshot {wall} detailed />
			</div>
		{/if}
		<p class="not-kept">
			Not kept: the file each cell is playing and its position, the loop marks, Quality, speed, the
			volume, and which cell has the sound.
		</p>
	</form>
	{#snippet footer()}
		<div class="decide">
			<Button type="button" onclick={() => (presets.asking = null)} disabled={keeping}>
				Cancel
			</Button>
			<Button
				type="submit"
				form="save-layout"
				tone="primary"
				icon="save"
				busy={keeping}
				disabled={!name.trim() || wall === null}
			>
				Save
			</Button>
		</div>
	{/snippet}
</Modal>

<style>
	.keeping {
		display: grid;
		gap: var(--space-4);
	}

	.not-kept {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* Cancel, then Save on the trailing edge, where every decision here ends. */
	.decide {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
	}
</style>
