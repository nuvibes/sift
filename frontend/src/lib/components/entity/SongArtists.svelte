<script lang="ts">
	/*
	 * Who a song credits, in order, each a press opening the Music wall filtered by `artists=` (the
	 * id; the name goes to `rememberFacetNames` for the chip). A card keeps its empty line.
	 * NOT ON THE GALLERY: it draws only inside a song's card (`EntityCard`'s `byline`) and a song's
	 * header.
	 */
	import { rememberFacetNames } from '$lib/components/shell/facet-labels';
	import { ContextMenu, VerbMenuItems } from '$lib/components/common';
	import { menuVerbs } from '$lib/components/common/verbs';
	import { ARTIST_FIELD as FIELD, artistVerbs } from '$lib/entity/artists.svelte';
	import { session } from '$lib/shell/session.svelte';
	import type { components } from '$lib/api/schema';

	interface Props {
		artists: readonly components['schemas']['ArtistCredit'][];
		/** Under a card, quieter and on one line; on a song's page, the body's size. */
		size?: 'card' | 'page';
	}

	let { artists, size = 'card' }: Props = $props();

	function hrefOf(id: string): string {
		return `/songs?${new URLSearchParams({ [FIELD]: id })}`;
	}

	function remember(one: components['schemas']['ArtistCredit']): void {
		rememberFacetNames(FIELD, [{ value: one.id, label: one.name, count: 0 }]);
	}
</script>

{#snippet press(one: components['schemas']['ArtistCredit'])}
	<a href={hrefOf(one.id)} aria-label="Music by {one.name}" onclick={() => remember(one)}
		>{one.name}</a
	>
{/snippet}

<!-- An artist's right-click is its own and stops there, not opening the card's menu. -->
{#if artists.length > 0}
	<div class="artists {size}">
		{#each artists as one, at (one.id)}{@const verbs = artistVerbs(
				one,
				session.isAdmin
			)}{#if at > 0}{', '}{/if}{#if verbs.length > 0}<span
					class="door"
					role="presentation"
					oncontextmenu={(event) => event.stopPropagation()}
					><ContextMenu triggerClass="song-artist-door" label="Actions for {one.name}"
						>{@render press(one)}{#snippet items()}<VerbMenuItems
								ids={[one.id]}
								subjectId={one.id}
								verbs={menuVerbs(verbs)}
							/>{/snippet}</ContextMenu
					></span
				>{:else}{@render press(one)}{/if}{/each}
	</div>
{:else if size === 'card'}
	<div class="artists card" aria-hidden="true">{'\u00a0'}</div>
{/if}

<style>
	.artists {
		margin: 0;
		min-inline-size: 0;
		color: var(--sift-ink-3);
	}

	/* Under a card: one line, cut with an ellipsis. */
	.card {
		font: var(--text-body-sm);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.page {
		font: var(--text-body);
	}

	/* The trigger stays in the line, its whitespace no space before a comma. */
	.artists :global(.song-artist-door),
	.door {
		display: inline-flex;
	}

	.artists a {
		color: inherit;
		text-decoration: none;
	}

	/* Ink, not ground: a word with no box steps its ink on hover, as every link does. */
	.artists a:hover {
		color: var(--sift-ink);
		text-decoration: underline;
		transition: color var(--dur-instant) var(--ease);
	}

	/* The card name's own focus mark, so the two links on one card answer the keyboard alike. */
	.artists a:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}
</style>
