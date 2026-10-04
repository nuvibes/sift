<script lang="ts">
	/*
	 * Words that copy themselves when pressed: the file name under the player, the Location, and
	 * every fact on a file's record.
	 *
	 * THE ONE COPY HELPER: a hover ground says it can be pressed, a tooltip says Copy, and the same
	 * tooltip says Copied once it has. Three ways to take a value out of one panel would be two too
	 * many, so this one way is here and all three draw it.
	 *
	 * The text itself is the control, because it is already the widest target on its line.
	 *
	 * `Pressable` and not `Button`: this control's size is the text, which can be long, cut with an
	 * ellipsis, or wrap, and must give its row the room it needs. `wash` is the register for
	 * something in a row (scaling would shove its neighbours), a ground stepping over
	 * `--dur-instant`.
	 *
	 * `staysOnPress`, because tooltips are dismissed on press everywhere else (a control that opens
	 * something over itself never fires `pointerleave`), and here the label is the answer.
	 *
	 * Through `copyText` and never `navigator.clipboard` directly: that object does not exist on a
	 * plain-http address, which is how a self-hosted Sift is normally reached.
	 *
	 * The word goes back on its own because the tooltip is the control's name; left reading
	 * "Copied", the next person to hover is told what happened rather than what pressing would do.
	 * No toast when it lands, because it happened under the pointer; one when it does not, because
	 * the tooltip saying "Copied" would be a lie about the only thing this control does.
	 */
	import type { Snippet } from 'svelte';
	import { onDestroy } from 'svelte';

	import { Pressable, Tooltip } from '$lib/components/common';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';

	interface Props {
		/** What goes on the clipboard. */
		text: string;
		/** What the failure says it could not copy: "That name", "That location". */
		what?: string;
		/** Handed to the press, for a caller that sets how the words sit in its row. */
		class?: string;
		/** A tooltip wrapper that may shrink, for words cut with an ellipsis. */
		shrinks?: boolean;
		/** What is drawn. The text itself when nothing is given. */
		children?: Snippet;
	}

	let { text, what = 'That', class: className = '', shrinks = false, children }: Props = $props();

	/** Long enough to be read, short enough that the label is a name again before it is next used. */
	const COPIED_MS = 1600;

	let copied = $state(false);
	let copiedFor: ReturnType<typeof setTimeout> | null = null;

	async function copy() {
		if (text === '') return;
		if (!(await copyText(text))) {
			toasts.show(`${what} couldn't be copied`, { tone: 'error' });
			return;
		}
		if (copiedFor) clearTimeout(copiedFor);
		copied = true;
		copiedFor = setTimeout(() => (copied = false), COPIED_MS);
	}

	onDestroy(() => {
		if (copiedFor) clearTimeout(copiedFor);
	});
</script>

<Tooltip label={copied ? 'Copied' : 'Copy'} staysOnPress {shrinks}>
	<Pressable
		class="copyable {className}"
		feedback="wash"
		radius="sm"
		pad="sm"
		onclick={() => void copy()}
	>
		{#if children}{@render children()}{:else}{text}{/if}
	</Pressable>
</Tooltip>

<style>
	/* The words keep their own face and colour; the press adds only the ground under a pointer.
	   Where the words start is the row's to say (a record pulls them back to its label's edge).
	   `:global` because the class is handed to `Pressable`, which puts it on its own button. */
	:global(.copyable) {
		max-inline-size: 100%;
		min-inline-size: 0;
		font: inherit;
		color: inherit;
		text-align: start;
	}
</style>
