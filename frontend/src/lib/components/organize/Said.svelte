<script lang="ts">
	/*
	 * One sentence the server wrote, with each thing it names drawn as a way to that thing.
	 *
	 * The server sends the words and, beside them, who and what they name, the shape a History
	 * line's names travel in (`HistoryLink`). The run that says each name is found by History's own
	 * `sentenceParts` (a name counts only where it stands whole) and addressed by its own `hrefOf`,
	 * both imported, so a name is found and linked one way on every screen. A link to a thing that
	 * has gone is drawn as plain words, as in History.
	 *
	 * With no links it is the sentence, character for character. The board's card and the Shoots
	 * page draw a card's question through this; the line under it is drawn plain, because only the
	 * title line links.
	 *
	 * The markup is on one line on purpose: the runs sit inside the sentence, and a line break
	 * between them would be a space in the words.
	 */
	import { hrefOf, sentenceParts, type HistoryLink } from '$lib/components/common/history';

	let { what, links = [] }: { what: string; links?: readonly HistoryLink[] } = $props();
</script>

{#each sentenceParts(what, links) as part, index (index)}{@const href =
		part.link && !part.link.gone && hrefOf(part.link)}{#if href}<a class="named" {href}
			>{part.text}</a
		>{:else}{part.text}{/if}{/each}

<style>
	/* A name in a sentence, drawn the way a history line draws one (`HistoryRow`'s `.named`): the
	   accent's text colour, underlined only under the pointer or the keyboard. */
	.named {
		color: var(--sift-accent-text);
		text-decoration: none;
		text-underline-offset: 2px;
		border-radius: var(--radius-sm);
		transition: color var(--dur-instant) var(--ease);
	}

	.named:hover,
	.named:focus-visible {
		text-decoration: underline;
	}
</style>
