<script lang="ts">
	import BarPanel from '$lib/components/common/BarPanel.svelte';
	/* The Saved Layouts, as kept pills like the saved filters, and the press that saves this wall
	   (the dialog is the screen's, `SaveLayout`). */
	import { Button, Empty, Field, TextInput } from '$lib/components/common';
	import { toasts } from '$lib/shell/toasts.svelte';
	import PresetPill from './PresetPill.svelte';
	import { presets, NameTaken, type Preset } from '$lib/theater/presets.svelte';
	import { showing } from '$lib/theater/wall.svelte';
	import { aims, type Aiming } from '$lib/theater/aim';

	interface Props {
		/**
		 * Called once a kept wall has been put up. A phone's sheet closes on it, as every chooser
		 * does once a choice is made; the desk's panel stays, since it hangs from the bar.
		 */
		onopened?: () => void;
	}

	let { onopened }: Props = $props();

	const wall = $derived(showing.wall);

	/** Nothing to mark while no wall is on screen: the same four handlers, doing nothing. */
	const NO_AIM: Aiming = {
		onmouseenter: () => {},
		onmouseleave: () => {},
		onfocus: () => {},
		onblur: () => {}
	};

	/** The wash over every cell while a kept group is pointed at. See the row's own note. */
	const aimAll = $derived(wall ? aims(wall, 'every') : NO_AIM);

	/* Which kept wall a delete is against, so its pill can say so. */
	let working = $state<string | null>(null);
	/** Which kept wall is being renamed, and what to. */
	let renaming = $state<string | null>(null);
	let draft = $state('');

	$effect(() => {
		void (async () => {
			try {
				await presets.ensure();
			} catch {
				toasts.show("Your Saved Layouts couldn't be loaded", { tone: 'error' });
			}
		})();
	});

	function open(kept: Preset) {
		if (wall === null) return;
		wall.adopt(kept);
		onopened?.();
	}

	function startRename(kept: Preset) {
		renaming = kept.id;
		draft = kept.name;
	}

	async function commitRename(kept: Preset) {
		const called = draft.trim();
		if (!called || called === kept.name) {
			renaming = null;
			return;
		}
		try {
			await presets.rename(kept, called);
			renaming = null;
		} catch (failure) {
			if (failure instanceof NameTaken) toasts.show(failure.message, { tone: 'error' });
			else toasts.show("That Saved Layout couldn't be renamed", { tone: 'error' });
		}
	}

	async function forget(kept: Preset) {
		working = kept.id;
		try {
			await presets.remove(kept.id);
		} catch {
			toasts.show("That Saved Layout couldn't be deleted", { tone: 'error' });
			await presets.reload();
		} finally {
			working = null;
		}
	}
</script>

<BarPanel label="Saved Layouts">
	{#if presets.items.length === 0}
		<Empty scope="block">No Saved Layouts yet &mdash; set the cells up and save this one.</Empty>
	{:else}
		<ul>
			{#each presets.items as one (one.id)}
				<!-- Pointing at one washes every cell, since loading it replaces the whole wall; on the row,
				     because the pill is the shared `KeptPill`, and with the bubbling focus pair. -->
				<!-- svelte-ignore a11y_no_noninteractive_element_interactions: the row marks the wall, it is
				     not a control; the pill inside it is what is operated -->
				<li {...aimAll} onfocusin={aimAll.onfocus} onfocusout={aimAll.onblur}>
					{#if renaming === one.id}
						<!-- `data-unfinished` keeps the panel from shutting on a half-typed name. -->
						<div class="renaming" data-unfinished>
							<Field label="Name" hideLabel>
								{#snippet control({ id })}
									<!-- svelte-ignore a11y_autofocus: the row that opened this exists to type into -->
									<TextInput
										{id}
										bind:value={draft}
										maxlength={80}
										autofocus
										aria-label="Rename {one.name}"
										onkeydown={(event) => {
											if (event.key === 'Enter') void commitRename(one);
											if (event.key === 'Escape') renaming = null;
										}}
									/>
								{/snippet}
							</Field>
							<Button tone="ghost" size="small" onclick={() => (renaming = null)}>Cancel</Button>
							<Button size="small" icon="save" onclick={() => void commitRename(one)}>Save</Button>
						</div>
					{:else}
						<PresetPill
							kept={one}
							busy={working === one.id}
							onopen={open}
							onupdate={(kept) => presets.ask(kept)}
							onrename={startRename}
							onremove={(kept) => void forget(kept)}
						/>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	<div class="keeping">
		<Button size="small" icon="save" onclick={() => presets.ask()}>Save as new</Button>
	</div>
</BarPanel>

<style>
	/* A WRAPPING ROW of pills, which is what the saved filters are. A column of full-width rows
	   would make eight kept walls eight lines of mostly empty panel. */
	ul {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	li {
		display: flex;
		align-items: center;
	}

	.renaming {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.keeping {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}
</style>
