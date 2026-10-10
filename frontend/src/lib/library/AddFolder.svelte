<script lang="ts">
	/* Adding a folder to the library: the button, and the dialog behind it. */
	import { Button, Problem, Scroller } from '$lib/components/common';
	import Modal from '$lib/components/common/Modal.svelte';
	import FolderPicker from '$lib/library/FolderPicker.svelte';
	import type { Library } from '$lib/library/library.svelte';
	import type { Picker } from '$lib/library/picker.svelte';
	import type { Grants } from '$lib/library/grants-state.svelte';
	import { bridge } from '$lib/bridge';
	import { toasts } from '$lib/shell/toasts.svelte';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';

	/* `offer` is where it stands. In Settings it is one row of a pane: the name and its sentence
	 * on the left, the button at the far edge. */
	let {
		library,
		grants,
		picker,
		scan = true,
		offer = false
	}: {
		library: Library;
		grants: Grants;
		picker: Picker;
		scan?: boolean;
		offer?: boolean;
	} = $props();

	const NAME = 'Add a folder';
	const SAID = 'Its files are imported, and new ones as they appear in it.';

	let addOpen = $state(false);
	let addError = $state<string | undefined>(undefined);

	/* Add pressed while the picker stands on a list rather than in a folder. */
	let pressedAtTop = $state(false);
	const warnAtTop = $derived(pressedAtTop && picker.atTopLevel);

	async function openAdd() {
		addError = undefined;
		pressedAtTop = false;
		if (grants.canAdd) {
			await addByDialog();
			return;
		}
		// Opened fresh each time, so the dialog starts at the top of the media area rather than
		// wherever it was left after the last folder was added.
		await picker.open();
		addOpen = true;
	}

	/* The desktop's one gesture. `scan` goes to `addRoot` as the screen decided it: `false` here
	 * is a folder added and NOT read, so it must never be passed for any other meaning. */
	async function addByDialog() {
		const chosen = await bridge.chooseFolder();
		// Closing the dialog without choosing is not an error and must not draw one.
		if (chosen === null) return;
		await grants.ensure(chosen);
		const refusal = await library.addRoot(chosen, scan);
		if (refusal) {
			// A toast rather than a line under a picker: there is no picker on screen in this flow,
			// and the folder they chose is gone from view the moment the dialog closes.
			toasts.show(refusal, { tone: 'error' });
			return;
		}
		await picker.open();
	}

	async function add(event: SubmitEvent) {
		event.preventDefault();
		// The picker stands in a pressed folder only once it has opened: until then, the one above.
		if (picker.loading) return;
		addError = undefined;
		if (picker.atTopLevel) {
			pressedAtTop = true;
			return;
		}
		const chosen = picker.selected;
		if (!chosen) return;

		/* A folder picked off the machine has never been handed over, so it is handed over here,
		   silently, because the grant and the add read as one job to the person doing them, and
		   `ensure` says nothing when the folder was already granted or sits inside one that was. */
		if (picker.scope === 'machine') await grants.ensure(chosen.path);

		const refusal = await library.addRoot(chosen.path, scan);
		if (refusal) {
			// Under the picker, not in a toast that slides away.
			addError = refusal;
			return;
		}
		addOpen = false;
		// Back to the top.
		await picker.open();
	}
</script>

