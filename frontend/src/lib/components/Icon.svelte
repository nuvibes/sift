<script lang="ts" module>
	/** The sanctioned glyph sizes. Each is a class; nothing draws a glyph at a size that is not one of these. */
	export type IconSize = 14 | 16 | 18 | 20 | 28 | 34;
</script>

<script lang="ts">
	import generated from '$lib/generated/icon-codepoints.json';
	import type { IconName } from '$lib/design/icons';

	// The generated map has a key for every name in the list (the build fails if one could not be
	// resolved) so this lookup cannot miss. Typed here rather than trusting the shape of a JSON
	// import, which would widen the key back to `string` and take the checking with it.
	const codepoints = generated as Record<IconName, string>;

	interface Props {
		name: IconName;
		/** Outlined normally; filled when its thing is the current one. */
		filled?: boolean;
		/**
		 * 20 in the rail, 18 in the top bar, 16 inline, 28 for a glyph with a number drawn into it.
		 *
		 * The fourth exists for the five-second jumps and for the play control the small panel puts
		 * over its picture. Those glyphs are not a symbol beside a label: the label is inside the
		 * drawing, and at 20px the digit is a smudge, which leaves the pair reading as two arrows
		 * that go some unspecified distance.
		 */
		size?: IconSize;
		/** Give the icon a label when it stands alone. See below. */
		label?: string;
	}

	let { name, filled = false, size = 20, label }: Props = $props();

	// The size is a class and not a style attribute: the policy this app is served under refuses
	// inline styles, which would silently leave the icon at the wrong size, and loosening it would
	// allow every injected inline style too. The sizes are the sanctioned list above, so there is
	// no number to pass, only a choice from that list.
	//
	// The glyph, or nothing at all for a name this build does not have. Some icon names arrive from
	// the server (the workbench asks each area what picture to draw its queue with) and are cast to
	// `IconName` unchecked. An unknown one would reach `fromCodePoint` as NaN, which throws, and a
	// throw while rendering takes down the whole screen. An empty string lays out as an icon-sized
	// gap, so a newer server costs an older client a missing picture rather than a missing page, as
	// the panel registry treats a queue it cannot draw. A gate refuses unknown names at the source;
	// this keeps the failure survivable when one gets past it.
	const glyph = $derived.by(() => {
		const point = parseInt(codepoints[name], 16);
		return Number.isFinite(point) ? String.fromCodePoint(point) : '';
	});
</script>

<!--
	An icon on its own is a button with no name to a screen reader, so `label` becomes its accessible
	name. An icon sitting beside text it merely repeats gets no label and is hidden instead: hearing
	"delete delete" is worse than hearing it once.
-->
<span
	class="icon size-{size}"
	class:filled
	role={label ? 'img' : undefined}
	aria-label={label}
	aria-hidden={label ? undefined : 'true'}>{glyph}</span
>

<style>
	/*
	 * The glyph's advance width is exactly the font size in this typeface and the box below is that
	 * size, so an icon laid out as ordinary text is already centred in it. `an icon and a word
	 * inside a button are centred together` in the alignment suite keeps it that way.
	 */
	.icon {
		font-family: 'Material Symbols Rounded';
		line-height: 1;
		display: inline-block;
		flex: none;
		white-space: nowrap;
		direction: ltr;
		user-select: none;
		-webkit-font-smoothing: antialiased;

		/* The fill axis is the active state. Colour still steps as well, but fill is what the eye
		   reads, and animating an axis is why this is a variable font and not thirty pictures. */
		font-variation-settings: 'FILL' 0;
		transition: font-variation-settings var(--dur-fast) var(--ease);
	}

	.icon.filled {
		font-variation-settings: 'FILL' 1;
	}

	/* The standing marks beside a folder in the library list: on this network, or on a disk in this
	   computer. They sit in a small circle next to a switch, and at 16 that circle is heavier than
	   anything else in the row: these are facts you glance at, not controls. 14 is 16 less about
	   fifteen per cent, which sits the pair down into the row. */
	.size-14 {
		font-size: 14px;
		width: 14px;
		height: 14px;
	}

	.size-16 {
		font-size: 16px;
		width: 16px;
		height: 16px;
	}

	.size-18 {
		font-size: 18px;
		width: 18px;
		height: 18px;
	}

	.size-20 {
		font-size: 20px;
		width: 20px;
		height: 20px;
	}

	/* For a glyph whose meaning is drawn inside it rather than written beside it. */
	.size-28 {
		font-size: 28px;
		width: 28px;
		height: 28px;
	}

	/* Play and pause. A triangle and two bars fill less of their box than a ringed arrow with a
	   digit in it, so at the same nominal size they read smaller than the controls beside them:
	   this is the size at which the three look like one set. */
	.size-34 {
		font-size: 34px;
		width: 34px;
		height: 34px;
	}
</style>
