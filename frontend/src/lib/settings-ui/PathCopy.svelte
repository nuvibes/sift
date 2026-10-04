<script lang="ts">
	/*
	 * The copy button to the left of a settings name: puts the name's settings path on the
	 * clipboard (`Settings > Privacy > Auto-lock > Lock Hidden when you switch away`).
	 *
	 * Shown while the name's holder is hovered. The holder is the element this is drawn in, marked
	 * `path-host` by the caller; the button stands in the gutter to the left of it, which the
	 * settings frame keeps clear for it. Outside Settings there is no place to say, so nothing is
	 * drawn.
	 *
	 * Out of the tab order: every row on a pane has one, and a second stop per row would double the
	 * walk through a pane for a shortcut the search box already offers by typing the path.
	 */
	/* By file, not through the common index: rows and headings there draw this component. */
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { headingPath, pagePath, placeHere, rowPath } from './settings-path';

	interface Props {
		/** The name this copies the path of. Absent for a page title, whose path is its place. */
		name?: string | (() => string);
		/** A group heading names its own group, so its path stops at the heading. */
		heading?: boolean;
	}

	let { name, heading = false }: Props = $props();

	const LABEL = 'Copy settings path';

	const place = placeHere();

	function path(): string {
		if (!place) return '';
		const said = typeof name === 'function' ? name() : name;
		if (said === undefined) return pagePath(place);
		return heading ? headingPath(place, said) : rowPath(place, said);
	}

	async function copy(): Promise<void> {
		if (await copyText(path())) toasts.show('Settings path copied');
		else toasts.show("Couldn't copy the settings path", { tone: 'error' });
	}
</script>

{#if place}
	<span class="path-copy" class:heading>
		<Tooltip label={LABEL}>
			<Button
				icon="content_copy"
				tone="ghost"
				size="small"
				iconSize={16}
				aria-label={LABEL}
				tabindex={-1}
				onclick={copy}
			/>
		</Tooltip>
	</span>
{/if}

<style>
	/*
	 * In the gutter left of the name, centred on the name's first line. The padding on its right
	 * bridges the gap to the name, so the pointer moving across to it never leaves the holder.
	 *
	 * The line is the ROW NAME's line, said here in the name's own font and line height: `1lh` in
	 * the holder's inherited font would be the help text's taller line, standing every press 2px
	 * below the name it copies, on every row of every pane.
	 */
	.path-copy {
		position: absolute;
		inset-block-start: 0;
		inset-inline-end: 100%;
		display: flex;
		align-items: center;
		font: var(--text-body);
		line-height: 1.3;
		block-size: 1lh;
		padding-inline-end: var(--space-1);
		/* Faded, not hidden: the press appears one step under the pointer, as every hover does. */
		opacity: 0;
		pointer-events: none;
		transition: opacity var(--dur-instant) var(--ease);
	}

	/* A group heading stands in a line of its own, centred on it beside whatever presses the heading
	   carries: the press spans that line and centres with the words, rather than taking the top of
	   a line a heading's own press had made taller (Performance's device heading, 5px high). */
	.path-copy.heading {
		inset-block-end: 0;
		block-size: auto;
	}

	/* The button is smaller than a row's press: it sits in a gutter one glyph wide. */
	.path-copy :global(.btn) {
		inline-size: var(--space-6);
		block-size: var(--space-6);
		min-block-size: 0;
	}

	:global(.path-host:hover) > .path-copy {
		opacity: 1;
		pointer-events: auto;
	}

	/* Nothing to hover on a touch screen, and no gutter at a phone's width. */
	@media (hover: none) {
		.path-copy {
			display: none;
		}
	}
</style>
