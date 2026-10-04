<script lang="ts">
	/*
	 * Who a song credits, in credit order: one line, each artist a press that opens the Music wall
	 * filtered to that artist.
	 *
	 * The same line under a song's card on the Music wall, under a song's card on a Music tab and
	 * under the name on a song's own page, so an artist is reached the same way wherever a song is
	 * drawn. The filter is the wall's own `artists=` parameter (the Artists column of its filter
	 * panel), so the press lands as a chip that can be taken off, added to or turned into "not this
	 * artist", rather than a page of its own: an artist is a field of a song, not a thing in the
	 * sidebar.
	 *
	 * The parameter is the artist's id, which is the one value naming exactly one artist; the chip
	 * draws a name rather than the id because the press hands the name to the bar's memory of what
	 * each filter value is called (`rememberFacetNames`), the memory the filter panel fills when it
	 * counts the column.
	 *
	 * Under a card, a song that credits nobody still draws its (empty) line, so its card is as tall
	 * as its neighbours and a wall of songs stays a grid of equal cards. On a song's page nothing is
	 * drawn for nobody: there is no row of neighbours to keep in step with.
	 *
	 * NOT ON THE GALLERY: it draws only inside a song's card (`EntityCard`'s `byline`) and a song's
	 * header, and the gallery's cards and headers draw no song; an entry of its own would be the
	 * line without the card that gives it its place.
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

<!-- An artist with verbs (an admin's Rename) answers a right-click with the app's menu. The press
     sits inside a song's card, which has a menu of its own: the right-click is the artist's and
     goes no further, so the card's menu does not open over it. -->
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

	/* Under a card: one line, cut with an ellipsis where the card is narrower than the names, as
	   the card's own name above it is. The whole list is on the song's page, a press away. */
	.card {
		font: var(--text-body-sm);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.page {
		font: var(--text-body);
	}

	/* The menu's trigger wraps a press in a block of its own; here it stays in the line. A flex box
	   inside the line, so the trigger's own whitespace is not drawn as a space before the comma. */
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
