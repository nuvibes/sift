<script lang="ts">
	/*
	 * The whole of an entity's PAGE, taking a dropped link and filing what comes back under it.
	 *
	 * Drawn as a sibling rather than as a wrapper, and it lays out nothing: what it renders is one
	 * fixed layer, and only while something is actually being held over the window. Why it is not a
	 * wrapper, and why the answer it publishes is a named question rather than a hook into somebody
	 * else's handler, is written in full in `aimed-page`.
	 *
	 * What it draws is `DropOffer`, the same offer the window makes, with this page's name in it.
	 */
	import { onMount } from 'svelte';
	import DropOffer from '$lib/components/common/DropOffer.svelte';
	import { carriesALink, overADropZone, readLink } from '$lib/components/common/drag-assign.svelte';
	import { dropOffer, fetchOnto, type AimedAt } from '$lib/library/aimed-drop.svelte';
	import { aimPageAt } from '$lib/components/entity/aimed-page.svelte';

	interface Props {
		/** What kind of thing this page is about, in the server's own word for it. */
		kind: AimedAt;
		/** The row a fetched file is filed under. */
		id: string;
		/** What to call it on screen. A drop on the wrong page must not look like the right one. */
		name: string;
	}

	let { kind, id, name }: Props = $props();

	/* Say what this screen is about, for as long as it is on screen. Cleared on the way out, which
	   is what stops the next screen's drop being aimed at whoever was on this one. */
	$effect(() => {
		aimPageAt({ kind, id, name });
		return () => aimPageAt(null);
	});

	/* Counted rather than flagged, and split into the same two questions the window-wide offer
	   splits into, for the same reason, written up there in full. `depth` counts EVERY enter
	   against EVERY leave, so the pair cancels; `taking` is whether this page would take what is
	   being carried, re-asked from the latest event. Counting only the enters it liked would make
	   the offer flash. */
	let depth = $state(0);
	let taking = $state(false);
	const over = $derived(depth > 0 && taking);

	/* A card inside this page is a SMALLER aim at a NAMED thing, and it wins.
	 *
	 * A person's page carries walls of collections, tags and photo sets, and every one of those
	 * cards takes a link of its own. Without this the page's wash would sit over the card that was
	 * lit up underneath it, and on a drop both of them would file the same link: one under the
	 * page, one under the card. The question is `overADropZone`'s, and the window's offer stands
	 * down over this page for exactly the same reason. */
	function wouldTakeIt(event: DragEvent): boolean {
		return carriesALink(event) && !overADropZone(event);
	}

	function enter(event: DragEvent) {
		depth += 1;
		taking = wouldTakeIt(event);
	}

	function leave() {
		depth = Math.max(0, depth - 1);
	}

	function over_(event: DragEvent) {
		taking = wouldTakeIt(event);
		if (!over) return;
		// Without this the browser navigates to the link instead, which loses the page.
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'link';
	}

	/* `dragend` as well as `drop`, and the counter is RESET rather than counted down: a drag that
	   finishes outside the window fires no matching `dragleave`, and the offer would sit over the
	   page with nothing in flight and no way to dismiss it. The same guard the window's own offer
	   carries, for the same reason. */
	function ended() {
		depth = 0;
		taking = false;
	}

	function dropped(event: DragEvent) {
		const mine = over && wouldTakeIt(event);
		// Always, even for a drop that belongs to something else: this is the one place the counter
		// is cleared, so leaving early before it would strand the offer open.
		ended();
		if (!mine) return;
		const url = readLink(event);
		if (!url) return;
		event.preventDefault();
		void fetchOnto(url, kind, id, name);
	}

	/* A late mount is still a mount. The listeners go on the window because a page is not a box
	   (see `aimed-page`) and they come off with the component.
	 *
	 * In the CAPTURE phase, and it matters: a card inside this page calls `stopPropagation` on a
	 * link it takes, which is exactly what stops a bubbling listener on the window. A bubbling
	 * `drop` that clears this counter would therefore never run for a drop onto a card, and the
	 * page's wash would sit there afterwards with nothing in flight. Capture runs on the way
	 * down and nothing on the page can prevent it. See `DropOverlay`, which says the same. */
	onMount(() => {
		window.addEventListener('dragenter', enter, true);
		window.addEventListener('dragleave', leave, true);
		window.addEventListener('dragover', over_, true);
		window.addEventListener('drop', dropped, true);
		window.addEventListener('dragend', ended, true);
		return () => {
			window.removeEventListener('dragenter', enter, true);
			window.removeEventListener('dragleave', leave, true);
			window.removeEventListener('dragover', over_, true);
			window.removeEventListener('drop', dropped, true);
			window.removeEventListener('dragend', ended, true);
		};
	});
</script>

<!-- The sentence is `dropOffer`'s, shared with the card's own smaller one; the drawing is the one
     every whole-window offer shares. -->
<DropOffer shown={over} words={dropOffer(kind, name)} />
