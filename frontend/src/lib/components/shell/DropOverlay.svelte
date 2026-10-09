<script lang="ts">
	/* NOT ON THE GALLERY: it draws nothing until something is dragged over the WINDOW, which it listens for
	   globally; a second one would answer the same drag. */

	// Drop a file anywhere in the window. It renders nothing until a drag.
	import { capture } from '$lib/capture/capture.svelte';
	import { draggingOut } from '$lib/capture/copy-out';
	import DropOffer from '$lib/components/common/DropOffer.svelte';
	import { ASSIGN_TYPE, overADropZone } from '$lib/components/common/drag-assign.svelte';
	import { dragBeganInside } from '$lib/components/common/drag-origin.svelte';
	import { pageAim } from '$lib/components/entity/aimed-page.svelte';
	import { onMount } from 'svelte';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';

	const ADMINS_ONLY = 'Adding media is available to admins';

	// `depth` counts every enter against every leave (enter fires before leave); `offering` is
	// whether we would take what is carried here.
	let depth = $state(0);
	let offering = $state(false);
	const active = $derived(depth > 0 && offering);

	// Whether the drag began here is `drag-origin`'s question, for the whole app.

	// `dragend` fires even outside the window, where the matching `dragleave` never arrives.
	function ended() {
		depth = 0;
		offering = false;
	}

	/** Whether the screen on show is going to take this link itself. Files are never a page's. */
	function aimedAtAPage(event: DragEvent): boolean {
		if (pageAim() === null) return false;
		return !(event.dataTransfer?.types.includes('Files') ?? false);
	}

	// A link may arrive only as text/plain; unarmed for it, the browser would navigate away.
	function draggingSomethingWeTake(event: DragEvent): boolean {
		if (dragBeganInside()) return false;
		// A page about one thing files a dropped link itself; files stay the window's.
		if (aimedAtAPage(event)) return false;
		// A file Sift is handing to Windows comes back as `Files`, with no `dragend`.
		if (draggingOut()) return false;
		// A zone is asked rather than left to stop the event, so the reset always runs.
		if (overADropZone(event)) return false;
		const types = event.dataTransfer?.types;
		if (!types) return false;
		// The app's own type too, in case a handler stopped the `dragstart` the origin check needs.
		if (types.includes(ASSIGN_TYPE)) return false;
		return (
			types.includes('Files') || types.includes('text/uri-list') || types.includes('text/plain')
		);
	}

	// Enter and leave fire for every element crossed, so they are counted to stay steady.
	function enter(event: DragEvent) {
		depth += 1;
		offering = draggingSomethingWeTake(event);
	}

	function leave() {
		depth = Math.max(0, depth - 1);
	}

	function over(event: DragEvent) {
		// Re-asked on every `dragover`, so the offer settles when the pointer stops.
		offering = draggingSomethingWeTake(event);
		// Without this the browser navigates to the file instead, which loses the page.
		if (active) {
			event.preventDefault();
			return;
		}
		// Accepted (never imported) while dragging out, so the cursor shows no no-entry sign.
		if (draggingOut()) event.preventDefault();
	}

	function drop(event: DragEvent) {
		// Read before `ended` clears the counter `active` derives from.
		const offering =
			active &&
			!dragBeganInside() &&
			!draggingOut() &&
			!overADropZone(event) &&
			!aimedAtAPage(event);
		// Always: this is the one place the counter is cleared.
		ended();
		if (!offering) return;
		// Prevented even when nothing is imported: a page replaced by the file is worse.
		event.preventDefault();
		const data = event.dataTransfer;
		if (!data) return;
		// Not a permission (the server refuses non-admins); a note beats a drop refused later.
		if (!session.isAdmin) {
			toasts.show(ADMINS_ONLY);
			return;
		}
		void capture.handleDrop(data);
	}

	// Capture phase, so the reset runs even when a target stops a drop's propagation.
	onMount(() => {
		window.addEventListener('dragenter', enter, true);
		window.addEventListener('dragleave', leave, true);
		window.addEventListener('dragover', over, true);
		window.addEventListener('drop', drop, true);
		window.addEventListener('dragend', ended, true);
		return () => {
			window.removeEventListener('dragenter', enter, true);
			window.removeEventListener('dragleave', leave, true);
			window.removeEventListener('dragover', over, true);
			window.removeEventListener('drop', drop, true);
			window.removeEventListener('dragend', ended, true);
		};
	});
</script>

<!-- Shared by every whole-window offer; a guest is told immediately that adding is not theirs. -->
<DropOffer shown={active} words={session.isAdmin ? 'Drop to add' : ADMINS_ONLY} />
