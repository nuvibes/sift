<script lang="ts">
	/* NOT ON THE GALLERY: a singleton the layout renders, and it opens the same add sheet from every screen.
	   A second one would open a second copy of that sheet. */

	import { capture } from '$lib/capture/capture.svelte';
	import {
		Button,
		ChooseFile,
		Empty,
		Field,
		Popover,
		Select,
		TextInput,
		Tooltip
	} from '$lib/components/common';
	import SplitButton from '$lib/components/common/SplitButton.svelte';
	import { canReadClipboard } from '$lib/shell/clipboard';
	import { Destinations } from '$lib/library/destinations.svelte';
	import { noteFolderUse, recallInterfaceState } from '$lib/shell/interface-state.svelte';
	import { carriesALink } from '$lib/components/common/drag-assign.svelte';
	import { session } from '$lib/shell/session.svelte';
	import SwapModeButton from '$lib/swap/SwapModeButton.svelte';
	import { screenBar } from './screen-bar.svelte';

	// Opens on hover, focus or click (`Popover`); a panel in use is never closed by the pointer
	// leaving, so the folder menu and the file chooser survive. Adding is admin-only on the server.
	let open = $state(false);

	let url = $state('');
	/* The chooser's value: the default row, or a folder while it is being made the default. */
	let dest = $state('');

	/* The same list the Downloads screen's "Download folder" draws, so the two cannot differ. */
	const destinations = new Destinations();
	destinations.follow();

	/** Opening is when the folder list and its recency order are worth asking for. */
	function opened(isOpen: boolean) {
		if (!isOpen || !session.isAdmin) return;
		void destinations.load();
		void recallInterfaceState();
	}

	function addUrl(event: SubmitEvent) {
		event.preventDefault();
		start(url);
	}

	// A pasted link starts fetching; never on typing, which passes through shorter links.
	function pasted(event: ClipboardEvent) {
		const dropped = event.clipboardData?.getData('text') ?? '';
		if (!looksLikeALink(dropped)) return;
		// Taken from the input, or the started link would sit there to be started again.
		event.preventDefault();
		start(dropped);
	}

	/** A whole http or https address as `URL` reads one; `file:` and `javascript:` are refused. */
	function looksLikeALink(text: string): boolean {
		try {
			const parsed = new URL(text.trim());
			return parsed.protocol === 'http:' || parsed.protocol === 'https:';
		} catch {
			return false;
		}
	}

	/* A folder picked here becomes the default, so every way in from this panel sends none. */
	async function chooseFolder(folderId: string) {
		if (!folderId) return;
		noteFolderUse(folderId);
		await destinations.makeDefault(folderId);
		dest = '';
	}

	function start(link: string) {
		const trimmed = link.trim();
		if (!trimmed) return;
		void capture.submitUrl(trimmed, null);
		url = '';
		open = false;
	}

	function addFiles(files: File[]) {
		for (const file of files) void capture.submitFile(file, null);
		open = false;
	}

	// Only where the clipboard can be read (the desktop client, or HTTPS); Ctrl-V works anyway.
	const canPaste = canReadClipboard();

	/* Short of room, the paste half folds into Add and its press moves into the panel. */
	const pasteHere = $derived(canPaste && session.isAdmin && screenBar.pasteOnBar);

	async function pasteIn() {
		await capture.pasteFromClipboard(null);
		open = false;
	}
	// A link from outside held over Add: the same offer the rail's rows make.
	let takingALink = $state(false);

	function overWithALink(event: DragEvent): void {
		if (!carriesALink(event)) return;
		// Without this the browser refuses the drop, silently, and the gesture just fails.
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
		takingALink = true;
	}

	function dropLink(event: DragEvent): void {
		takingALink = false;
		if (!carriesALink(event) || !event.dataTransfer) return;
		event.preventDefault();
		// Not stopped: the window clears its counter on this `drop`, and skips a named zone.
		void capture.handleDrop(event.dataTransfer);
	}
</script>

