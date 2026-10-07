<script lang="ts" module>
	/** Which wall draws an entity page's tab: the media grid for Files and for Loops, the wall of
	 *  cards for every other. */
	export function wallOfTab(tab: string): string {
		return tab === 'files' || tab === 'loops' ? tab : 'cards';
	}
</script>

<script lang="ts" generics="T extends string">
	/*
	 * An entity page's tabs across walls (Files and Loops are media grids, the rest a wall of
	 * cards), so a press never shows an empty screen between two full ones. The wall being left
	 * stays over the new one, quieter and out of reach, until the new one has its answer and has
	 * finished arriving; it leaves the top bar to the new one (`TabLayer`). The new wall is drawn at
	 * once underneath, so it asks the server immediately.
	 *
	 * NOT ON THE GALLERY: it draws whatever walls a page hands it, and those walls fetch.
	 */
	import { onDestroy, untrack, type Snippet } from 'svelte';
	import TabLayer from './TabLayer.svelte';

	interface Props {
		/** The tab chosen. */
		tab: T;
		/** Which wall draws a tab. Two tabs on one wall are the wall's own business. */
		wallOf: (tab: T) => string;
		/** One wall, for the tab it is drawn for; the page calls `arrived` once it has its answer. */
		surface: Snippet<[tab: T, arrived: () => void]>;
	}

	let { tab, wallOf, surface }: Props = $props();

	interface Layer {
		wall: string;
		tab: T;
	}

	/** The walls on screen: the chosen one last, and at most one being left over it. */
	let layers = $state<Layer[]>([]);
	/** The walls that have had an answer since they were drawn: only those are worth holding. */
	const landed = new Set<string>();
	/** The element each wall is drawn in, for the arrival to finish on. */
	const boxes = new Map<string, HTMLElement>();

	/* A wall whose answer never comes (a failure says so in its body) is not hidden for long. */
	const HOLD_AT_MOST_MS = 1500;
	let timer: ReturnType<typeof setTimeout> | null = null;

	$effect.pre(() => {
		const chosen = tab;
		untrack(() => {
			const wall = wallOf(chosen);
			const current = layers.at(-1);
			if (!current) {
				layers = [{ wall, tab: chosen }];
				return;
			}
			if (current.wall === wall) {
				current.tab = chosen;
				return;
			}
			const held = layers.length > 1 ? layers[0] : null;
			if (held && !landed.has(current.wall)) {
				// Pressed again before the new wall answered: what is on screen is still the held
				// wall, so it stays held, or comes back if it is the one chosen.
				if (held.wall === wall) {
					held.tab = chosen;
					layers = [held];
					landed.add(wall);
					release();
					return;
				}
				layers = [held, { wall, tab: chosen }];
			} else {
				// The wall chosen before is held over the new one; anything held before that goes.
				layers = [current, { wall, tab: chosen }];
			}
			landed.delete(wall);
			if (timer) clearTimeout(timer);
			timer = setTimeout(release, HOLD_AT_MOST_MS);
		});
	});

	/** Let the held wall go: everything but the chosen one. */
	function release(): void {
		if (timer) clearTimeout(timer);
		timer = null;
		if (layers.length > 1) layers = layers.slice(-1);
		const shown = layers.at(-1);
		if (shown) landed.add(shown.wall);
	}

	/* The held wall goes once the new one has finished arriving: its entrance is a fade from
	   nothing, and uncovered halfway it would be the flash this exists to stop. */
	function arrivedOn(wall: string): void {
		landed.add(wall);
		if (layers.at(-1)?.wall !== wall || layers.length < 2) return;
		// The entrance starts on a frame after the answer lands, so it is looked for two frames on.
		afterFrames(2, () => {
			const box = boxes.get(wall);
			const moving =
				typeof box?.getAnimations === 'function'
					? box
							.getAnimations({ subtree: true })
							// Only what ends: a shimmer or a spinner repeats for as long as work runs.
							.filter((one) => one.effect?.getComputedTiming().endTime !== Infinity)
							.map((one) => one.finished.catch(() => undefined))
					: [];
			void Promise.all(moving).then(() => {
				if (layers.at(-1)?.wall === wall) release();
			});
		});
	}

	function afterFrames(count: number, then: () => void): void {
		if (count === 0 || typeof requestAnimationFrame !== 'function') return then();
		requestAnimationFrame(() => afterFrames(count - 1, then));
	}

	function holding(element: HTMLElement, wall: string) {
		boxes.set(wall, element);
		return () => {
			if (boxes.get(wall) === element) boxes.delete(wall);
		};
	}

	onDestroy(() => {
		if (timer) clearTimeout(timer);
	});
</script>

<div class="hold">
	{#each layers as layer, at (layer.wall)}
		{@const held = at < layers.length - 1}
		<div
			class="layer"
			class:held
			inert={held}
			aria-hidden={held || undefined}
			{@attach (element: HTMLElement) => holding(element, layer.wall)}
		>
			<TabLayer {held}>{@render surface(layer.tab, () => arrivedOn(layer.wall))}</TabLayer>
		</div>
	{/each}
</div>

<style>
	/* The page's whole box, every wall in one cell so the held one lies over the chosen one. */
	.hold {
		flex: 1;
		display: grid;
		grid-template: minmax(0, 1fr) / minmax(0, 1fr);
		min-block-size: 0;
	}

	.layer {
		grid-area: 1 / 1;
		display: flex;
		flex-direction: column;
		min-block-size: 0;
		min-inline-size: 0;
	}

	/* Over the chosen wall on the page's ground, its body as quiet as `EntityGrid`'s `held`. On the
	   box the body scrolls in: the one inside keeps the opacity its entrance wrote on it. */
	.layer.held {
		z-index: 1;
		background: var(--sift-bg);
	}

	.layer.held :global(.frame-body-slot) {
		opacity: 0.6;
		transition: opacity var(--dur-base) var(--ease) var(--dur-fast);
	}
</style>
