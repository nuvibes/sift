<script lang="ts">
	/* NOT ON THE GALLERY: a singleton over the whole window, opened from the shared search state. A second
	   one would open with the real one and cover it. */

	/* The search bar, bigger, over a blurred page: the same `SearchBox`, in the media view's frame.
	 * Ctrl-F is taken because the search has no other key route; F3 still finds in the page. */
	import { tick, untrack } from 'svelte';
	import { afterNavigate } from '$app/navigation';
	import SearchBox from '$lib/components/shell/SearchBox.svelte';
	import { arrive } from '$lib/shell/motion.svelte';
	import Veil from '$lib/components/common/Veil.svelte';
	import { keystrokeIsUnanswered } from '$lib/shell/layers';
	import { searchBox } from '$lib/search/search.svelte';
	import { matches } from '$lib/shell/shortcuts';

	let open = $state(false);

	// The top bar's box drops its keyboard hint while this is up.
	$effect(() => {
		searchBox.sheetOpen = open;
	});

	// Opened by something other than the key; it reads only the counter, so it cannot re-trigger.
	let answered = 0;
	$effect(() => {
		const wanted = searchBox.sheetWanted;
		if (wanted === answered) return;
		answered = wanted;
		untrack(() => void show());
	});
	let sheet = $state<HTMLElement | null>(null);

	async function show() {
		open = true;
		// After the sheet exists, or there is nothing to focus.
		await tick();
		sheet?.querySelector('input')?.focus();
	}

	// Shut on a navigation that goes somewhere (a picked suggestion never submits), not on a
	// reorder: the search-by-meaning control navigates too.
	afterNavigate(({ from, to }) => {
		// A change of how to search carries the box into the address; closing would lose the caret.
		if (searchBox.switching) {
			searchBox.switching = false;
			return;
		}
		if (from && to && from.url.pathname === to.url.pathname) {
			const asked = (url: URL) => url.searchParams.get('q') ?? '';
			if (asked(from.url) === asked(to.url)) return;
		}
		open = false;
	});

	// Capture: the box keeps Escape for its always-open list, so one press closes the sheet.
	function onEscape(event: KeyboardEvent) {
		if (event.key !== 'Escape' || !open) return;
		event.stopPropagation();
		open = false;
	}

	// Not when a layer on top (the Settings search) already answered Ctrl-F; see `layers`.
	function onKeydown(event: KeyboardEvent) {
		if (!keystrokeIsUnanswered(event)) return;
		if (!matches(event, 'app.search')) return;
		event.preventDefault();
		void show();
	}
</script>

<svelte:window onkeydowncapture={onEscape} onkeydown={onKeydown} />

{#if open}
	<!-- A button, so clicking away closes and it is announced as dismissable. -->
	<Veil label="Close search" layer="asset" onclose={() => (open = false)} />

	<div
		class="overlay"
		role="dialog"
		aria-modal="true"
		aria-label="Search"
		bind:this={sheet}
		transition:arrive={{ y: -8, pace: 'base', scale: 0.98 }}
	>
		<div class="box">
			<SearchBox exclusive />
		</div>
	</div>
{/if}

<style>
	/* Not `.sheet` (the centred 420px dialog). Absolute inside `.content`, so it centres on the
	   column rather than on a viewport that includes the rail. */
	.overlay {
		/* Every grid track: an absolute child of a grid is placed against its own grid area. */
		grid-area: 1 / 1 / -1 / -1;
		position: absolute;
		z-index: var(--z-asset-sheet);
		/* A fixed distance below the top bar; a share of the height moves it between screens. */
		inset-block-start: calc(var(--topbar-height) + var(--space-16));
		inset-inline: 0;
		display: flex;
		justify-content: center;
		padding-inline: var(--space-6);
		pointer-events: none;
	}

	/* Wider than the top bar's box: here somebody builds a query and reads suggestions. */
	.box {
		inline-size: min(840px, 100%);
		pointer-events: auto;
		font-size: 1.125rem;
	}

	/* At a phone's width this is the search itself (see `TopBar`), so it sits at the top. */
	@media (max-width: 767px) {
		.overlay {
			inset-block-start: var(--space-2);
			padding-inline: var(--space-4);
		}
	}
</style>
