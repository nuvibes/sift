/*
 * The picture a screen stands on, said by the thing that knows it to the frame that has the room.
 *
 * An entity page draws its cover, blurred past recognition, behind everything above the tabs: the
 * breadcrumbs, the title row, the band and the tab strip, from the top of the page to the first
 * tile. Those are all the frame's furniture (the trail is `PageFrame`'s band, the tab strip its
 * tools row, the band an item inside its header), so only the frame can draw a layer that size, and
 * the frame is the one thing that cannot know which picture: what it holds is a snippet.
 *
 * Not a `backdrop` prop threaded from the page: the picture is six fields deep (`EntityHeader`'s
 * `picture`: an upload, a face, a chosen moment, the file's own still, the person's art, a Site's
 * mark), and every page would hold a copy of that ladder beside the header that already computes
 * it, threaded through `AssetGrid` and `RelatedWall` as well. One answer, computed where the fields
 * are, said once.
 *
 * A context rather than a store. `screen-bar.svelte.ts` publishes one level up and needs a token
 * per publisher, because the top bar is a singleton and navigation mounts the new screen before
 * unmounting the old one. Here a frame hands this down to its own subtree, so a second frame has
 * its own and the two cannot reach each other: nothing to claim, nothing to release.
 */
import { getContext, setContext } from 'svelte';

const WHAT_THE_SCREEN_STANDS_ON = Symbol('sift:backdrop');

/** What a frame offers whatever it is drawing. */
interface ScreenBackdrop {
	/** Draw this picture behind the furniture, or `null` to draw none at all. */
	stand: (picture: string | null) => void;
}

/** Said by a frame, once, for everything inside it. */
export function ownsTheBackdrop(backdrop: ScreenBackdrop): void {
	setContext(WHAT_THE_SCREEN_STANDS_ON, backdrop);
}

/**
 * Read by whatever knows the picture.
 *
 * The default does nothing, which is what a header drawn outside any frame of ours wants: the
 * gallery draws one on its own, and a component that refused to exist without a frame would make
 * that page impossible for no benefit. Same reasoning as `getStage`.
 */
export function theBackdrop(): ScreenBackdrop {
	return getContext<ScreenBackdrop | undefined>(WHAT_THE_SCREEN_STANDS_ON) ?? { stand: () => {} };
}
