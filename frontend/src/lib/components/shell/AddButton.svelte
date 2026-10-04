<script lang="ts">
	/* NOT ON THE GALLERY: a singleton the layout renders, and it opens the same add sheet from every screen.
	   A second one would be a second button opening a second copy of that sheet over the gallery. */

	import { capture } from '$lib/capture/capture.svelte';
	import {
		Button,
		ChooseFile,
		Empty,
		Field,
		Popover,
		Select,
		TextInput
	} from '$lib/components/common';
	import SplitButton from '$lib/components/common/SplitButton.svelte';
	import { canReadClipboard } from '$lib/shell/clipboard';
	import { Destinations } from '$lib/library/destinations.svelte';
	import { noteFolderUse, recallInterfaceState } from '$lib/shell/interface-state.svelte';
	import { carriesALink } from '$lib/components/common/drag-assign.svelte';
	import { session } from '$lib/shell/session.svelte';
	import SwapModeButton from '$lib/swap/SwapModeButton.svelte';

	/*
	 * Opens on hover rather than on click, and the opening is the library's.
	 *
	 * It is the one control in the top bar worth reaching for without aiming, and a panel that also
	 * opens on focus and on click costs a keyboard user nothing. `Popover` does all three:
	 * `openOnHover`, with no delay in and a short one out.
	 *
	 * Two things it opens are not inside it: the folder chooser is a portalled menu, and the file
	 * chooser is a window of the operating system. The library marks the panel as in use on a
	 * pointer down or a focus inside it, and a panel in use is never closed by a pointer leaving,
	 * so moving onto a folder does not close it and opening the file chooser does not destroy the
	 * `<input type=file>` whose dialog is on screen. The pointer's travel from the button to the
	 * panel is the library's own grace area.
	 *
	 * What it does (take a link or a file and add it) is admin-only on the server. This shows the
	 * controls to an admin and a plain note to anyone else; the note is a rendering choice, and the
	 * server keeps the permission.
	 */
	let open = $state(false);

	let url = $state('');
	// '' is the default download folder; any other value is a folder the file lands in instead.
	let dest = $state('');

	/* The folders a download can go to, as the chooser lists them: the SAME list the Downloads
	   screen's "Download folder" draws, from one module, so the two cannot come to name the default or order
	   the folders differently. See `$lib/library/destinations.svelte`. */
	const destinations = new Destinations();
	destinations.follow();

	/** The panel opened or closed. Opening is when the folder list is worth asking for, and the
	 *  record of which folders were used last, which decides the order it is drawn in. */
	function opened(isOpen: boolean) {
		if (!isOpen || !session.isAdmin) return;
		void destinations.load();
		void recallInterfaceState();
	}

	function addUrl(event: SubmitEvent) {
		event.preventDefault();
		start(url);
	}

	/*
	 * A link pasted into the box starts fetching, without the button: pasting a link is the
	 * request, and the Add button would be one more thing to aim at for no decision.
	 *
	 * Only on paste, never on typing: a link typed a character at a time passes through many states
	 * that look like shorter links, and firing on any would fetch something nobody asked for. The
	 * button stays for anyone who types one, or edits after pasting.
	 */
	function pasted(event: ClipboardEvent) {
		const dropped = event.clipboardData?.getData('text') ?? '';
		if (!looksLikeALink(dropped)) return;
		// Taken over from the input entirely: letting it also land in the box would leave the text
		// of a download that has already started sitting there to be started again.
		event.preventDefault();
		start(dropped);
	}

	/** Whether this is a whole web address rather than a fragment somebody happens to have copied.
	 *
	 * Deliberately narrow. `URL` decides it, so what counts as a link is the same thing the browser
	 * means by one, and only http and https: a `file:` or a `javascript:` is a valid URL and not
	 * something to hand to a downloader.
	 */
	function looksLikeALink(text: string): boolean {
		try {
			const parsed = new URL(text.trim());
			return parsed.protocol === 'http:' || parsed.protocol === 'https:';
		} catch {
			return false;
		}
	}

	/* Every way out of this panel that NAMES a folder records it, and the three of them do it
	   through one line. What is not recorded is a download that named none: a drop onto the window
	   goes wherever the default says, and remembering the default as a recent choice would put a
	   row nobody picked at the top of this list. */
	function landedIn(folderId: string) {
		noteFolderUse(folderId);
	}

	function start(link: string) {
		const trimmed = link.trim();
		if (!trimmed) return;
		void capture.submitUrl(trimmed, dest || null);
		landedIn(dest);
		url = '';
		open = false;
	}

	function addFiles(files: File[]) {
		for (const file of files) void capture.submitFile(file, dest || null);
		landedIn(dest);
		open = false;
	}

	/* The button is drawn only where reading the clipboard is actually possible: in the desktop
	 * client, or over HTTPS. Over plain http in a browser it is absent rather than present and
	 * broken, because Ctrl-V still works there and a button that fails teaches people the feature
	 * does not. Read once at setup: whether this shell can do it does not change while it is open. */
	const canPaste = canReadClipboard();

	async function pasteIn() {
		await capture.pasteFromClipboard(dest || null);
		landedIn(dest);
		open = false;
	}
	/* Whether a link from outside is being held over Add. See the markup: this is the same offer the
	   rail's own rows make, drawn on the one control that is about bringing things in. */
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
		// Not stopped: the window's own counter is cleared by the `drop` it sees bubble past, and
		// it refuses to import this itself because the button is a named zone. Stopping it here
		// would leave the window-wide offer up with no drag in progress.
		void capture.handleDrop(event.dataTransfer);
	}
