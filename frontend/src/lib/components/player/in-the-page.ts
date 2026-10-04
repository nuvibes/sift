// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The page's box, for a bar fixed to the window to centre in (the Audio player).
 *
 * The box beside the rail (`.content` in `routes/+layout.svelte`, the bar's sibling's) moves and
 * narrows when the rail is collapsed or the window is resized, and either changes its width, so one
 * ResizeObserver hears both. Its place and width are written on the bar as `--frame-x` and
 * `--frame-w`, which the bar's stylesheet centres on; outside the shell nothing is written and the
 * stylesheet falls back to the window.
 */
export function inThePage(bar: HTMLElement): () => void {
	const content = bar.closest('.shell')?.querySelector<HTMLElement>(':scope > .content');
	if (!content) return () => {};
	const measure = () => {
		const box = content.getBoundingClientRect();
		bar.style.setProperty('--frame-x', `${box.left}px`);
		bar.style.setProperty('--frame-w', `${box.width}px`);
	};
	measure();
	const watcher = new ResizeObserver(measure);
	watcher.observe(content);
	return () => {
		watcher.disconnect();
		bar.style.removeProperty('--frame-x');
		bar.style.removeProperty('--frame-w');
	};
}
