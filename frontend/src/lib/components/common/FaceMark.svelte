<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts" module>
	/* WHY NOT BITS-UI: the library has no mark, and there is nothing for it to have. This is a glyph
	   with a tooltip: no focus, no keyboard, no state of its own. */
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'FaceMark',
		category: 'primitive',
		role: "a small accent glyph saying where a face's name came from, beside the name or on the face's card",
		basis: 'composes:Tooltip',
		states: ['asking', 'recognized', 'reference', 'learned', 'small', 'announced', 'decorative']
	} satisfies DesignEntry;

	/**
	 * Which mark.
	 *
	 * `asking`: Sift suggested this name and is asking (Needs your input), the one mark that is work.
	 * `recognized`: Sift named this face without asking (Recognized by Sift).
	 * `reference`: Sift matches other faces against this one; its sentence says whose face it helps
	 * find, so the caller hands the words (`label`, required for this kind).
	 * `learned`: a reference Sift took from a face it named itself, in History's glyph for faces.
	 *
	 * A face somebody confirmed has no mark: "there is nothing to do here" is the absence of one.
	 */
	export type FaceMarkKind = 'asking' | 'recognized' | 'reference' | 'learned';
</script>

<script lang="ts">
	/*
	 * THE FACE'S MARKS, drawn once.
	 *
	 * A mark that means one thing drawn in two files is two marks the moment one of them changes, so
	 * the glyph, the words and the tooltip live here, and a screen says only which mark and how big.
	 *
	 * The glyph alone in the accent's text role (`--sift-accent-text`), nothing filled behind it:
	 * the fill's accent is too faint on a card bare. Green would read as approval on a mark that
	 * reports a fact. The asking mark is as strong as the others: it is the state that wants somebody.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { facetValueIcon } from '$lib/components/shell/facet-labels';
	import type { IconName } from '$lib/design/icons';
	import Tooltip from './Tooltip.svelte';

	interface Shared {
		/**
		 * `small` beside a name in a line of small print (the strip under a file); `medium` on a
		 * face's card, the size of the card's selection tick, so the things on a card are one family.
		 */
		size?: 'small' | 'medium';
		/**
		 * Announced by its words (the default), or left out of what is read because the thing it
		 * sits on already says it in its own accessible name (a face card names its state).
		 */
		decorative?: boolean;
	}

	/** A state mark: its words are the ones every faces screen uses for that state. */
	interface State extends Shared {
		kind: 'asking' | 'recognized';
		label?: undefined;
	}

	/**
	 * The reference mark: its words are the caller's, because the sentence somebody needs is whose
	 * face it helps Sift find, and only the screen knows whose. The tooltip, and the mark's name
	 * when it is announced.
	 */
	interface Reference extends Shared {
		kind: 'reference' | 'learned';
		label: string;
	}

	type Props = State | Reference;

	let { kind, label, size = 'medium', decorative = false }: Props = $props();

	const GLYPH: Record<FaceMarkKind, IconName> = {
		asking: 'help',
		recognized: 'auto_fix_high',
		reference: 'face',
		learned: facetValueIcon('enriched', 'faces') ?? 'familiar_face_and_zone'
	};

	/* The same words every faces screen uses for these states. */
	const WORDS: Record<State['kind'], string> = {
		asking: 'Needs your input',
		recognized: 'Recognized by Sift'
	};

	const said = $derived(kind === 'reference' || kind === 'learned' ? (label ?? '') : WORDS[kind]);
</script>

<Tooltip label={said} placement="top">
	<span class="face-mark" aria-hidden={decorative ? 'true' : undefined}>
		<Icon
			name={GLYPH[kind]}
			size={size === 'small' ? 14 : 16}
			label={decorative ? undefined : said}
		/>
	</span>
</Tooltip>

<style>
	/*
	 * The glyph's size and a half-step round it, so it adds no literal size and keeps one
	 * footprint: 14 and 2 each side beside a name, 16 and 2 on a card, which is the selection
	 * tick's family. It takes the pointer back from whatever column it sits in, because its tooltip
	 * is how its words are read.
	 */
	.face-mark {
		display: inline-grid;
		place-items: center;
		padding: calc(var(--space-1) / 2);
		color: var(--sift-accent-text);
		pointer-events: auto;
	}
</style>
