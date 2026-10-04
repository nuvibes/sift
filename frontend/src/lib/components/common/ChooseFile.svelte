<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ChooseFile',
		category: 'control',
		role: 'the button in front of the site file and folder picker',
		basis: 'site:<input type=file>',
		states: ['default']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no such primitive. Choosing a file from the disk is `<input
	   type=file>` and nothing else: what this adds is the app's own button in front of it. */
	/*
	 * Choose a file from the disk, wearing the app's button.
	 *
	 * The site's file input cannot be styled into anything, so every screen that wants one has to
	 * hide it and dress a `<label>` up as a button, and two screens doing that by hand will not
	 * match each other or the app's `Button`. One component, used by every screen that asks for a
	 * file (Backup, the Add menu, a cover upload).
	 *
	 * The input is HIDDEN FROM SIGHT AND NOT FROM THE PAGE. `display: none` takes it out of the
	 * accessibility tree and off the keyboard path, and the control in front of it would then open
	 * nothing at all. Clipped instead.
	 *
	 * **The value is cleared after every choice, and that is not tidiness.** A file input does not
	 * fire `change` when the same file is chosen twice, so a pick that failed (refused bytes, a
	 * request that fell over) could not be retried without choosing a different file. Cleared, it
	 * can.
	 */
	import type { Snippet } from 'svelte';
	import Button from './Button.svelte';
	import type { ButtonSize, ButtonTone } from './Button.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** What was chosen. Never called with nothing. */
		/** One file chosen. For a picker that takes several, see `onchooseAll`. */
		onchoose?: (file: File) => void;
		/** Every file chosen, when `multiple` or `directory`. The add panel takes a handful at once. */
		onchooseAll?: (files: File[]) => void;
		multiple?: boolean;
		/**
		 * Ask for a folder, and hand back everything under it, subfolders included.
		 *
		 * `webkitdirectory` makes the system dialog offer a directory rather than a file, and
		 * implies `multiple`, because a folder is never one thing. Read `onchooseAll`; `onchoose`
		 * is still called with the first file, which is rarely what a folder picker wants. One
		 * component for files and folders, since the clipped input, the cleared value and the
		 * button in front of them are the same.
		 */
		directory?: boolean;
		/** What the button says. */
		children: Snippet;
		/** Which kinds to offer in the system dialog. A hint to the dialog and never a check:
		 *  the server decides what it will accept, because a client-side filter is advice. */
		accept?: string;
		tone?: ButtonTone;
		size?: ButtonSize;
		icon?: IconName;
		disabled?: boolean;
		busy?: boolean;
		/** The accessible name of the input itself, which the button's words do not reach. */
		label?: string;
	}

	let {
		onchoose,
		onchooseAll,
		multiple = false,
		directory = false,
		children,
		accept,
		tone = 'secondary',
		size,
		icon,
		disabled = false,
		busy = false,
		label = 'Choose a file'
	}: Props = $props();

	let input = $state<HTMLInputElement | null>(null);

	function chosen(event: Event) {
		const element = event.currentTarget as HTMLInputElement;
		const files = [...(element.files ?? [])];
		// Cleared before the caller is told, so a caller that opens a dialog and fails inside it
		// still leaves an input that can offer the same file again.
		element.value = '';
		if (files.length === 0) return;
		onchooseAll?.(files);
		if (files[0]) onchoose?.(files[0]);
	}
</script>

<span class="choose">
	<input
		bind:this={input}
		type="file"
		{accept}
		multiple={multiple || directory}
		{...directory ? { webkitdirectory: true } : {}}
		aria-label={label}
		onchange={chosen}
		{disabled}
	/>
	<Button type="button" {tone} {size} {icon} {disabled} {busy} onclick={() => input?.click()}>
		{@render children()}
	</Button>
</span>

<style>
	.choose {
		display: inline-flex;
		position: relative;
	}

	/* Hidden from sight, not from the page. See the note at the top. */
	input[type='file'] {
		position: absolute;
		inline-size: 1px;
		block-size: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}
</style>
