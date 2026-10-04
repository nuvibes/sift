<script lang="ts">
	/*
	 * Adding a folder to the library: the button, and the dialog behind it.
	 *
	 * ONE component for every place a folder is added: the library panel in Settings, and the
	 * empty Browse wall of an install that has no folders yet, which would otherwise land on
	 * "Nothing here yet. Add some files" with no way to add any short of finding Settings. A second
	 * copy of this flow would be two places where handing a folder over and adding it are kept in
	 * step, and the grant half is the one a copy forgets.
	 *
	 * The instances are handed in rather than made here, because the screen drawing this owns them:
	 * the library list has to show the folder the moment it is added (`library.load` runs inside
	 * `addRoot`), and the Settings panel draws the granted list and the picker's state beside this.
	 *
	 * ONE gesture on the machine itself, two only where it has to be. On the desktop the operating
	 * system's own dialog is the consent as well as the choice, so the folder is handed over and
	 * added in one go. A browser cannot open that dialog (which is the whole security property of
	 * it), so there the picker walks what the server may show, and the add is a second press.
	 *
	 * `scan` says whether the folder is read at once. Settings reads it (somebody adding a folder
	 * to a working library expects it to fill); the empty Browse wall does not, because what
	 * follows there is the Scan Now offer with its warning about the hours a first read takes:
	 * the same order first run keeps, see `NewRoot.scan` on the server.
	 */
	import { Button, Problem, Scroller } from '$lib/components/common';
	import Modal from '$lib/components/common/Modal.svelte';
	import FolderPicker from '$lib/library/FolderPicker.svelte';
	import type { Library } from '$lib/library/library.svelte';
	import type { Picker } from '$lib/library/picker.svelte';
	import type { Grants } from '$lib/library/grants-state.svelte';
	import { bridge } from '$lib/bridge';
	import { toasts } from '$lib/shell/toasts.svelte';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';

	/*
	 * `offer` is where it stands. In Settings it is one row of a pane: the name and its sentence on
	 * the left, the button at the far edge. On an empty wall it is the wall's one act, under the
	 * glyph and the sentence that `Empty` centres, so it is one centred column too: the name, the
	 * sentence, the button. A pane's two columns there stretch across the wall and put the button
	 * a screen's width from the words it answers.
	 */
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
	const SAID = 'Its files are imported, and new ones as they arrive.';

	let addOpen = $state(false);
	let addError = $state<string | undefined>(undefined);

	/* Add pressed while the picker stands on a list rather than in a folder. The warning waits for
	   that press: shown from the start it would read as a fault with a dialog nobody had used yet. */
	let pressedAtTop = $state(false);
	const warnAtTop = $derived(pressedAtTop && picker.atTopLevel);

	/* What the list is, said above it: the folders Sift has at the top of the granted scope, the
	   drives at the top of the computer, and otherwise the question the list answers. */
	const listName = $derived(
		!picker.atTopLevel
			? 'Which folder'
			: picker.scope === 'machine'
				? 'Drives where Sift runs'
				: 'Folders Sift already has'
	);

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

	/*
	 * The desktop's one gesture.
	 *
	 * `scan` goes to `addRoot` as the screen decided it: `false` here is a folder added and NOT
	 * read, so it must never be passed for any other meaning. The success sentence is `addRoot`'s
	 * own: it knows which of the two happened.
	 */
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
		   `ensure` says nothing when the folder was already granted or sits inside one that was.
		   Anything genuinely wrong with the folder is raised again by `addRoot` below, in a sentence
		   about the thing they actually asked for. */
		if (picker.scope === 'machine') await grants.ensure(chosen.path);

		const refusal = await library.addRoot(chosen.path, scan);
		if (refusal) {
			// Under the picker, not in a toast that slides away. Every refusal here is about the
			// folder they just chose, and it is the thing they have to change.
			addError = refusal;
			return;
		}
		addOpen = false;
		// Back to the top. The folder just added is now a library rather than somewhere to pick, and
		// leaving the picker standing inside it invites adding it again, which is refused, in a
		// sentence about overlapping folders that would be baffling to somebody who thought they
		// were starting over.
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
		The picker IS the dialog, rather than a box inside a form: the thing you came here to do
		(point at a folder) is the largest part of the screen, not the smallest part under a name
		field and two paragraphs.
	-->
	<form onsubmit={add} class="add">
		<Scroller viewportClass="add-scroll">
			<div class="add-body">
				<div class="field">
					<!-- What the list is, with the way to the other list beside it. The way across is
					     for a browser only: in the application the machine's own dialog is right there
					     and is the consent as well as the choice, so this dialog is never opened. -->
					<div class="list-head">
						<span class="label" id="picker-label">{listName}</span>
						{#if !grants.canAdd}
							{#if picker.scope === 'granted'}
								<Button tone="quiet" size="small" onclick={() => void picker.look('machine')}>
									Browse this device
								</Button>
							{:else}
								<Button tone="quiet" size="small" onclick={() => void picker.look('granted')}>
									Back to the folders Sift has
								</Button>
							{/if}
						{/if}
					</div>
					<FolderPicker
						{picker}
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

				<!-- Only where the folder cannot be written, and only as a fact: there is no switch to
				     turn on. Handing Sift the folder is the permission; a delete or a move
				     there asks at the moment and the filesystem answers. A folder Sift cannot write in
				     is still a perfectly good library to read. -->
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
	/* The wall's act: one column, centred under the sentence above it, the way `Empty` stacks its
	   own glyph and sentence. The name a step up from the sentence, the button a step below it. */
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

	/*
	 * One size, whatever is inside it.
	 *
	 * Three things in here change height as somebody clicks through folders: the list is shorter in
	 * a folder holding three things than in one holding thirty, the warning about the top of the
	 * media area comes and goes, and the line under the switch is one line or three depending on
	 * what the folder allows. Sized by its content, the sheet would grow and shrink under the pointer
	 * on every click. Held at one height instead, with anything that does not fit scrolling inside it.
	 *
	 * Global because `Modal` draws this element: a scoped rule carries a marker stamped onto what
	 * THIS file renders, and would match nothing here.
	 */
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

	/* The same four parts a Field draws, for a widget a Field cannot label. Kept in step with it
	   by using the same tokens rather than by copying its numbers. */
	.field {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.field .label {
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.field .help {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The list's name on the left and the way to the other list at the right edge, on one line. */
	.list-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
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
	   :global(.scroll-root)` in `FolderPicker.svelte`. Nothing here sets it: the picker renders
	   `ul.entries` inside `.picker`, and a rule here aimed at anything else would match nothing.
	   And Svelte cannot warn about a dead `:global()` selector. Said here so the next person
	   looking for where the picker's height is set does not look in this file for it. */

	/* The refusal that fires at the moment of picking, rather than prose standing above the
	   picker that nobody reads until it is too late. Its own rule, so the one sentence in this
	   dialog that says STOP is not in the same ink as the help text under the switch. */
	.warn {
		margin: 0;
		color: var(--sift-warn);
		font: var(--text-body-sm);
	}

	/* The row of answers at the foot of the add dialog: apart from the scrolling body above
	   them, and against the far end so the primary is where it is in every other sheet, not
	   two buttons hard against the left edge under an unseparated body. */
	.dialog-actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
		padding-block-start: var(--space-3);
		border-block-start: 1px solid var(--sift-line);
	}
</style>
