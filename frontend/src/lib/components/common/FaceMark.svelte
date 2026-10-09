<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts" module>
	/* WHY NOT BITS-UI: the library has no mark, and there is nothing for it to have. This is a glyph
	with a tooltip, with no focus or state. */
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'FaceMark',
		category: 'primitive',
		role: "a small accent glyph saying where a face's name came from, beside the name or on the face's card",
		basis: 'composes:Tooltip',
		states: ['asking', 'recognized', 'reference', 'learned', 'small', 'announced', 'decorative']
	} satisfies DesignEntry;

	/** Which mark: `asking` (the one that is work), `recognized`, `reference` (the caller's words)
	 * or `learned`. A confirmed face has none. */
	export type FaceMarkKind = 'asking' | 'recognized' | 'reference' | 'learned';
</script>

<script lang="ts">
	/* A face's marks, drawn once: the glyph alone in the accent's text role. */
	import Icon from '$lib/components/Icon.svelte';
	import { facetValueIcon } from '$lib/components/shell/facet-labels';
	import type { IconName } from '$lib/design/icons';
	import Tooltip from './Tooltip.svelte';

	interface Shared {
		/** `small` beside a name, `medium` on a card at the selection tick's size. */
		size?: 'small' | 'medium';
		/** Left out of what is read where the thing it sits on already says it. */
		decorative?: boolean;
	}

	/** A state mark: its words are the ones every faces screen uses for that state. */
	interface State extends Shared {
		kind: 'asking' | 'recognized';
		label?: undefined;
	}

	/** The reference mark, in the caller's words: whose face it helps find. */
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
	/* The glyph and a half-step round it; it takes the pointer back for its tooltip. */
	.face-mark {
		display: inline-grid;
		place-items: center;
		padding: calc(var(--space-1) / 2);
		color: var(--sift-accent-text);
		pointer-events: auto;
	}
</style>
