<script lang="ts">
	/* An entity's whole page taking a dropped link and filing it under it: a fixed layer drawn only
	 * while something is held over the window (see `aimed-page`), showing DropOffer. */
	import { onMount } from 'svelte';
	import DropOffer from '$lib/components/common/DropOffer.svelte';
	import { carriesALink, overADropZone, readLink } from '$lib/components/common/drag-assign.svelte';
	import { dropOffer, fetchOnto, type AimedAt } from '$lib/library/aimed-drop.svelte';
	import { aimPageAt } from '$lib/components/entity/aimed-page.svelte';
	import { session } from '$lib/shell/session.svelte';

	interface Props {
		/** What kind of thing this page is about, in the server's own word for it. */
		kind: AimedAt;
		/** The row a fetched file is filed under. */
		id: string;
		/** What to call it on screen. A drop on the wrong page must not look like the right one. */
		name: string;
	}

	let { kind, id, name }: Props = $props();

	/* Say what this screen is about while it is up; a guest's drop is left to the window. */
	$effect(() => {
		if (!session.isAdmin) return;
		aimPageAt({ kind, id, name });
		return () => aimPageAt(null);
	});

	/*
	 * Every enter counted against every leave, and `taking` re-asked, so the offer does not flash.
	 */
	let depth = $state(0);
	let taking = $state(false);
	const over = $derived(depth > 0 && taking);

	/* A card inside the page is a smaller aim and wins, or one drop would file the link twice. */
	function wouldTakeIt(event: DragEvent): boolean {
		return session.isAdmin && carriesALink(event) && !overADropZone(event);
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

	/* Reset on dragend too: a drag ending outside fires no matching dragleave. */
	function ended() {
		depth = 0;
		taking = false;
	}

	function dropped(event: DragEvent) {
		const mine = over && wouldTakeIt(event);
		// Always: this is the one place the counter clears.
		ended();
		if (!mine) return;
		const url = readLink(event);
		if (!url) return;
		event.preventDefault();
		void fetchOnto(url, kind, id, name);
	}

	/* On the window in the capture phase, since a card stops a link's drop from bubbling. */
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

<!-- The sentence is `dropOffer`'s; the drawing every whole-window offer shares. -->
<DropOffer shown={over} words={dropOffer(kind, name)} />
