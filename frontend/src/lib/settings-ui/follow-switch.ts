/* Following Sift onto another library: the server answers the request that arranged the switch,
 * stops, and is started again on the library named. */
import { serverBootId } from '$lib/shell/health';

/** How long a switch may take before the page says it has not finished. */
const SWITCH_LIMIT_MS = 90_000;

/** How often the page asks whether the new run has answered. */
const SWITCH_POLL_MS = 500;

/* Every wait belongs to the page that started it. */
let generation = 0;

/** Abandon every wait started before now: they end quietly and never load a page. */
export function abandonSwitches(): void {
	generation += 1;
}

/** Wait for a run other than `before` to answer, then load its first page. */
export async function followSwitch(
	before: string | null,
	{
		limitMs = SWITCH_LIMIT_MS,
		pollMs = SWITCH_POLL_MS,
		arrive = () => location.assign('/')
	}: { limitMs?: number; pollMs?: number; arrive?: () => void } = {}
): Promise<boolean> {
	const mine = generation;
	const deadline = Date.now() + limitMs;
	while (Date.now() < deadline) {
		await new Promise((settle) => setTimeout(settle, pollMs));
		if (mine !== generation) return false;
		const now = await serverBootId();
		if (mine !== generation) return false;
		if (now !== null && (before === null || now !== before)) {
			arrive();
			return true;
		}
	}
	return false;
}
