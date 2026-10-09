<script lang="ts">
	/*
	 * The handful of face crops that stands for a card, and the link into whatever it stands for.
	 *
	 * One component for the pile wall, the Identified wall and the group card, so the row, the
	 * squares, the radius, the lazy loading, the selection outline and the click ordering are
	 * written once. Copies drift: an outline on some and not others, a press that opens a pile when
	 * you meant to let go of it, a fix to the spacing in one and not the rest.
	 *
	 * Not the interactive face tiles. Those are buttons with their own marks, counts and per-face
	 * decisions, and folding them in here would mean a prop for every one of those: a shared
	 * component with a switch for each caller is two components wearing one name.
	 */
	import { TILE_ID } from '$lib/components/common';
	import { cropUrl, type Sighting } from '$lib/people/faces.svelte';

	interface Props {
		/** The faces to show. Whatever the caller passes; this does not decide how many. */
		faces: Sighting[];
		/** Where the row leads. Omitted, it is not a link: a person this account may not be told
		 *  about has no page to open, and the row still has to be drawn. */
		href?: string;
		label?: string;
		/** Drawn as picked. */
		picked?: boolean;
		onpointerdown?: (event: PointerEvent) => void;
		onpointerup?: () => void;
		onclickcapture?: (event: MouseEvent) => void;
		/**
		 * The right button, so a wall can pick the card before its menu opens.
		 *
		 * Forwarded rather than handled: the menu belongs to whoever draws this row, because what can
		 * be done to the thing it stands for is the wall's business and not the row of crops'. What
		 * this contributes is that the gesture reaches the wall at all: without it the press would
		 * land on a bare `<img>` and the browser would answer with "Copy image address".
		 */
		oncontextmenu?: (event: MouseEvent) => void;
		/**
		 * What this row STANDS FOR, so a press-and-drag sweep can find it under the pointer.
		 *
		 * `TileGesture` reads the id off `document.elementFromPoint` while the pointer is somewhere
		 * else entirely (that is the whole gesture) so it has to be in the DOM rather than
		 * closed over by the handler. Without it the hold picks the row it started on and the drag
		 * across the wall then picks nothing, which reads as the gesture being half-broken.
		 *
		 * Absent on a wall with no selection: an id there would advertise a gesture that does
		 * nothing. Only ever on the LINK form, because a row that cannot be pressed cannot be swept.
		 */
		sweepId?: string;
		/**
		 * How many cells this row draws, whatever it was handed.
		 *
		 * So every card on a wall is the same size. The crops are the tallest thing on a card, and
		 * a card as tall as its subject's face count would make the rows step up and down.
		 *
		 * Six crops a row whatever the card's width, so a count of cells is whole rows and a height,
		 * the same on every card of that wall at every window size: a row filled by width would
		 * leave twelve faces two cells short of a second row of seven.
		 *
		 * Handed more than it draws, the last cell says how many are not shown, so a card never
		 * reports twelve of somebody's two thousand faces as though that were all. Handed fewer,
		 * the remaining cells are empty and hold their room.
		 *
		 * Absent, this draws exactly what it was given and reserves nothing, right where the row is
		 * not one of a wall of equals: a file's own strip of faces is as long as that file's faces.
		 */
		most?: number;
		/** Columns of a card's strip: six, or three or two where a card's six columns are shared
		 *  between two or three groups, so every crop on a wall is one size. */
		across?: 2 | 3 | 6;
		/** How many faces the row stands for, where the caller was handed only some of them: the
		 *  counter counts the rest too ("+217" of 229 when twelve came). */
		total?: number;
		/**
		 * Whether a short row holds the room of a full one (the default, with `most`). Off, `most`
		 * is only the cap and its counter: a card of two faces takes one line of crops, and a wall
		 * whose cards push their question to the foot (`DecisionCard`) keeps its bottoms even
		 * without empty cells.
		 */
		hold?: boolean;
	}

	let {
		faces,
		href,
		label,
		picked = false,
		onpointerdown,
		onpointerup,
		onclickcapture,
		oncontextmenu,
		sweepId,
		most,
		total,
		across = 6,
		hold = true
	}: Props = $props();

	const sweepable = $derived(sweepId ? { [TILE_ID]: sweepId } : {});

	/* How many are not drawn, and 0 when everything is. The counter takes a cell of its own, so it
	   is the number over the CAP LESS ONE: a "+1" standing where the one face it counts could
	   have been drawn would be a cell spent hiding something it had room for. */
	const standing = $derived(Math.max(faces.length, total ?? 0));
	const hidden = $derived(most !== undefined && standing > most ? standing - (most - 1) : 0);
	/** The crops actually drawn: everything, or as many as fit beside whatever the counter needs. */
	const shown = $derived(most === undefined ? faces : faces.slice(0, hidden > 0 ? most - 1 : most));
	/* The empty cells that keep a short row the same height as a full one. An array because Svelte
	   iterates values rather than counting; the index is the key and nothing else reads it. */
	const holes = $derived(
		most === undefined || !hold
			? []
			: Array.from({ length: most - shown.length - (hidden > 0 ? 1 : 0) })
	);
