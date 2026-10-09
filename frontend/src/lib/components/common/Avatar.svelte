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
	/* A person's picture, or a letter tinted from the name, which reads as no chosen cover. */
	import { Avatar } from 'bits-ui';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** Where the picture is. Absent, or failing to load, tries `instead` and then the letter. */
		src?: string | null;
		/** A second address to try: the file's own still while a chosen moment's cover renders. */
		instead?: string | null;
		/** Whose it is. The letter and the tint both come from this, and it is the accessible name. */
		name: string;
		shape?: AvatarShape;
		/** Hide it from a screen reader where the name is written beside it. */
		decorative?: boolean;
		/** Off for anything above the fold. The wall of faces wants it on. */
		lazy?: boolean;
		/** A site's logo, shown whole rather than cropped; at the letter's size on a portrait. */
		mark?: boolean;
		/**
		 * A face-box mark drawn with no ground, for a row listing Sites; the letter keeps its tile.
		 */
		bare?: boolean;
		/** A glyph in the letter's place on the same tint (a song with no cover). */
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

	/* Reset on a new address, so a replaced cover is asked for again. */
	let failed = $state(false);
	$effect(() => {
		void src;
		failed = false;
	});
	const showing = $derived(failed ? instead : src);

	/* Blank ground while loading: the letter would claim nobody chose a picture. */
	let heard = $state<{ at: string | null | undefined; status: string } | null>(null);
	const waiting = $derived(
		Boolean(showing) && !(heard?.at === showing && heard?.status === 'error')
	);

	/* A hue from the name, so a person keeps their tint when the list re-sorts. */
	const hue = $derived(
		[...name].reduce((total, letter) => (total * 31 + letter.charCodeAt(0)) % 360, 7)
	);
</script>

<!-- The `child` snippet, so scoped styles reach the elements. The root reports the loading
status (see `instead`), keyed on the address since the library never re-asks a loaded one. -->

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
			<!-- The hue is set here: a site's mark stands on the same tint as the letter. -->
			<div
				{...props}
				class="avatar {shape}"
				class:mark
				class:bare={bareMark}
				style:--hue={hue}
				role={decorative ? 'presentation' : 'img'}
			>
				{#if showing && mark && shape === 'portrait'}
					<!-- A portrait mark stands on its own picture enlarged, blurred and scrimmed: the same
					cached address, hidden from a screen reader. -->

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
				Drawn with no address or a failed one; the library watches the image itself.
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
		/* Inherited, so whatever clips this decides the shape. */
		border-radius: inherit;
		background: var(--sift-surface-3);
		/* So the letter is sized as a share of the picture. */
		container-type: inline-size;
		/* The name's tint, named once: the letter and a site's mark both stand on it. */
		--tint: hsl(var(--hue) 32% 24%);
	}

	/* Square by default, the frame cropped to it. */
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

	/* A mark is contained, not cropped: a logo is drawn to its own edges. */
	.picture.mark {
		object-fit: contain;
	}

	/* On a portrait, the mark takes the letter's 30cqi rather than upscaling across the width. */
	.avatar.portrait.mark {
		background: var(--tint);
	}

	/* The mark enlarged and blurred as the ground, grown past the box so its edges do not fade. */
	.avatar.portrait.mark .ground {
		position: absolute;
		inset: calc(-2 * var(--band-blur));
		inline-size: calc(100% + 4 * var(--band-blur));
		block-size: calc(100% + 4 * var(--band-blur));
		object-fit: cover;
		object-position: center;
		filter: blur(var(--band-blur));
	}

	/* One even scrim for every mark, at the page backdrop's strength; Avatar.svelte.test.ts pins
	   the worst contrast (a black mark, 1.06:1). */
	.avatar.portrait.mark .ground-scrim {
		position: absolute;
		inset: 0;
		background: var(--band-scrim);
	}

	/* Every mark at the letter's box, so a wall of Sites shows one size; no image-rendering set. */
	.avatar.portrait .picture.mark {
		position: absolute;
		inset: 0;
		margin: auto;
		inline-size: 30cqi;
		block-size: 30cqi;
	}

	/* A bare mark: no ground and no clip, so the radius never cuts a logo; the letter keeps it. */
	.avatar.bare {
		background: none;
		overflow: visible;
	}

	.avatar.bare .monogram {
		border-radius: inherit;
	}

	/* The letter on a muted tint of its name, its capital 30cqi tall to match a mark. */
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

	/* A glyph at the letter's size, larger than any icon size class. */
	.monogram :global(.icon) {
		font-size: 30cqi;
		font-size-adjust: none;
		inline-size: auto;
		block-size: auto;
	}
</style>