</script>

<!-- A group rather than a bare div: it is a button and the panel it owns, and the pointer moving
	between the two is what keeps it open. -->
<!--
	The trigger is the MAIN HALF, never this wrapper.

	On the wrapper, the hover would be on the clipboard half too: pointing at the button that pastes
	would drop the whole add panel open over the screen, for an action that opens nothing. A pointer
	entering a wrapper cannot say which child it arrived on, so what opens the panel is bound
	to the part that should answer, and `SplitButton` hands everything it is given to the main half
	for exactly this reason.

	The gap between the button and the panel is the library's grace area; see the note in the script.
-->
<!--
	Add takes a dropped link as well as a pressed one.

	It is the control whose whole job is "bring something into Sift", so a link held over it should
	land, and it is where somebody aims when the window's own offer is not what they want. The
	drop is `capture`'s, the same call the whole window makes, so this is one more way to reach the
	one path rather than a second path beside it. `data-drop-zone` names what it takes, so the
	window-wide offer stands down over this button and leaves the button's own ring showing.
-->
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
	<!--
			The shared button, so the one primary in the top bar is dressed by the same rule as every
			other primary rather than by a copy of it that drifted. It is the library's trigger as
			well: `child` hands this file the props that open the panel, and they go onto the button
			rather than onto a second one drawn to look like it.

			It is SPLIT where the clipboard can be read: pasting a link is far and away the commonest
			thing this button is opened for, and through the panel it is three actions deep: hover, wait
			for the panel, aim at a row. The trailing half is that one act, on the control somebody is
			already pointing at. The panel keeps its own "Paste a link" box: this is a shortcut past it,
			not a replacement, and somebody who has opened the panel to choose a folder first still needs
			it there.

			Where the clipboard CANNOT be read the plain button comes back rather than a split one with a
			dead half. A browser only lets a page read the clipboard by itself over a secure connection,
			so a self-hosted Sift reached across a home network has no way to do this, and a button
			that fails teaches people the feature does not exist. Ctrl-V still works; see `pasted`.
		-->
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
			{#if canPaste && session.isAdmin}
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
							<Button type="submit" tone="primary" size="small" icon="add">Add</Button>
						</div>
					{/snippet}
				</Field>
			</form>

			{#if destinations.folders.length > 0}
				<Field label="Download folder">
					{#snippet control({ id, describedBy })}
						<Select {id} {describedBy} bind:value={dest} options={destinations.options} />
					{/snippet}
				</Field>
			{/if}

			<!-- The panel's last row: its own choosing on the left, and swap mode at the bottom right,
			     on the panel's one right edge where every button in a row sits. Pressing it closes the
			     panel so the swap drawer it opens is what is seen. -->
			<div class="last-row">
				<ChooseFile multiple icon="upload" label="Choose files" onchooseAll={addFiles}>
					Choose files
				</ChooseFile>
				<SwapModeButton onpressed={() => (open = false)} />
			</div>

			<!--
								There is no "Paste from clipboard" row here, and no note explaining its absence.

								The Add button has a half of its own that does it: a row in this panel doing exactly
								what the button above it does would be a second door to one act, and this panel is
								the list of things that button cannot do. A note would be worse: a paragraph about
								why something is missing, shown on the devices where nothing is missing, which reads
								as the application apologising for the browser's rules.

								Ctrl-V still works everywhere, including where the button is absent, because a paste
								EVENT carries its own data and needs no permission at all. See `pasted`.
							-->
		{:else}
			<Empty scope="block">Adding media is available to admins.</Empty>
		{/if}
	</Popover>
</div>

<style>
	.add {
		position: relative;
	}

	/* A link is being held over Add. The same accent ring the rail's rows and the entity cards draw
	   for the same gesture, so "this will take it" looks the same wherever it is offered. */
	.add.taking {
		border-radius: var(--radius-md);
		box-shadow: 0 0 0 2px var(--sift-accent);
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

	/* The box takes what the button beside it does not. `min-inline-size: 0` because a flex item
	   refuses to shrink below the width of what is typed in it without it, and a long link would
	   push the button off the end of the panel. */
	.url-row :global(.text-input) {
		flex: 1;
		min-inline-size: 0;
	}

	/*
	 * On a phone the top bar is the search box and a row of squares, so Add is a square too: the
	 * glyph shows, and the word stays the button's name for a screen reader. The paste half goes;
	 * the panel keeps its own "Paste a link" box, so nothing is lost with it.
	 *
	 * `button.btn` so the rule outranks the shared button's own padding rather than tying with it.
	 */
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
