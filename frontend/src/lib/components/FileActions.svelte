<script lang="ts">
	/*
	 * Renaming a file, and putting a move back: the two verbs that only exist where ONE file is
	 * being looked at.
	 *
	 * It draws no control of its own. It asks the server the one question both depend on, owns the
	 * sheet that asks before it writes, and hands the finished rows to a snippet: the same shape
	 * `FileVerbs` has, and for the same reason. The screen decides where the rows go; it does not
	 * get to decide whether they exist, and it must not have to know which request is behind them.
	 *
	 * ## Why these two are not file verbs
	 *
	 * Hiding, moving, sharing and saving are offered by every surface showing files, so they are
	 * declared once and rendered from that declaration. These two hang off a question the server
	 * answers per FILE (whether this file can be organized at all) and a wall of forty tiles
	 * cannot ask it forty times to decide what to draw. That is exactly the case `RowMenu`'s `extra`
	 * exists for, and it is where these rows are drawn today.
	 *
	 * The whole component is absent unless that answer is yes, and that is deliberate rather than
	 * tidy. Sift indexes most libraries read-only and cannot change anything in them, so on most
	 * installs these would be permanently greyed out: an invitation to a dead end, repeated on
	 * every file. The server is asked first, and if the answer is no there is nothing to press.
	 *
	 * Hiding it is not the control. Both are refused on the server as well, for any account that may
	 * not do it and any folder that was not handed over read-write. What is here is the part that
	 * decides what a person is offered; what stops it happening is somewhere else.
	 */
	import type { Snippet } from 'svelte';
	import { api, ApiError } from '$lib/api/client';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { Button, Field, Modal, TextInput } from '$lib/components/common';
	import type { components } from '$lib/api/schema';

	interface Props {
		id: string;
		/** The name shown now, so the rename box opens with it rather than empty. */
		filename: string | null;
		/**
		 * The file now goes by a different name. Told to whoever drew this, because the name is on
		 * their screen, not this component's; an old name left sitting there reads as the rename
		 * having failed.
		 */
		onrenamed?: (filename: string | null) => void;
		/**
		 * Drawn with the two rows ready to place.
		 *
		 * `canOrganize` is the server's answer, handed over rather than acted on here: a caller that
		 * draws these among other rows needs to know whether it has anything to separate. `undo` is
		 * null until there is a move to put back, so the row is absent rather than permanently
		 * greyed, which says nothing.
		 */
		children: Snippet<[{ canOrganize: boolean; rename: () => void; undo: (() => void) | null }]>;
	}

	type Options = components['schemas']['OrganizeOptions'];

	type Organized = components['schemas']['OrganizeDone'];

	let { id, filename, onrenamed, children }: Props = $props();

	let options = $state<Options | null>(null);
	let renaming = $state(false);
	let typed = $state('');
	let error = $state<string | undefined>(undefined);
	let busy = $state(false);
	let shownName = $state<string | null>(null);

	const name = $derived(shownName ?? filename);
	const allowed = $derived(options?.can_organize === true);

	$effect(() => {
		// A different asset is a different answer, and the previous one must not be on screen while
		// the new one is fetched: these actions change files, and the wrong file is the failure.
		options = null;
		shownName = null;
		renaming = false;
		void ask(id);
	});
	/* Whether this file may be organized, and what it is called, move with the library (a folder
	   handed over, a file renamed in another window): asked again on its bell. */
	whenChanged(libraryChanges, () => void ask(id));

	async function ask(assetId: string) {
		try {
			options = await api.get<Options>(`/assets/${assetId}/organize`);
		} catch {
			// Anything at all means these actions are not on offer. There is no error to show: the
			// person did not ask for anything yet.
			options = null;
		}
	}

	function openRename() {
		typed = name ?? '';
		error = undefined;
		renaming = true;
	}

	/* The server's sentence, shown as written.
	 *
	 * These refusals are the product: "There is already something called 'holiday.mp4' in that
	 * folder" is what somebody needs in order to pick a different name. Replacing it with a generic
	 * failure leaves them with a box that will not close and no idea why.
	 */
	function refusal(failure: unknown, fallback: string): string {
		return failure instanceof ApiError && failure.detail ? failure.detail : fallback;
	}

	async function rename() {
		busy = true;
		error = undefined;
		try {
			const done = await api.post<Organized>(`/assets/${id}/rename`, { body: { name: typed } });
			shownName = done.filename;
			options = options && { ...options, undo_move_id: done.move_id };
			renaming = false;
			onrenamed?.(done.filename);
		} catch (failure) {
			error = refusal(failure, "That name couldn't be used.");
		} finally {
			busy = false;
		}
	}

	async function undo() {
		const moveId = options?.undo_move_id;
		if (!moveId) return;
		busy = true;
		try {
			const done = await api.post<Organized>(`/moves/${moveId}/undo`, {});
			shownName = done.filename;
			onrenamed?.(done.filename);
			await ask(id);
		} catch (failure) {
			toasts.show(refusal(failure, "That couldn't be undone"), { tone: 'error' });
		} finally {
			busy = false;
		}
	}
</script>

{@render children({
	canOrganize: allowed,
	rename: openRename,
	/* Only after a move, and only while the server still holds the record of one. Null rather than a
	   function that would do nothing, so the row can be left out entirely. */
	undo: allowed && options?.undo_move_id ? () => void undo() : null
})}

<!-- The box, as a sheet rather than a form on the page.
     The row that opens it is in a menu at the top of the screen, and a form appearing several
     hundred pixels below the thing that was pressed is a form nobody finds. -->
<Modal
	bind:open={renaming}
	title="Rename this file"
	description="Only the name changes. The file stays in the folder it is in."
>
	<form
		id="rename-file"
		onsubmit={(event) => {
			event.preventDefault();
			void rename();
		}}
	>
		<Field label="Name" {error}>
			{#snippet control({ id: fieldId, describedBy, invalid })}
				<!-- svelte-ignore a11y_autofocus: the sheet exists to be typed into -->
				<TextInput
					id={fieldId}
					bind:value={typed}
					{describedBy}
					{invalid}
					disabled={busy}
					spellcheck="false"
					autocomplete="off"
					autofocus
				/>
			{/snippet}
		</Field>
	</form>

	{#snippet footer()}
		<div class="buttons">
			<Button type="button" onclick={() => (renaming = false)} disabled={busy}>Cancel</Button>
			<Button
				type="submit"
				form="rename-file"
				tone="primary"
				icon="save"
				disabled={busy || !typed.trim()}
			>
				Save
			</Button>
		</div>
	{/snippet}
</Modal>