</script>

{#if href}
	<!-- The sweep's hook is spread FIRST, before this component's own class. It carries one data
	     attribute and never a class, so the order changes nothing today, but a spread written
	     after a literal `class` is the shape that replaces it outright the day the object grows
	     one, and `caller-class.test.ts` reads the shape rather than the object. -->
	<a
		{...sweepable}
		class="faces"
		class:rows={most !== undefined}
		class:across-2={across === 2}
		class:across-3={across === 3}
		class:picked
		{href}
		aria-label={label}
		{onpointerdown}
		{onpointerup}
		onpointercancel={onpointerup}
		{onclickcapture}
		{oncontextmenu}
	>
		{#each shown as face (face.track_id)}
			<img src={cropUrl(face)} alt="" loading="lazy" />
		{/each}
		{#if hidden > 0}
			<span class="more">+{hidden.toLocaleString()}</span>
		{/if}
		{#each holes as _hole, at (at)}
			<!-- Room held, and nothing in it. `aria-hidden` because an empty cell is a fact about
			     the layout and there is nothing here to announce. -->
			<span class="hole" aria-hidden="true"></span>
		{/each}
	</a>
{:else}
	<div
		class="faces"
		class:rows={most !== undefined}
		class:across-2={across === 2}
		class:across-3={across === 3}
	>
		{#each shown as face (face.track_id)}
			<img src={cropUrl(face)} alt="" loading="lazy" />
		{/each}
		{#if hidden > 0}
			<span class="more">+{hidden.toLocaleString()}</span>
		{/if}
		{#each holes as _hole, at (at)}
			<!-- Room held, and nothing in it. `aria-hidden` because an empty cell is a fact about
			     the layout and there is nothing here to announce. -->
			<span class="hole" aria-hidden="true"></span>
		{/each}
	</div>
{/if}

<style>
	/*
	 * A grid of as many crops as fit, each growing to fill its share of the row, so a card whose
	 * width is not a multiple of the crop size leaves no strip of ground down its edge (the wall's
	 * columns stretch with `1fr`). The crops are the floor and the card's width is what they fill:
	 * four across at 15.25rem, five when the column is wider.
	 */
	.faces {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(3.25rem, 1fr));
		gap: var(--space-1);
	}

	/* A card's strip: six across at every width, so its cells are whole rows. */
	.faces.rows {
		grid-template-columns: repeat(6, minmax(0, 1fr));
	}

	.faces.rows.across-3 {
		grid-template-columns: repeat(3, minmax(0, 1fr));
	}

	.faces.rows.across-2 {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}

	a.faces {
		border-radius: var(--radius-sm);
		text-decoration: none;
	}

	a.faces:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	a.faces.picked {
		outline: 2px solid var(--sift-accent);
		outline-offset: 2px;
	}

	.faces img,
	.faces .more,
	.faces .hole {
		inline-size: 100%;
		aspect-ratio: 1;
		border-radius: var(--radius-sm);
		object-fit: cover;
		background: var(--sift-surface-3);
	}

	/* How many more there are. It sits in the grid as a cell like any crop, so the row is the same
	   height with it as without. */
	.faces .more {
		display: grid;
		place-items: center;
		color: var(--sift-ink-2);
		font: var(--text-label);
	}

	/* A cell holding its room and nothing else. No ground at all: a wall of cards would otherwise
	   show its own scaffolding, and what this is for is the height rather than the mark. */
	.faces .hole {
		background: none;
	}
</style>
