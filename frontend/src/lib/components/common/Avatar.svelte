<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Avatar',
		category: 'primitive',
		role: "a person's or site's picture, or the initial that stands in for one",
		basis: 'bits-ui:Avatar',
		states: ['portrait', 'initial', 'glyph', 'missing']
	} satisfies DesignEntry;

	/** How it is cut. A `face` is a square; a `portrait` is the 3:4 a person's cover is shot at. */
	export type AvatarShape = 'face' | 'portrait';
</script>

<script lang="ts">
	/*
	 * A person's picture, or the letter that stands in for it.
	 *
	 * One component so "has the picture failed" and its reset on a new address live in one place; a
	 * copy that forgot the reset would show a monogram over a good photograph until the page was
	 * rebuilt.
	 *
	 * The letter is a picture in its own right. An empty grey rectangle reads as a photo that
	 * failed to load; a letter in a colour derived from the name reads as somebody without a chosen
	 * cover, and stays recognisable tomorrow because the colour comes from the name rather than the
	 * position in the list.
	 */
	import { Avatar } from 'bits-ui';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** Where the picture is. Absent, or failing to load, tries `instead` and then the letter. */
		src?: string | null;
		/**
		 * A second address to try when the first one does not load.
		 *
		 * There for one case and it is a real one: an entity's cover is served at the entity's own
		 * address, and that address answers 404 for as long as a chosen MOMENT of a clip has not
		 * been rendered yet, which is ordinary, because the still is queued when the moment is
		 * chosen. `kernel/covers.py` says in its own words that "the client falls back to the file's
		 * own picture, exactly as a mark's tile does", and this is that fallback. Without it the
		 * monogram would replace somebody's photograph with a coloured letter for several seconds,
		 * on the machine that had just chosen the frame.
		 *
		 * The second address is the FILE's own still, which the caller already knows and which is
		 * scoped to the same viewer by the same permission read, so this is a different picture of
		 * the same thing rather than a way round anything. When it fails too, the letter still
		 * stands behind both.
		 */
		instead?: string | null;
		/** Whose it is. The letter and the tint both come from this, and it is the accessible name. */
		name: string;
		shape?: AvatarShape;
		/**
		 * Whether to hide it from a screen reader.
		 *
		 * True wherever the name is already written beside the picture, which is nearly everywhere:
		 * a card says the person's name underneath, and hearing it twice is worse than hearing it
		 * once. False only where the picture stands alone.
		 */
		decorative?: boolean;
		/** Off for anything above the fold. The wall of faces wants it on. */
		lazy?: boolean;
		/**
		 * Whether this picture is a MARK rather than a frame.
		 *
		 * A frame is a still out of a clip: the subject is in the middle and the edges are room, so
		 * cropping it to the box's shape throws away room. A mark is a site's own logo, drawn to its
		 * own edges, and cropping it takes pieces out of the logo. A mark is shown whole.
		 *
		 * On a PORTRAIT box it is also shown at the LETTER'S size rather than filling the width.
		 * See the rules at the bottom of this file, which say why.
		 */
		mark?: boolean;
		/**
		 * Whether a MARK in a FACE box is drawn with nothing behind it.
		 *
		 * A face box stands on a ground of its own (`--sift-surface-3`), which is right for a tile
		 * in a wall of entities: every tile is the same square whatever its picture does. In a ROW
		 * that lists sites (the download queue, the cookies sheet), the same ground reads as a
		 * plate the site's logo was put on: most logos in the pack are a disc or a shape on a
		 * transparent square, so the ground would show in the corners as a grey tile round every one.
		 * Bare, the logo is drawn the way a person's links draw theirs (`RecordSummary`,
		 * `RecordValue`): the picture alone, at the box's size, uncropped.
		 *
		 * The LETTER keeps its tile when no picture answers, because the letter is nothing without
		 * the tint it stands on. Only a face mark can be bare: a portrait mark's ground is the
		 * mark's own blurred colour and is the whole of how a wall of sites reads.
		 */
		bare?: boolean;
		/**
		 * A glyph drawn in the letter's place, on the same tint, where nothing was chosen.
		 *
		 * For a kind of thing whose name tells two of them apart less well than what they ARE: a
		 * song with no cover is drawn as the music glyph (the Songs page's own), because a wall of
		 * songs is a wall of music and a coloured letter would read as somebody's initial. The
		 * tint still comes from the name, so two songs side by side are still two tiles.
		 */
		glyph?: IconName;
		/** Told when `src` fails, so a caller drawing one address many times can stop asking. */
		onfailed?: () => void;
	}

	let {
		src,
		instead,
		name,
		shape = 'face',
		decorative = true,
		lazy = false,
		mark = false,
		bare = false,
		glyph,
		onfailed
	}: Props = $props();

	/* Bare only where it means something. See `bare`. */
	const bareMark = $derived(bare && mark && shape === 'face');

	const initial = $derived(name.trim().charAt(0).toUpperCase());

	/* Which of the two addresses is being tried. Reset whenever the first one changes, so a cover
	   that was replaced is asked for again rather than staying on whatever it fell back to. */
	let failed = $state(false);
	$effect(() => {
		void src;
		failed = false;
	});
	const showing = $derived(failed ? instead : src);

	/*
	 * While a picture is on its way the box stays its own blank ground: the letter says nobody
	 * chose a picture, untrue of a cover still loading. With no address, or every one failed, the
	 * letter is drawn at once. The answer is kept with its address, so a new one starts waiting.
	 */
	let heard = $state<{ at: string | null | undefined; status: string } | null>(null);
	const waiting = $derived(
		Boolean(showing) && !(heard?.at === showing && heard?.status === 'error')
	);

	/* A colour from the name, not from the position. Two people keep their own tints when the list
	   is re-sorted, which is what makes a wall of letters scannable at all. */
	const hue = $derived(
		[...name].reduce((total, letter) => (total * 31 + letter.charCodeAt(0)) % 360, 7)
	);
