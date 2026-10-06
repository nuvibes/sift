import { describe, expect, it } from 'vitest';
import { isNewer } from './version-order';

describe('which release comes after which', () => {
	it('reads the three numbers as numbers, with or without a leading v', () => {
		expect(isNewer('0.2.1', '0.2.0')).toBe(true);
		expect(isNewer('v0.2.1', '0.2.0')).toBe(true);
		expect(isNewer('0.2.10', '0.2.9')).toBe(true);
		expect(isNewer('0.2.0', '0.2.1')).toBe(false);
		expect(isNewer('0.2.1', '0.2.1')).toBe(false);
	});

	it('sorts a pre-release below the release it leads to', () => {
		expect(isNewer('0.3.0', '0.3.0-rc.1')).toBe(true);
		expect(isNewer('0.3.0-rc.1', '0.3.0')).toBe(false);
		expect(isNewer('0.3.0-rc.2', '0.3.0-rc.1')).toBe(true);
	});

	it('claims nothing from a version it cannot read', () => {
		expect(isNewer('newest', '0.2.0')).toBe(false);
		expect(isNewer('0.2.1', '')).toBe(false);
	});
});
