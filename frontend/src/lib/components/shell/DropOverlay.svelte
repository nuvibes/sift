<script lang="ts">
	/* NOT ON THE GALLERY: it draws nothing until something is dragged over the WINDOW, which it listens for
	   globally. A second one would answer the same drag and draw two overlays on top of each other,
	   and there is nothing to see with no drag in progress. */

	// Drop a file anywhere in the window. Idle, this renders nothing; it appears only during a drag.
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

	/*
	 * Two facts, because they are two questions.
	 *
	 * `depth` is only "is a drag inside this window", counted from every enter against every leave,
	 * unconditionally. The browser fires the enter before the leave, so counting only enters this
	 * overlay would take while counting every leave nets out wrong: crossing from an entity card
	 * back onto the page would go +1 then -1 to zero and the offer would flash and vanish.
	 *
	 * `offering` is "would we take what is being carried, here", answered from the latest event.
	 */
	let depth = $state(0);
	let offering = $state(false);
	const active = $derived(depth > 0 && offering);

	/*
	 * Whether the drag in flight started in here is asked of `drag-origin`, which owns that
	 * question for the whole application (a link dropped on an entity card needs the same answer),
	 * with the reasoning written there: a browser fills any drag begun on a link with
	 * `text/uri-list` of its own accord.
	 */

	/* Both ends of the drag, and `depth` is reset here rather than only counted down.
	 *
	 * `dragend` fires on the source wherever the drag finished, including outside the window,
	 * where the matching `dragleave` never arrives. Without this the counter would stay above zero
	 * and the overlay up over the whole app, with no drag in progress and no way to dismiss it.
	 */
	function ended() {
		depth = 0;
		offering = false;
	}

	// What a drag is carrying that this can actually take in: files, or a link. A link dragged from
	// another tab (or from Discord) arrives as text/uri-list (sometimes only as text/plain) and
	// the drop handler downstream already knows what to do with it. Arming only for 'Files' would leave a
	// dropped link falling through to the browser, which navigates away to it; this is what stops
	// that.
	/* Whether the pointer is over something that takes its own drops.
	 *
	 * The window is the biggest target there is, which is the point of this overlay, and it is
	 * also why it could swallow a file meant for a box on the page: a WireGuard configuration
	 * dropped on the tunnel importer would bubble up to here and be imported as MEDIA, so dropping
	 * a config file would start a download of it.
	 *
	 * Answered by asking where the drop landed rather than by having each zone stop the event: a
	 * stopped event never reaches the reset below, and the overlay would then sit over the whole
	 * application with no drag in progress. This way the counting stays in one place and only the
	 * taking is handed over.
	 */
	/** Whether the screen on show is going to take this link itself. Files are never a page's. */
	function aimedAtAPage(event: DragEvent): boolean {
		if (pageAim() === null) return false;
		return !(event.dataTransfer?.types.includes('Files') ?? false);
	}

	function draggingSomethingWeTake(event: DragEvent): boolean {
		if (dragBeganInside()) return false;
		/* A screen that is ABOUT one thing takes a dropped link itself and files it under that
		 * thing: somebody's own page, a tag's, a collection's. The window has to stand down or one
		 * gesture starts two downloads, one filed and one filed nowhere.
		 *
		 * A FILE is still the window's: a page has nothing to do with importing one, and that is
		 * the same split every entity card makes. See `aimed-page`, which owns the question. */
		if (aimedAtAPage(event)) return false;
		/* A file this application is currently handing TO Windows.
		 *
		 * It arrives back here as an ordinary `Files` drag while the pointer is still over the
		 * window, which is indistinguishable from somebody bringing a file in, so dragging a clip
		 * out towards Discord would raise a full-window offer to import the file being sent.
		 *
		 * Asked as a separate question from where the drag began, because the two are answered by
		 * different things: that one is a browser drag that began here and ends with `dragend`, and
		 * this is an
		 * operating-system drag that has no `dragend` at all. See `draggingOut`.
		 */
		if (draggingOut()) return false;
		if (overADropZone(event)) return false;
		const types = event.dataTransfer?.types;
		if (!types) return false;
		// The app's own type, checked as well as the origin above. It is not redundant: it holds even
		// if a `dragstart` never reaches the window: a handler between here and there that stops
		// propagation would take the first check out without saying so. Two independent reasons to
		// refuse, and an import needs both to be wrong before it can offer itself.
		if (types.includes(ASSIGN_TYPE)) return false;
		return (
			types.includes('Files') || types.includes('text/uri-list') || types.includes('text/plain')
		);
	}

	// dragenter and dragleave fire for every element the pointer crosses, not just the window, so a
	// naive "enter shows, leave hides" flickers the overlay off the moment the pointer moves over
	// anything inside it. Counting enters against leaves is what makes it steady.
	function enter(event: DragEvent) {
		depth += 1;
		offering = draggingSomethingWeTake(event);
	}

	function leave() {
		depth = Math.max(0, depth - 1);
	}

	function over(event: DragEvent) {
		/* Re-asked on every move, which is what keeps the answer right when the pointer stops.
		   `dragover` fires continuously over whatever is under it, so this settles the offer over a
		   card the moment the pointer arrives rather than only on the crossing. */
		offering = draggingSomethingWeTake(event);
		// Without this the browser navigates to the file instead, which loses the page.
		if (active) {
			event.preventDefault();
			return;
		}
		/*
		 * A file this window is handing out, still passing over the window on its way.
		 *
		 * Taken as well, for the cursor's sake: a page that accepts no drop reports no effect to
		 * the operating system, which draws a no-entry sign, so beginning a drag towards a chat
		 * window would paint "you cannot drop that here" across the library the file is leaving,
		 * reading as a failed drag.
		 *
		 * Accepting the drag is not accepting the drop: `drop` below still refuses to import it, so
		 * letting go over Sift does nothing, the right answer for somebody who started a drag out
		 * and changed their mind.
		 *
		 * Once the pointer leaves this window the cursor is not Sift's business: the window
		 * underneath answers.
		 */
		if (draggingOut()) event.preventDefault();
	}

	function drop(event: DragEvent) {
		// Read before resetting: `active` is derived from the counter that `ended` clears, so asking
		// afterwards would answer "nothing was being dropped" for every drop there was.
		const offering =
			active &&
			!dragBeganInside() &&
			!draggingOut() &&
			!overADropZone(event) &&
			!aimedAtAPage(event);
		// Always, even for a drop that belongs to something else: this is the one place the counter
		// is cleared, so leaving early before it would strand the overlay open.
		ended();
		if (!offering) return;
		// Prevent the browser opening the dropped file even when nothing here will import it: a file
		// that replaces the page is a worse surprise than one that quietly does nothing.
		event.preventDefault();
		const data = event.dataTransfer;
		if (!data) return;
		// Deciding what to render, not a permission: the server refuses a non-admin regardless. A
		// quiet note here is kinder than a drop that appears to work and comes back refused.
		if (!session.isAdmin) {
			toasts.show(ADMINS_ONLY);
			return;
		}
		void capture.handleDrop(data);
	}

	/*
	 * The CAPTURE phase, and that is a matter of correctness rather than a preference.
	 *
	 * A target that takes a drop for itself calls `stopPropagation` on it (an entity card does,
	 * the rail's Downloads row does) and a bubbling listener on the window is precisely what that
	 * stops. So in the bubbling phase the one place the counter is cleared would not run for
	 * exactly the drops that happen over a target, and with the counting symmetric that would
	 * leave this overlay up over the whole application with nothing in flight and no way to dismiss
	 * it. Capture runs on the way DOWN, before any of them, and nothing on the page can prevent it.
	 *
	 * `dragstart` is still not here: where a drag BEGAN is `drag-origin`'s question and it listens
	 * for itself, so this file cannot answer it differently.
	 */
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

<!-- The drawing every whole-window offer shares; a guest is told immediately that adding is not theirs. -->
<DropOffer shown={active} words={session.isAdmin ? 'Drop to add' : ADMINS_ONLY} />
