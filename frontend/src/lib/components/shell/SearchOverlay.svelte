<script lang="ts">
	/* NOT ON THE GALLERY: a singleton over the whole window, opened from the shared search state. A second
	   one would open at the same moment as the real one and cover it. */

	/*
	 * The search bar, bigger, over a blurred page.
	 *
	 * Not a second search. It is the SAME component the top bar draws, given room, so the chips,
	 * the grammar, the suggestions and the recent searches are the ones already there, and there is
	 * no second box to keep in step with the first.
	 *
	 * The frame is the one the media view uses: the same veil, the same faint blur, the same
	 * treatment. A screen that dims and softens is how this application says "this, now": adding
	 * a different one for a search would be a second visual language for the same idea.
	 *
	 * ## Catching Ctrl-F
	 *
	 * `Ctrl-F` is the browser's own find, and taking it is a deliberate trade rather than an
	 * oversight. The worst outcome of catching it is somebody who wanted to find a word on the page
	 * and got a search box instead, and on a page that is a grid of pictures, finding a word on it
	 * is rarely the thing they meant. The worst outcome of NOT catching it is that the application's
	 * own search has no keyboard route at all. `F3` and the menu still reach the browser's find.
	 *
	 * It does not fire while somebody is typing. That rule is not this component's invention: the
	 * search field already has its own `Ctrl-A`, and a person typing into a box expects the box's
	 * shortcuts rather than the application's.
	 */
	import { tick, untrack } from 'svelte';
	import { afterNavigate } from '$app/navigation';
	import SearchBox from '$lib/components/shell/SearchBox.svelte';
	import { arrive } from '$lib/shell/motion.svelte';
	import Veil from '$lib/components/common/Veil.svelte';
	import { keystrokeIsUnanswered } from '$lib/shell/layers';
	import { searchBox } from '$lib/search/search.svelte';
	import { matches } from '$lib/shell/shortcuts';

	let open = $state(false);

	/* Told to the box in the top bar, whose keyboard hint has nothing left to advise while this is
	   up. Kept in step here rather than set at each of the three places `open` moves. */
	$effect(() => {
		searchBox.sheetOpen = open;
	});

	/* Opened by something other than the key. Reads the counter and nothing it writes, so it cannot
	   re-trigger itself; `untrack` around the opening keeps it that way if `show` ever grows. */
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
		// After the sheet exists, or there is nothing to put the caret in. The box focuses its own
		// field when it is focused, so this hands over rather than reaching inside it.
		await tick();
		sheet?.querySelector('input')?.focus();
	}

	/* Shut when a search takes you somewhere, and NOT when it only changes how the answer is
	 * ordered.
	 *
	 * On the navigation rather than on the form's submit, because not every way out of that box is
	 * a submit: picking a suggestion navigates directly. Left to the submit alone, choosing a
	 * suggestion would leave the sheet standing over the results it had just fetched.
	 *
	 * The exception is what the search-by-meaning control does. Its state lives in the address, so
	 * pressing it navigates. Were every navigation to shut this sheet, pressing it in here would
	 * close the sheet and light the control in the TOP BAR instead, which is the same control on a
	 * box somebody was not typing into: nothing broken, and reading as everything being broken.
	 *
	 * So the test is what CHANGED rather than that anything did: a different screen, or a different
	 * query, is somebody having gone somewhere. The same screen and the same words in a different
	 * order is somebody still standing here deciding how to ask.
	 */
	afterNavigate(({ from, to }) => {
		/* A change of HOW to search is not a search being run, and it is the one navigation that
		   must not close this. It carries what is in the box into the address to keep it, which
		   looks from here exactly like somebody having pressed Enter; closing on it would shut the
		   sheet under whoever was still typing, and the caret would go with it. */
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

	/*
	 * Escape, taken on the way DOWN.
	 *
	 * The search box inside this sheet keeps Escape for itself while its suggestion list is open,
	 * which, in here, it always is, because the sheet opens with the caret in the field and the list
	 * showing. Heard after the field, the first press would only shut the list and the sheet would
	 * need a second one, which is not what Escape means to anybody standing in front of it.
	 *
	 * Capture runs this before the field's own handler rather than after it, so one press closes the
	 * sheet and the list goes with it. The box keeps its own behaviour everywhere else: this only
	 * fires while the sheet is open, and the sheet is the only thing this component draws.
	 */
	function onEscape(event: KeyboardEvent) {
		if (event.key !== 'Escape' || !open) return;
		event.stopPropagation();
		open = false;
	}

	/*
	 * Ctrl-F, unless something on top of this has already answered it.
	 *
	 * Settings is the case that made this necessary: it is a panel over the whole window with a
	 * search box of its own, and Ctrl-F while it is open means THAT box. Its box listens on the
	 * document and calls `preventDefault`; a document listener is reached before a window one, so by
	 * the time this runs the question has been settled and the sheet must not also open over the
	 * panel. Exactly the rule `layers` was written for. See it for why asking where the focus is
	 * would be the wrong test.
	 */
	function onKeydown(event: KeyboardEvent) {
		if (!keystrokeIsUnanswered(event)) return;
		if (!matches(event, 'app.search')) return;
		event.preventDefault();
		void show();
	}
</script>

<svelte:window onkeydowncapture={onEscape} onkeydown={onKeydown} />

{#if open}
	<!-- A button so that clicking away closes, and so it is announced as something that can be
	     dismissed rather than as decoration. The same arrangement the media view uses. -->
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
	/*
	 * Not `.sheet`: that class is the centred modal dialog in the global stylesheet, carrying
	 * `translate: -50% -50%` and a fixed 420px width, and this is neither centred nor 420 wide.
	 *
	 * Positioned against the content column, not the window: `position: fixed` centres in the
	 * viewport, which includes the rail, so this would land to the left of the field it stands in
	 * for. Absolute, inside `.content`, follows whatever the rail is doing at any width with no
	 * copy of the rail's widths here. The veil stays fixed, because it really does cover the
	 * window.
	 */
	.overlay {
		/*
		 * The whole of the content area, explicitly.
		 *
		 * `.content` is a GRID, and an absolutely positioned child of a grid container is positioned
		 * against its own GRID AREA rather than against the container, so with no area named, this
		 * would be auto-placed into an implicit track and `inset-inline: 0` would mean the width of
		 * whatever that track happened to be, which changes from page to page. The layout one level
		 * up carries the same warning in so many words: it names its areas rather than leaving them
		 * to auto-placement, because a modal that stops being `position: fixed` is handed a row.
		 *
		 * `1 / 1 / -1 / -1` is every track there is, so the containing block is the content box and
		 * the centring is against the column the page actually occupies.
		 */
		grid-area: 1 / 1 / -1 / -1;
		position: absolute;
		z-index: var(--z-asset-sheet);
		/* A top bar and a gap below the top of the screen: a FIXED distance, not a share of the
		   window. A share of the height lands the same panel in a visibly different place on a laptop
		   and on a large monitor. */
		inset-block-start: calc(var(--topbar-height) + var(--space-16));
		inset-inline: 0;
		display: flex;
		justify-content: center;
		padding-inline: var(--space-6);
		pointer-events: none;
	}

	/* Wider than the bar in the top bar, and scaled up with it: the point of this is room: this is
	   the one place in the application where somebody is building a query rather than glancing at
	   one, and the suggestions under it are a list to read. The box itself lays out to whatever
	   width it is given, so this is the only thing that has to change. */
	.box {
		inline-size: min(840px, 100%);
		pointer-events: auto;
		font-size: 1.125rem;
	}

	/*
	 * AT A PHONE'S WIDTH THIS IS THE SEARCH, not a bigger copy of one: the bar there draws a square
	 * that opens it, and no field (see `TopBar`). So the field lands where the bar is, a bar-gap in
	 * from the top and the page's own inset in from each side, the whole width of the screen, with
	 * its suggestions under it. The gap below the bar that places it on a desktop would put it in
	 * the middle of a small screen, away from the square that was pressed.
	 */
	@media (max-width: 767px) {
		.overlay {
			inset-block-start: var(--space-2);
			padding-inline: var(--space-4);
		}
	}
</style>
