import { redirect } from '@sveltejs/kit';

import { resolveAddress, settingsPath } from '$lib/settings-ui/sections';

/* The decision record's old address: History, showing the decisions alone. */
export function load(): void {
	redirect(308, settingsPath(resolveAddress('tasks', 'activity.decisions')));
}
