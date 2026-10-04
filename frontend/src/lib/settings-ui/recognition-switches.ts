// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The three recognition switches, which Importing's Identify page draws (`RecognitionSection`) and
 * each feature's own pane links to ("Change in Importing", `SwitchPointer`).
 *
 * A module of its own rather than an export of the component, so the page that holds the block can
 * claim the keys for its sub-page (a link naming one opens that page and lands on the switch) while
 * a test that stands the component in with a mock still has the list.
 */
import { FACES_ENABLED_KEY } from '$lib/people/faces.svelte';
import { SEMANTIC_ENABLED_KEY } from '$lib/search/semantic.svelte';
import { WATERMARKS_ENABLED_KEY } from '$lib/library/watermarks.svelte';

export const RECOGNITION_SWITCHES = [
	FACES_ENABLED_KEY,
	SEMANTIC_ENABLED_KEY,
	WATERMARKS_ENABLED_KEY
] as const;
