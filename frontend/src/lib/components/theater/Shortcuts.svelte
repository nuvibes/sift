<script lang="ts">
	/*
	 * What the keys do here.
	 *
	 * It exists because of the first row. Every video site binds the number keys to "jump to N
	 * times ten percent", and Theater takes them for "talk to cell N" instead: a wall is not a
	 * player, and a percentage seek means nothing with several files playing at different points. A
	 * silently overridden shortcut is the documented complaint about every product that has done
	 * this, so this sheet is part of the decision.
	 *
	 * A panel on the bar rather than a sheet over the wall: it is read while using the keys it
	 * describes, and a sheet covering the picture would have to be shut before any of it could be
	 * tried. It is taller than a short window, so it stops at the foot of the window and scrolls.
	 *
	 * Generated from the declarations, so the list cannot disagree with the code that reads the
	 * keys (such as the range the number keys take).
	 */
	import KeyRows from '$lib/components/KeyRows.svelte';
	import Panel from '$lib/components/common/Panel.svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import { shortcutsIn } from '$lib/shell/shortcuts';

	/*
	 * Every key that works on this screen: Theater's own, and then everything declared "Anywhere".
	 *
	 * The keys come from two areas of the one declaration rather than a hand-picked list of ids,
	 * because an area is what a declaration already says about where a key applies: "Anywhere" is
	 * handled by the shell on every screen, this one included, and "Theater" by this screen. A key
	 * added to either is on this panel the moment it is declared. The test beside this file holds
	 * the panel to those two areas, and holds every key the screen reads to being among them, so a
	 * key read from a third area fails there rather than going missing here.
	 *
	 * Theater's own first: they are the ones somebody opened this panel to find.
	 */
	const KEYS = [...shortcutsIn('Theater'), ...shortcutsIn('Anywhere')];

	/*
	 * Where the panel starts, so it can stop at the foot of the window.
	 *
	 * It hangs from whichever bar opened it, and that bar is at a different height with the window's
	 * own title strip, on a filled screen and on a phone, so the room below it is measured rather
	 * than assumed. Measured again when the window changes size, which is also what filling the
	 * screen does.
	 */
	let box: HTMLElement | undefined = $state();
	let top = $state(0);

	$effect(() => {
		const panel = box;
		if (!panel) return;
		const measure = () => {
			top = Math.max(0, panel.getBoundingClientRect().top);
		};
		measure();
		window.addEventListener('resize', measure);
		return () => window.removeEventListener('resize', measure);
	});
</script>

<!-- The box is `Panel`'s: the ground, the edge, the corner and the inset are one decision made in
     one place, not an answer of this file's own to all four. What is left below is the only part
     that is about a sheet hanging under the theater's bar, where it sits. -->
<!-- Not called `sheet`: that is the app's dialog, a fixed box centred on the window, and a panel
     wearing the name is taken out of the bar and drawn as one. -->
<div class="key-panel" bind:this={box} style:--key-panel-top="{top}px">
	<Panel inset="md" corner="md" floating>
		<!-- The rows are `KeyRows`, which the Shortcuts section in Settings draws too: two copies of
		     a key sheet are two key sheets that can come to describe different keyboards. -->
		<Scroller>
			<KeyRows shortcuts={KEYS} />
		</Scroller>
	</Panel>
</div>

<style>
	/*
	 * At most the room between the bar and the foot of the window, and the rows scroll inside it.
	 *
	 * One `minmax(0, 1fr)` row at each level down to the scroller, which is what gives the scroller a
	 * height to be shorter than its rows: an automatic row grows to its content and the ceiling would
	 * cut the rows off instead of scrolling them.
	 */
	.key-panel {
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		max-block-size: calc(100dvh - var(--key-panel-top, 0px) - var(--space-4));
		margin-inline: var(--space-4);
		margin-block-end: var(--space-2);
	}

	.key-panel > :global(.panel) {
		grid-template-rows: minmax(0, 1fr);
		min-block-size: 0;
	}
</style>
