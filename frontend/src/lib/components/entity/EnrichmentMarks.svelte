<script lang="ts">
	/*
	 * WHY NOT BITS-UI: there is nothing here to reach for. These are marks, not controls: nothing
	 * is pressable or focusable. Who wrote to a subject without a person doing it, named per box,
	 * in the `enriched:` filter's own glyphs and words.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import {
		afterWords,
		boxIcon,
		boxRowLabel,
		madeIcon,
		madeLabel
	} from '$lib/components/shell/facet-labels';
	import type { components } from '$lib/api/schema';

	/** One author as the server sends it; `act` is the producing act on the Created by line. */
	type Source = components['schemas']['EnrichedBy'] & { act?: string | null };

	/** Which sentence the marks are in; only the word differs. */
	type Said = 'enriched' | 'created';

	interface Props {
		/** What wrote to this subject, newest first; one per box, so never keyed on `via`. */
		sources: readonly Source[];
		/** What the marks are of; `created` only on the Created by line. */
		said?: Said;
		/** Take a control's box, to space evenly beside Heart and RatingChip. */
		padded?: boolean;
		/** On the page's own ground, where the accent's fill clears 3:1 in every theme. */
		onPage?: boolean;
	}

	let { sources, said = 'enriched', padded = false, onPage = false }: Props = $props();

	/* The Enriched by column's own row for this author, word for word. */
	function row(one: Source): string {
		return one.name === null || one.name === undefined
			? madeLabel(one.via, one.act)
			: boxRowLabel(one.name);
	}

	/* Written out, not suffixed, so a third reading is a deliberate act. */
	const SAID: Record<Said, string> = {
		enriched: 'Enriched by',
		created: 'Created by'
	};

	/* Every tooltip is a whole sentence: nothing announces the group to a pointer. */
	function words(one: Source): string {
		return `${SAID[said]} ${afterWords(row(one))}`;
	}
</script>

<!-- A group, not a list: each mark already says what it is. -->
{#if sources.length > 0}
	<span class="marks" class:padded class:page={onPage} role="group" aria-label={SAID[said]}>
		{#each sources as one, at (`${at}:${one.via}:${one.name ?? ''}`)}
			{@const glyph = boxIcon(one.box) ?? madeIcon(one.via, one.act)}
			{#if glyph}
				<Tooltip label={words(one)} placement="bottom">
					<span class="mark" role="img" aria-label={words(one)}>
						<Icon name={glyph} size={padded ? 20 : 18} />
					</span>
				</Tooltip>
			{/if}
		{/each}
	</span>
{/if}

<style>
	.marks {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
	}

	/* Read-only: no ground, no hover. Text tone, since the fill misses 3:1 on a raised ground. */
	.mark {
		display: inline-flex;
		align-items: center;
		color: var(--sift-accent-text);
	}

	.page .mark {
		color: var(--sift-accent);
	}

	/* A control's box, to the token: `Heart`'s and `RatingChip`'s padding. See `padded`. */
	.padded .mark {
		padding: var(--space-1) var(--space-2);
	}
</style>
