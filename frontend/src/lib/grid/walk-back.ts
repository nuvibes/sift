import type { PageStart } from './grid.svelte';

/** What a page's size is read from: the same files fill the same page only at the same size. */
interface Pages {
	items: readonly { id: string }[];
	offset: number;
	nextOffset: number;
	containerWidth: number;
	rowHeight: number;
	rowsPerScreen: number;
}

/** The pages this visit turned forward from, so Previous shows the page that was left. */
export class WalkBack {
	#left: { from: string; near: number }[] = [];
	#size = '';

	/** Where Next goes, remembering the page it leaves. */
	next(pages: Pages): PageStart {
		const first = pages.items[0]?.id;
		if (first === undefined || this.#sizeOf(pages) !== this.#size) this.forget();
		this.#size = this.#sizeOf(pages);
		if (first !== undefined) this.#left.push({ from: first, near: pages.offset });
		return { at: pages.nextOffset };
	}

	/** The page Next left, by its first file; filled backwards when this visit never saw it. */
	previous(pages: Pages, anchored: boolean): PageStart {
		const page = this.#sizeOf(pages) === this.#size ? this.#left.pop() : undefined;
		if (page === undefined) return { endingAt: pages.offset };
		return anchored ? page : { at: page.near };
	}

	forget(): void {
		this.#left = [];
	}

	#sizeOf(pages: Pages): string {
		return `${pages.containerWidth}x${pages.rowHeight}x${pages.rowsPerScreen}`;
	}
}