{#snippet press()}
	<Button tone="secondary" size="small" icon="add" onclick={() => void openAdd()}
		>Add a folder</Button
	>
{/snippet}

{#if offer}
	<div class="offer">
		<p class="offer-name">{NAME}</p>
		<p class="offer-said">{SAID}</p>
		{@render press()}
	</div>
{:else}
	<LabelledRow id="library.add" label={NAME} help={SAID}>
		{@render press()}
	</LabelledRow>
{/if}

<Modal bind:open={addOpen} title="Add a folder" sheetClass="add-sheet" scrolls={false}>
	<!--
		The picker IS the dialog, rather than a box inside a form: the thing you came here to do (point
		at a folder) is the largest part of the screen, not the smallest part under a name field and
		two paragraphs.
	-->
	<form onsubmit={add} class="add">
		<Scroller viewportClass="add-scroll">
			<div class="add-body">
				<div class="field">
					<FolderPicker
						{picker}
						device={!grants.canAdd}
						labelledBy="picker-label"
						describedBy={addError ? 'picker-help picker-error' : 'picker-help'}
					/>
					<p class="help" id="picker-help">
						Click through to the folder you want. The files inside it are imported.
					</p>
					{#if addError}
						<Problem id="picker-error" message={addError} />
					{/if}
				</div>

				<!-- Fired by an Add pressed on the list itself, the moment it matters, never standing
				     above the picker before anything was pressed. -->
				{#if warnAtTop}
					<p class="warn" role="alert">
						{picker.scope === 'machine'
							? 'These are the drives on the computer Sift runs on, not folders themselves. Click into one and pick a folder inside it.'
							: 'This is the list of folders Sift already has, not a folder itself. Pick one of them, or click into it and pick a folder inside.'}
					</p>
				{/if}

				<!--
					Only where the folder cannot be written, and only as a fact: there is no switch to
					turn on.
				-->
				{#if picker.selected && !picker.writable}
					<p class="quiet">
						{picker.readOnlyMount
							? 'This folder was handed to Sift as read-only. Sift can read everything in it and will not be able to delete or move anything there.'
							: 'Sift is not allowed to write in this folder, so it can read everything in it but cannot delete or move anything there.'}
					</p>
				{/if}
			</div>
		</Scroller>

		<div class="dialog-actions">
			<Button onclick={() => (addOpen = false)}>Cancel</Button>
			<Button type="submit" tone="primary" icon="add" disabled={library.busy || picker.loading}>
				Add folder
			</Button>
		</div>
	</form>
</Modal>

<style>
	/* The wall's act: one column, centred under the sentence above it, the way `Empty` stacks
	   its own glyph and sentence. */
	.offer {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-1);
		text-align: center;
	}

	.offer-name {
		margin: 0;
		font: var(--text-label);
		color: var(--sift-ink);
	}

	.offer-said {
		margin: 0 0 var(--space-2);
		max-inline-size: 46ch;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* One size, whatever is inside it. */
	:global(.add-sheet) {
		display: flex;
		flex-direction: column;
		block-size: min(80vh, 42rem);
	}

	.add {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		margin-block-start: var(--space-5);
		padding: var(--space-5);
		/* The card's light, its edge under this transparent border (see `--sift-card`). */
		background: var(--sift-card);
		border: 1px solid transparent;
		border-radius: var(--radius-lg);
		max-inline-size: 34rem;
		flex: 1;
		min-block-size: 0;
	}

	/* The same four parts a Field draws, for a widget a Field cannot label. */
	.field {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.field .help {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The buttons stay put; only what is above them scrolls. */
	.add-body {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	/* The region takes the flexible height. It is the thing that scrolls, so it is the thing
	   that has to be told how much room it has. */
	.add :global(.scroll-root) {
		flex: 1;
		min-block-size: 0;
	}

	/* The picker's own list height is declared ONCE, by the picker itself: `.picker
	   :global(.scroll-root)` in `FolderPicker.svelte`. */

	/* The refusal that fires at the moment of picking, rather than prose standing above the
	   picker that nobody reads until it is too late. */
	.warn {
		margin: 0;
		color: var(--sift-warn);
		font: var(--text-body-sm);
	}

	/* The row of answers at the foot of the add dialog: apart from the scrolling body above
	   them, and against the far end so the primary is where it is in every other sheet, not two
	   buttons hard against the left edge under an unseparated body. */
	.dialog-actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
		padding-block-start: var(--space-3);
		border-block-start: 1px solid var(--sift-line);
	}
</style>
