<script lang="ts" module>
	import { SIZE_STEPS, type SizeStep } from '$lib/grid/justify';

	/** The notch the slider sits on; the middle while the grid sizes itself, since the bar cannot measure it. */
	export function nearestStep(step: SizeStep | null): number {
		if (step === null) return Math.floor(SIZE_STEPS.length / 2);
		const at = SIZE_STEPS.indexOf(step);
		return at === -1 ? Math.floor(SIZE_STEPS.length / 2) : at;
	}
</script>

<script lang="ts">
	/* NOT ON THE GALLERY: the top bar's own tile size, drawn on the screen's row while the bar has no room for it. */

	import { BarPanel, Slider, Tooltip } from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import { gridSize } from '$lib/grid/grid.svelte';
	import { NOT_HERE, ableTo, screenBar, whyNot } from './screen-bar.svelte';

	const sizable = $derived(ableTo(screenBar.tools.resizable));
</script>

<BarPanel label="Tile size">
	<Tooltip
		label={sizable ? 'Tile size' : whyNot(screenBar.tools.resizable, NOT_HERE.resize)}
		placement="bottom"
	>
		<label class="tile-size" class:off={!sizable}>
			<Icon name="grid_view" size={18} label="Tile size" />
			<Slider
				class="steps"
				label="Tile size"
				max={SIZE_STEPS.length - 1}
				disabled={!sizable}
				value={nearestStep(gridSize.step)}
				valueText="{nearestStep(gridSize.step) + 1} of {SIZE_STEPS.length}"
				oninput={(step) => gridSize.set(SIZE_STEPS[step])}
			/>
		</label>
	</Tooltip>
</BarPanel>

<style>
	.tile-size {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		color: var(--sift-ink-3);
	}

	/* DRESSED BY: .steps (`Slider` draws the track; this says how wide, a chooser's width). */
	.tile-size :global(.steps) {
		inline-size: var(--chooser-width);
	}

	.tile-size.off {
		opacity: 0.5;
	}
</style>