<!-- A group: the button and its panel, the pointer moving between them keeps it open. -->
<!-- The trigger is the main half, so pointing at the paste half opens nothing. -->
<!-- Takes a dropped link via `capture`; `data-drop-zone` stands the window's offer down. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="add"
	class:taking={takingALink}
	role="group"
	data-drop-zone="link"
	ondragenter={overWithALink}
	ondragover={overWithALink}
	ondragleave={() => (takingALink = false)}
	ondrop={dropLink}
>
	<!-- Split where the clipboard can be read, so pasting is one act; elsewhere a plain button. -->
	<Popover
		bind:open
		onOpenChange={opened}
		hover
		side="bottom"
		align="end"
		sideOffset={8}
		label="Add media"
		fromTheBar
	>
		{#snippet trigger({ props })}
			{#if pasteHere}
				<SplitButton
					{...props}
					tone="primary"
					icon="add"
					trailingIcon="assignment_add"
					trailingLabel="Paste from clipboard"
					ontrailing={pasteIn}>Add</SplitButton
				>
			{:else}
				<Button {...props} tone="primary" icon="add">Add</Button>
			{/if}
		{/snippet}

		{#if session.isAdmin}
			<form onsubmit={addUrl}>
				<Field label="Paste a link">
					{#snippet control({ id, describedBy })}
						<div class="url-row">
							<TextInput
								{id}
								{describedBy}
								type="url"
								placeholder="Paste a link"
								onpaste={pasted}
								bind:value={url}
							/>
							{#if canPaste && !pasteHere}
								<Tooltip label="Paste from clipboard">
									<Button
										tone="secondary"
										size="small"
										icon="assignment_add"
										aria-label="Paste from clipboard"
										onclick={pasteIn}
									/>
								</Tooltip>
							{/if}
							<Button type="submit" tone="primary" size="small" icon="add">Add</Button>
						</div>
					{/snippet}
				</Field>
			</form>

			{#if destinations.folders.length > 0}
				<Field label="Download folder">
					{#snippet control({ id, describedBy })}
						<Select
							{id}
							{describedBy}
							bind:value={dest}
							options={destinations.options}
							onValueChange={(chosen: string) => void chooseFolder(chosen)}
						/>
					{/snippet}
				</Field>
			{:else if destinations.foldersRead === 'read'}
				<p class="hint">
					Sift has no folder to save a download in yet. Add a folder to your library first.
				</p>
			{/if}

			<!-- Swap mode ends the last row; pressing it closes the panel for its drawer. -->
			<div class="last-row">
				<ChooseFile multiple icon="upload" label="Choose files" onchooseAll={addFiles}>
					Choose files
				</ChooseFile>
				<SwapModeButton onpressed={() => (open = false)} />
			</div>

			<!-- No paste row here while the half on Add does it; Ctrl-V works everywhere. -->
		{:else}
			<Empty scope="block">Adding media is available to admins.</Empty>
		{/if}
	</Popover>
</div>

<style>
	.add {
		position: relative;
	}

	/* The accent ring the rail's rows and entity cards draw for the same gesture. */
	.add.taking {
		border-radius: var(--radius-md);
		box-shadow: 0 0 0 2px var(--sift-accent);
	}

	.hint {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.url-row {
		display: flex;
		gap: var(--space-2);
	}

	.last-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
	}

	/* `min-inline-size: 0`, or a long link pushes the button off the panel. */
	.url-row :global(.text-input) {
		flex: 1;
		min-inline-size: 0;
	}

	/* On a phone Add is a square, its word kept for screen readers; the paste half goes. */
	@media (max-width: 767px) {
		.add :global(button.btn) {
			inline-size: var(--control-height);
			padding: 0;
			justify-content: center;
			border-radius: var(--radius-md);
		}

		.add :global(button.btn .label) {
			position: absolute;
			inline-size: 1px;
			block-size: 1px;
			overflow: hidden;
			clip-path: inset(50%);
			white-space: nowrap;
		}

		.add :global(.half.trail) {
			display: none;
		}
	}
</style>
