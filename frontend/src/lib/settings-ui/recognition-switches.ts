// SPDX-License-Identifier: AGPL-3.0-or-later
/* The three recognition switches, which Importing's Identify page draws (`RecognitionSection`)
 * and each feature's own pane links to ("Change in Importing", `SwitchPointer`). */
import { FACES_ENABLED_KEY } from '$lib/people/faces.svelte';
import { SEMANTIC_ENABLED_KEY } from '$lib/search/semantic.svelte';
import { WATERMARKS_ENABLED_KEY } from '$lib/library/watermarks.svelte';

export const RECOGNITION_SWITCHES = [
	FACES_ENABLED_KEY,
	SEMANTIC_ENABLED_KEY,
	WATERMARKS_ENABLED_KEY
] as const;
