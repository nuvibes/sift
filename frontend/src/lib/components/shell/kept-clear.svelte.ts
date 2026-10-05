/* The row a screen keeps clear of the bar's open panel: its title, where its own actions are. */
export const keptClear = $state<{ row: HTMLElement | null }>({ row: null });

/** Mark this element as the one the panel hangs below while it is on screen. */
export function keepClear(node: HTMLElement): () => void {
	keptClear.row = node;
	return () => {
		if (keptClear.row === node) keptClear.row = null;
	};
}
