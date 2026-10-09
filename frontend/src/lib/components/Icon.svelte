<script lang="ts" module>
	/** The sanctioned glyph sizes, each a class. */
	export type IconSize = 14 | 16 | 18 | 20 | 28 | 34;
</script>

<script lang="ts">
	import generated from '$lib/generated/icon-codepoints.json';
	import type { IconName } from '$lib/design/icons';

	// Typed here, since a JSON import would widen the key back to `string`.
	const codepoints = generated as Record<IconName, string>;

	interface Props {
		name: IconName;
		filled?: boolean;
		/** 28 for a glyph with a digit drawn into it, which at 20 is a smudge. */
		size?: IconSize;
		label?: string;
	}

	let { name, filled = false, size = 20, label }: Props = $props();

	// A class, not a style attribute (the policy refuses inline styles). A name this build lacks
	// draws an empty gap rather than throwing, since server-sent names are cast unchecked.
	const glyph = $derived.by(() => {
		const point = parseInt(codepoints[name], 16);
		return Number.isFinite(point) ? String.fromCodePoint(point) : '';
	});
</script>

<!-- `label` names a standalone icon; one repeating its text is hidden instead. -->
<span
	class="icon size-{size}"
	class:filled
	role={label ? 'img' : undefined}
	aria-label={label}
	aria-hidden={label ? undefined : 'true'}>{glyph}</span
>

<style>
	/* Centred by the typeface itself; the alignment suite keeps it so. */
	.icon {
		font-family: 'Material Symbols Rounded';
		line-height: 1;
		display: inline-block;
		flex: none;
		white-space: nowrap;
		direction: ltr;
		user-select: none;
		-webkit-font-smoothing: antialiased;

		/* Fill is the active state, which is why this is a variable font. */
		font-variation-settings: 'FILL' 0;
		transition: font-variation-settings var(--dur-fast) var(--ease);
	}

	.icon.filled {
		font-variation-settings: 'FILL' 1;
	}

	/* The standing marks beside a folder: facts to glance at. */
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

	.size-28 {
		font-size: 28px;
		width: 28px;
		height: 28px;
	}

	/* Play and pause fill less of their box, so they are drawn larger. */
	.size-34 {
		font-size: 34px;
		width: 34px;
		height: 34px;
	}
</style>
