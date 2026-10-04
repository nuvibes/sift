<script lang="ts">
	/*
	 * The host's token, to send to the other person, and the one sentence that goes beside it.
	 *
	 * The sentence is the SERVER's, drawn as it arrives: it is the one promise this screen makes
	 * about what the token gives away, and it is pinned word for word where the token is made, so
	 * the two cannot come to say different things. The token is text to copy (there is no picture
	 * of it) because it travels over whatever the two people already talk on.
	 */
	import { Button, Empty, Panel } from '$lib/components/common';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';

	interface Props {
		token: string;
		/** The sentence the server sends with the token, word for word. */
		sentence: string;
	}

	let { token, sentence }: Props = $props();

	async function copy(): Promise<void> {
		if (await copyText(token)) toasts.show('Token copied');
		else
			toasts.show("The token couldn't be copied. Select it and copy it yourself.", {
				tone: 'error'
			});
	}
</script>

<Panel label="Your swap token">
	<p class="lede">Send this token to the person you are swapping with.</p>
	<div class="box">
		<Panel tone="recessed" inset="sm">
			<p class="token">{token}</p>
		</Panel>
		<Button icon="content_copy" onclick={() => void copy()}>Copy</Button>
	</div>
	<p class="sentence">{sentence}</p>
	<Empty busy scope="block">Waiting for them to join</Empty>
</Panel>

<style>
	.lede,
	.sentence {
		margin: 0;
	}

	/* The token takes the room; Copy keeps its own width beside it. */
	.box {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: start;
		gap: var(--space-3);
	}

	/* The token wraps anywhere: it is one word of about a hundred and twenty letters, and a box that
	   refused to break it would push the Copy button off the side of a narrow window. */
	.token {
		margin: 0;
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		overflow-wrap: anywhere;
		user-select: all;
	}

	.sentence {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}
</style>
