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
	/* Choose a file wearing the app's button: the input is clipped, not `display: none` (which drops
	 * it from the keyboard), and cleared after every choice so the same file can be retried. */
	import type { Snippet } from 'svelte';
	import Button from './Button.svelte';
	import type { ButtonSize, ButtonTone } from './Button.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** One file chosen. For a picker that takes several, see `onchooseAll`. */
		onchoose?: (file: File) => void;
		/** Every file chosen, when `multiple` or `directory`. The add panel takes a handful in one go. */
		onchooseAll?: (files: File[]) => void;
		multiple?: boolean;
		/** Ask for a folder and hand back everything under it, through `onchooseAll`. */
		directory?: boolean;
		children: Snippet;
		/** Which kinds the dialog offers; a hint, the server decides. */
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
		// Cleared before the caller is told, so a failed pick can be retried.
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
