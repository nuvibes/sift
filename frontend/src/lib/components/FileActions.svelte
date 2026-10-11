<script lang="ts">
	/*
	 * Renaming a file and putting a move back, where ONE file is looked at: the server is asked
	 * whether this file can be organized, and the rows are absent unless it can. The server refuses
	 * either way.
	 */
	import type { Snippet } from 'svelte';
	import { api, ApiError } from '$lib/api/client';
	import { libraryChanges, WalkingPace, whenChanged } from '$lib/library/changes.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { Button, Field, Modal, TextInput } from '$lib/components/common';
	import CopiesChoice from '$lib/components/CopiesChoice.svelte';
	import { partsOf } from '$lib/organize/copies';
	import type { components } from '$lib/api/schema';

	interface Props {
		id: string;
		filename: string | null;
		/** The name is on the caller's screen, so it is told. */
		onrenamed?: (filename: string | null) => void;
		held?: boolean;
		/**
		 * `undo` is null until there is a move to put back, so the row is absent rather than
		 * greyed.
		 */
		children: Snippet<[{ canOrganize: boolean; rename: () => void; undo: (() => void) | null }]>;
	}

	type Options = components['schemas']['OrganizeOptions'];

	type Organized = components['schemas']['OrganizeDone'];

	let { id, filename, onrenamed, held = false, children }: Props = $props();

	let options = $state<Options | null>(null);
	let renaming = $state(false);
	let typed = $state('');
	let error = $state<string | undefined>(undefined);
	let busy = $state(false);
	let shownName = $state<string | null>(null);

	const name = $derived(shownName ?? filename);
	const allowed = $derived(options?.can_organize === true);

	/* A file stored in more than one place is renamed one copy at a time: the copies that can be. */
	const renamable = $derived(
		(options?.copies ?? []).length > 1
			? (options?.copies ?? []).filter((one) => one.reason === null)
			: []
	);
	let copy = $state<string | null>(null);
	const copyName = (locationId: string | null) => {
		const chosen = renamable.find((one) => one.location_id === locationId);
		return chosen ? partsOf(chosen.path).name : null;
	};

	/* Choosing another copy offers its own name, unless somebody has typed one already. */
	function chooseCopy(next: string | null) {
		if (typed === (copyName(copy) ?? name ?? '')) typed = copyName(next) ?? typed;
		copy = next;
	}

	$effect(() => {
		// A different asset's answer must not show while the new one is fetched.
		options = null;
		shownName = null;
		renaming = false;
		if (!held) void ask(id);
	});
	/* Asked again on the library bell, at a walking pace. */
	whenChanged(libraryChanges, new WalkingPace(() => ask(id)));

	async function ask(assetId: string) {
		try {
			options = await api.get<Options>(`/assets/${assetId}/organize`);
		} catch {
			// Not on offer; nothing was asked yet, so no error.
			options = null;
		}
	}

	function openRename() {
		copy = renamable[0]?.location_id ?? null;
		typed = copyName(copy) ?? name ?? '';
		error = undefined;
		renaming = true;
	}

	/* The server's sentence, as written: it says what to change. */
	function refusal(failure: unknown, fallback: string): string {
		return failure instanceof ApiError && failure.detail ? failure.detail : fallback;
	}

	async function rename() {
		busy = true;
		error = undefined;
		try {
			const done = await api.post<Organized>(`/assets/${id}/rename`, {
				body: copy === null ? { name: typed } : { name: typed, location_id: copy }
			});
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

<!-- A sheet, since the row that opens it is in a menu at the top. -->
<Modal
	bind:open={renaming}
	title="Rename this file"
	description="Only the name changes. The file stays in the folder it is in."
>
	<form
		class="naming"
		id="rename-file"
		onsubmit={(event) => {
			event.preventDefault();
			void rename();
		}}
	>
		<CopiesChoice
			copies={renamable}
			bind:value={() => copy, chooseCopy}
			label="Which copy to rename"
		/>
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

<style>
	/* The copy, when there is one to choose, above the name it takes. */
	.naming {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}
</style>