</script>

<!--
	The `child` snippet everywhere, so this file's scoped styles reach the elements. Rendered by the
	library they would be a stranger's elements and every rule below would match nothing, which is
	the failure mode that looks like the component simply has no styling.
-->
<!-- The loading status is reported by the ROOT rather than by the image, which is where bits-ui
     puts it. It is what turns a 404 into the second address: see `instead`.

     Keyed on the address: the library answers an address it has once loaded from memory and
     never asks again, so a list that hands one Avatar a new address in place (a row keyed by
     position) would keep the old picture's state and draw neither picture nor letter. A new
     address is a new root. -->
{#key showing}
	<Avatar.Root
		onLoadingStatusChange={(status) => {
			heard = { at: showing, status };
			// Only the FIRST address gets a second chance. Without the guard a failure of `instead`
			// would set the flag again on an element already showing it, leaving the component claiming
			// a state it is not in.
			if (status === 'error' && !failed) onfailed?.();
			if (status === 'error' && !failed && instead) failed = true;
		}}
	>
		{#snippet child({ props })}
			<!-- The hue is set HERE rather than on the letter, because the letter is not the only thing
		     that stands on it: a site's mark is drawn small on the same tint, and the tint has to
		     exist whether or not the letter is being rendered. -->
			<div
				{...props}
				class="avatar {shape}"
				class:mark
				class:bare={bareMark}
				style:--hue={hue}
				role={decorative ? 'presentation' : 'img'}
			>
				{#if showing && mark && shape === 'portrait'}
					<!--
					The ground is the mark's own colour, the same move the entity pages make with a
					cover (`PageFrame`'s `.frame-backdrop`): the picture enlarged, blurred past
					every feature, and a scrim poured over it so what stands on it still reads. A
					flat tint from the name would be a colour unrelated to the site.

					A second `<img>` rather than a CSS layer, because CSS cannot reach an element's
					`src`. It is the same address, so the browser serves it from cache with no
					second request. Hidden from a screen reader: it is the picture below, out of
					focus.

					One ground for every portrait mark, whatever the mark's colour: a tile unlike
					its neighbours reads as a different kind of tile. The measured cost is at
					`.ground-scrim` below.

					Portrait only, because only a portrait box styles it: a `face` box is 24 pixels
					across, the mark fills it, and there is no ground to see.
				-->
					<img
						class="ground"
						src={showing}
						alt=""
						aria-hidden="true"
						loading={lazy ? 'lazy' : 'eager'}
						decoding="async"
						draggable="false"
					/>
					<span class="ground-scrim" aria-hidden="true"></span>
				{/if}
				{#if showing}
					<Avatar.Image src={showing} alt={decorative ? '' : name}>
						{#snippet child({ props: imageProps })}
							<img
								{...imageProps}
								class="picture"
								class:mark
								loading={lazy ? 'lazy' : 'eager'}
								decoding="async"
							/>
						{/snippet}
					</Avatar.Image>
				{/if}

				<!--
				Drawn when there is no address AND when one was given and did not load. The second half
				is the whole reason this is the library's: it watches the image element rather than
				being told, so a picture that 404s after the markup was written still falls back,
				and it starts watching again by itself when the address changes.
			-->
				<Avatar.Fallback>
					{#snippet child({ props: fallbackProps })}
						<span {...fallbackProps} class="monogram" class:waiting aria-hidden="true">
							{#if glyph}
								<Icon name={glyph} />
							{:else}
								{initial}
							{/if}
						</span>
					{/snippet}
				</Avatar.Fallback>
			</div>
		{/snippet}
	</Avatar.Root>
{/key}

<style>
	.avatar {
		position: relative;
		display: block;
		overflow: hidden;
		/* Inherited, so whatever clips this decides the shape. A card's picture is square at the top
		   and rounded at the bottom by the card; a header's cover is rounded on all four. Owning a
		   radius here would mean fighting one of those two. */
		border-radius: inherit;
		background: var(--sift-surface-3);
		/* So the letter can be sized as a share of the picture rather than as a number per caller,
		   which would differ between a card and a header for no reason anybody chose. */
		container-type: inline-size;
		/* The tint this picture's name comes out as, named ONCE. The letter stands on it and so
		   does a site's mark, and two copies of one colour is two chances for them to drift. */
		--tint: hsl(var(--hue) 32% 24%);
	}

	/* A wall of faces wants to be scannable rather than to preserve anybody's aspect ratio, so the
	   square is the default and the frame is cropped to it. */
	.avatar.face {
		aspect-ratio: 1;
	}

	.avatar.portrait {
		aspect-ratio: 3 / 4;
	}

	.picture {
		inline-size: 100%;
		block-size: 100%;
		object-fit: cover;
		display: block;
	}

	/*
	 * A mark is contained, not cropped. A still from a clip is a frame whose edges are room; a
	 * site's tab icon is a logo drawn to its own edges, and cropping a 256x256 mark to the card's
	 * box would take a quarter of its width. So the whole mark is shown, on the card's own ground,
	 * at whatever size fits.
	 */
	.picture.mark {
		object-fit: contain;
	}

	/*
	 * On a portrait box the mark is drawn at the letter's size, not across the whole width.
	 * `contain` alone fits a face box exactly, but on a portrait box it fits the narrow axis, so a
	 * 128-pixel picture would be drawn over 300 pixels wide, an upscale that looks jagged and
	 * blurry.
	 *
	 * So the mark takes the monogram's own `30cqi`, centred on the same ground, and a wall of sites
	 * reads as one thing: a coloured ground with a small mark or a small letter on it.
	 *
	 * `image-rendering` is deliberately not set: the browser's own filtering is what makes a
	 * rescale clean, and `pixelated` or `crisp-edges` would put the jaggedness back by hand. See
	 * the rule below.
	 */
	.avatar.portrait.mark {
		background: var(--tint);
	}

	/*
	 * The mark, enlarged and blurred, as the tile's ground.
	 *
	 * Grown past the box by twice the blur on every side for the reason `PageFrame` writes out at
	 * length: a blur samples what is outside the element as transparent, so a picture drawn to the
	 * edges fades to nothing along all four of them and the ground reads as a soft rectangle
	 * floating in the tile. The size is written down beside the inset and from the same token, or
	 * an absolutely positioned `<img>` with no size takes its intrinsic width and anchors left.
	 *
	 * `cover` and not `contain`: nothing here is being read, it is a wash of the mark's colour, and
	 * a contained copy would leave the tint showing along two edges, a flat ground with a blurred
	 * stripe in the middle of it.
	 *
	 * The tint underneath is kept and is not dead: it is what a mark with transparency stands on,
	 * and what is seen for the moment before this loads.
	 */
	.avatar.portrait.mark .ground {
		position: absolute;
		inset: calc(-2 * var(--band-blur));
		inline-size: calc(100% + 4 * var(--band-blur));
		block-size: calc(100% + 4 * var(--band-blur));
		object-fit: cover;
		object-position: center;
		filter: blur(var(--band-blur));
	}

	/*
	 * The scrim over it, at the same strength the page's own backdrop uses.
	 *
	 * Flat rather than the page's gradient: the page fades at the top because a hard edge across a
	 * screen's width reads as a rendering fault, but a tile has a border and a radius already, and
	 * an even scrim keeps the mark at one contrast wherever the light behind it falls.
	 *
	 * One scrim for every mark, and its cost is measured. `Avatar.svelte.test.ts` does the
	 * arithmetic over every hue in every base: a white mark reads at 11:1 or better. A plain black
	 * mark reads at 1.06:1 at the worst hue, which is accepted: of 882 marks in the pack, 82 are
	 * `dark` by mean level and 29 are dark all through (95 in 100 opaque pixels at level 100 or
	 * under); the rest carry lighter shapes that read on this ground. The pack still records each
	 * mark's tone (`scripts/site_icon_art.py tone_of`), but a tile unlike its neighbours is judged
	 * worse than those 29. The test pins the 1.06, so a change that makes it worse is seen. (The
	 * tint does not bind it: under an 84% scrim it shows through at 16%.)
	 */
	.avatar.portrait.mark .ground-scrim {
		position: absolute;
		inset: 0;
		background: var(--band-scrim);
	}

	/*
	 * And it is drawn at the letter's box, whatever size the picture is.
	 *
	 * One measure for every mark: a ceiling would make the size of a site's mark a fact about that
	 * site's web server, and a wall of sites would show marks at four or five sizes meaning
	 * nothing. Many icons in the pack are small (128, 32 or 16 square), so the box is `30cqi`
	 * square and `contain` fits the mark inside it: a big mark is downscaled and a small one
	 * upscaled. A soft mark at the right size says which site this is; a sharp one at a third of
	 * the size looks like a fault. `scripts/build_site_icons.py` asks `/apple-touch-icon.png` first
	 * for any site whose favicon is under the cap, so only a site with nothing bigger stays small.
	 *
	 * The box is counted in device pixels: `30cqi` is about 96 CSS pixels on a Sites card, 144 or
	 * 192 on a screen at a scale of 1.5 or 2. The pack is built at 256, which keeps a full-size
	 * mark a downscale up to a scale of about 2.6.
	 *
	 * The blurred ground (`.ground`) is the same file. A mark with a transparent surround shows as
	 * a soft glow of the logo's colour over the tint; a mark on an opaque plate washes the tile
	 * with the plate's colour, which is a fact about the picture in the pack.
	 *
	 * `image-rendering` is deliberately not set; the browser's own filtering makes both directions
	 * clean.
	 */
	.avatar.portrait .picture.mark {
		position: absolute;
		inset: 0;
		margin: auto;
		inline-size: 30cqi;
		block-size: 30cqi;
	}

	/*
	 * A bare mark: no ground, and nothing clipped. The picture is contained in the box, so letting
	 * it past the corner radius crops nothing and draws nothing extra, and keeps the holder's
	 * radius from taking the corners off a logo drawn to its own edges. The letter still stands on
	 * its tile with the holder's corners: it takes the radius itself, since the box does not clip
	 * it.
	 */
	.avatar.bare {
		background: none;
		overflow: visible;
	}

	.avatar.bare .monogram {
		border-radius: inherit;
	}

	/*
	 * The letter, on a tint of its own name.
	 *
	 * Low saturation and low lightness so a wall of them is a texture rather than a set of competing
	 * signals: these stand in for a picture, they are not decoration in their own right. The hue
	 * moves and those two do not, which is what keeps every one of them legible.
	 *
	 * THE SAME SIZE AS A MARK. A mark on a portrait is drawn in a `30cqi` square (see above), so the
	 * letter's capital is made `30cqi` tall: `font-size-adjust` sets the size by the capital's height
	 * rather than by the em, which is taller than any capital in any face. Sized by the em, the
	 * letter is about two thirds of the height of the marks beside it, and a wall of sites or of a
	 * site's creators reads as two kinds of tile.
	 */
	.monogram {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		background: var(--tint);
		color: var(--sift-ink);
		font: var(--text-display);
		font-size: 30cqi;
		font-size-adjust: cap-height 1;
		line-height: 1;
		user-select: none;
	}

	.monogram.waiting {
		visibility: hidden;
	}

	/* A glyph in the letter's place, at the letter's size: the icon's own size classes stop at a
	   control's, and this one stands for a whole picture. Reached only inside this component's own
	   monogram, so no other icon is touched. */
	.monogram :global(.icon) {
		font-size: 30cqi;
		font-size-adjust: none;
		inline-size: auto;
		block-size: auto;
	}
</style>
