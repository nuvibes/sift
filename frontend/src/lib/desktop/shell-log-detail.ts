/*
 * The desktop shell's own log, told the library's Detail setting (`logs.detail`) and its `Hide
 * personal details in the log` (`logs.hide_personal`) from the start.
 *
 * The shell learns them only when somebody tells it, and a run in which nobody opened the Logs page
 * would leave the shell as it was started. So the page tells it once an admin is signed in, and
 * again whenever either is saved here or moved somewhere else (`onSettingsSaved` carries both). Once
 * per pair of answers. In a browser the bridge answers null, and the values read are the page's one
 * cached copy, so the browser asks nothing it was not already asking.
 */
import { bridge } from '$lib/bridge';
import { fetchSettingValues, onSettingsSaved } from '$lib/settings-ui/settings';

const DETAIL = 'logs.detail';
const HIDE = 'logs.hide_personal';

let detail: unknown;
let hide: unknown;
let told: string | undefined;

/** Tell the shell the two answers where either moved; undefined keeps an answer. */
export function tellShell(nextDetail: unknown, nextHide: unknown): void {
	if (nextDetail !== undefined) detail = nextDetail;
	if (nextHide !== undefined) hide = nextHide;
	if (detail === undefined) return;
	const detailed = detail === 'detailed';
	const hidden = hide === true;
	const pair = `${detailed}/${hidden}`;
	if (pair === told) return;
	told = pair;
	void bridge.shellLogDetail(detailed, hidden);
}

/** Read the settings and tell the shell. Called by the root layout once an admin is signed in. */
export async function tellShellLogDetail(): Promise<void> {
	try {
		const values = await fetchSettingValues();
		tellShell(values.get(DETAIL), values.get(HIDE));
	} catch {
		// Unreachable for a moment: the Logs page and the watcher below tell it later.
	}
}

onSettingsSaved((saved) => {
	if (DETAIL in saved || HIDE in saved) tellShell(saved[DETAIL], saved[HIDE]);
});

/** For the tests: forget what the shell was told, as a new page does. */
export function forgetToldShellLogDetail(): void {
	told = undefined;
	detail = undefined;
	hide = undefined;
}
