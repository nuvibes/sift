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
		states: ['asking', 'recognized', 'recognized, a reference', 'small', 'announced', 'decorative']
	} satisfies DesignEntry;

	/** Which mark: `asking` (the one that is work) or `recognized` (Sift named it). A confirmed face
	 * has none. */
	export type FaceMarkKind = 'asking' | 'recognized';
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

	/** The words every faces screen uses for the state, or, for a face Sift also identifies others
	 * by, the caller's sentence saying so: one glyph either way. */
	interface Props extends Shared {
		kind: FaceMarkKind;
		label?: string;
	}

	let { kind, label, size = 'medium', decorative = false }: Props = $props();

	/* Sift's own naming wears the glyph History gives what Sift learned from a face. */
	const GLYPH: Record<FaceMarkKind, IconName> = {
		asking: 'help',
		recognized: facetValueIcon('enriched', 'faces') ?? 'familiar_face_and_zone'
	};

	/* The same words every faces screen uses for these states. */
	const WORDS: Record<FaceMarkKind, string> = {
		asking: 'Needs your input',
		recognized: 'Recognized by Sift'
	};

	const said = $derived(label ?? WORDS[kind]);
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
