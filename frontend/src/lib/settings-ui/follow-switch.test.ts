// SPDX-License-Identifier: AGPL-3.0-or-later
/* A wait for the new run belongs to the page that started it. */
import { afterEach, expect, it, vi } from 'vitest';

import { abandonSwitches, followSwitch } from './follow-switch';

const serverBootId = vi.hoisted(() => vi.fn());
vi.mock('$lib/shell/health', () => ({ serverBootId }));

afterEach(() => {
	vi.clearAllMocks();
});

it('arrives once the server answers as a different run', async () => {
	serverBootId.mockResolvedValue('run-2');
	const arrive = vi.fn();
	expect(await followSwitch('run-1', { pollMs: 1, limitMs: 200, arrive })).toBe(true);
	expect(arrive).toHaveBeenCalledTimes(1);
});

it('an abandoned wait ends quietly and never arrives', async () => {
	serverBootId.mockResolvedValue('run-2');
	const arrive = vi.fn();
	const waiting = followSwitch('run-1', { pollMs: 20, limitMs: 500, arrive });
	abandonSwitches();
	expect(await waiting).toBe(false);
	expect(arrive).not.toHaveBeenCalled();
});

it('a wait that runs out answers false without arriving', async () => {
	serverBootId.mockResolvedValue('run-1');
	const arrive = vi.fn();
	expect(await followSwitch('run-1', { pollMs: 1, limitMs: 20, arrive })).toBe(false);
	expect(arrive).not.toHaveBeenCalled();
});
