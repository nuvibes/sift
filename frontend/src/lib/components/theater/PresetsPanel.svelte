<script lang="ts">
	import BarPanel from '$lib/components/common/BarPanel.svelte';
	/*
	 * The walls that have been kept, and the two ways to keep this one.
	 *
	 * A panel on the bar rather than a menu off a button, as every other panel here: while the
	 * window is filled the bar is along the bottom edge, and a menu placed against a button would
	 * open off the screen.
	 *
	 * Its rows are the app's kept pill, as kept filters are: a named piece of setting-up somebody
	 * reaches for later, with a hover preview, rename, a right-click, and a delete kept away from
	 * the thing that opens it. See `KeptPill` for the argument and `PresetPill` for what a preset's
	 * preview draws.
	 */
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

	let naming = $state(false);
	let name = $state('');
	/** The saved wall currently loaded, so Save can offer to change it rather than make a second. */
	let loaded = $state<{ id: string; name: string } | null>(null);
	let busy = $state(false);
	/*
	 * WHICH kept wall a write is against, so the pill it belongs to can say so.
	 *
	 * `busy` above is the PANEL's (it guards Save and stops two writes overlapping) and it
	 * cannot answer "is this row the one working", which is the question `KeptPill.busy` asks. A
	 * kept filter's pill says this, and so does a wall's: without it, updating or deleting one
	 * would be a press with no answer until the list moved underneath it.
	 */
	let working = $state<string | null>(null);
	/** Which kept wall is being renamed, and what to. */
	let renaming = $state<string | null>(null);
	let draft = $state('');

	$effect(() => {
		void (async () => {
			try {
				await presets.ensure();
			} catch {
				toasts.show("Your Layout Presets couldn't be loaded", { tone: 'error' });
			}
		})();
	});

	async function keep() {
		if (!name.trim() || busy || wall === null) return;
		busy = true;
		try {
			const made = await presets.save(name.trim(), wall.preset);
			loaded = { id: made.id, name: made.name };
			naming = false;
			name = '';
			toasts.show(`Saved as ${made.name}`, { tone: 'success' });
		} catch (failure) {
			// A name already used is an ordinary answer somebody acts on, and the server's sentence
			// names the preset. Anything else is a fault and says so in the app's own words.
			if (failure instanceof NameTaken) toasts.show(failure.message, { tone: 'error' });
			else toasts.show("That Layout Preset couldn't be saved", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	async function keepChanges(kept: Preset) {
		if (busy || wall === null) return;
		busy = true;
		working = kept.id;
		try {
			await presets.update(kept.id, kept.name, wall.preset);
			toasts.show(`${kept.name} updated`, { tone: 'success' });
		} catch (failure) {
			if (failure instanceof NameTaken) toasts.show(failure.message, { tone: 'error' });
			else toasts.show("That Layout Preset couldn't be updated", { tone: 'error' });
		} finally {
			busy = false;
			working = null;
		}
	}

	function open(kept: Preset) {
		if (wall === null) return;
		wall.adopt(kept);
		loaded = { id: kept.id, name: kept.name };
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
			if (loaded?.id === kept.id) loaded = { id: kept.id, name: called };
			renaming = null;
		} catch (failure) {
			if (failure instanceof NameTaken) toasts.show(failure.message, { tone: 'error' });
			else toasts.show("That Layout Preset couldn't be renamed", { tone: 'error' });
		}
	}

	async function forget(kept: Preset) {
		if (loaded?.id === kept.id) loaded = null;
		working = kept.id;
		try {
			await presets.remove(kept.id);
		} catch {
			toasts.show("That Layout Preset couldn't be deleted", { tone: 'error' });
			await presets.reload();
		} finally {
			working = null;
		}
	}
</script>

<BarPanel label="Saved Layout Presets">
	{#if presets.items.length === 0}
		<Empty scope="block">No Layout Presets saved yet — set the cells up and save this one.</Empty>
	{:else}
		<ul>
			{#each presets.items as one (one.id)}
				<!--
					POINTING AT A KEPT GROUP WASHES EVERY CELL, which is `aims`: the one mark this
					screen uses for "this is what you are about to change".

					Every cell, and not the cell a row of its preview is numbered for: loading a group
					replaces the whole wall (see `Wall.adopt`), so the honest answer to "which cells does
					this press change" is all of them. The numbers inside its bubble describe the SAVED
					group, not the wall on screen: cell 2 of a group is not cell 2 of this wall until it
					has been loaded, so washing one live cell from one of those lines would point at a
					rectangle the line is not about.

					On the row rather than inside the pill because the pill is the shared `KeptPill` and
					knows nothing of walls. `focusin`/`focusout` as well as the focus pair `aims` hands a
					control, because focus lands on the pill's own button inside this row and only the
					bubbling pair reaches the row from there. Spread, so the row carries the attachment
					that lets the mark go if the row is taken away under the pointer: a group deleted
					while pointed at.
				-->
				<!-- svelte-ignore a11y_no_noninteractive_element_interactions: the row marks the wall, it is
				     not a control; the pill inside it is what is operated -->
				<li {...aimAll} onfocusin={aimAll.onfocus} onfocusout={aimAll.onblur}>
					{#if renaming === one.id}
						<!-- The app's field, with a Save and a Cancel, rather than a bare box that commits
						     when it loses focus. `data-unfinished` keeps the panel from falling shut under
						     the pointer on its way to the keyboard and taking a half-typed name with
						     it, as the filter panel's own attribute does there. -->
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
							<!-- Cancel then Save: the confirming control is last and on the trailing edge,
							     which is where this application puts the thing that finishes a question. -->
							<Button tone="ghost" size="small" onclick={() => (renaming = null)}>Cancel</Button>
							<Button size="small" icon="save" onclick={() => void commitRename(one)}>Save</Button>
						</div>
					{:else}
						<PresetPill
							kept={one}
							busy={working === one.id}
							onopen={open}
							onupdate={(kept) => void keepChanges(kept)}
							onrename={startRename}
							onremove={(kept) => void forget(kept)}
						/>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	<!--
		`data-unfinished` while a name is being typed, which is what makes keeping one possible at
		all.

		The panel falls shut when the pointer leaves it, after a grace. Typing into it is vetoed
		while the caret is in the box, but the trip from the box to the Save button beside it is
		a pointer moving across the panel, and any wobble off it during the grace would shut the
		panel and take `naming` with it: the name typed, Save reached for, and nothing there, with
		no error, no toast and no saved group.

		The attribute is what the rename form two rows up already carries, for exactly this. It
		vetoes the hover close only: Escape and the trigger still shut the panel, since a mode you
		cannot leave deliberately would be worse than the problem it solves.
	-->
	<div class="keeping" data-unfinished={naming ? '' : undefined}>
		{#if naming}
			<!-- Typed into at once, and Enter keeps it, as the rename form above does: with the caret
			     left nowhere, the name would go to whatever had focus before the press. -->
			<!-- svelte-ignore a11y_autofocus: the press that opened this exists to type into it -->
			<TextInput
				type="text"
				bind:value={name}
				maxlength={80}
				placeholder="Call it something"
				aria-label="A name for this Layout Preset"
				autofocus
				onkeydown={(event) => {
					if (event.key === 'Enter') void keep();
					if (event.key === 'Escape') naming = false;
				}}
			/>
			<Button size="small" tone="primary" icon="save" onclick={keep} disabled={busy}>Save</Button>
			<Button size="small" onclick={() => (naming = false)}>Cancel</Button>
		{:else}
			<Button size="small" icon="save" onclick={() => (naming = true)}>Save as new</Button>
		{/if}
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

	/* `:global`, because the box is `TextInput`'s own element, compiled in that file's scope. Only
	   how wide it may grow is local: the height, edge, corner, ground, ink and face are app.css's,
	   and the focus ring is the global `:focus-visible` rule's. */
	.keeping :global(.text-input) {
		flex: 1;
		min-inline-size: 0;
		max-inline-size: 260px;
	}
</style>
