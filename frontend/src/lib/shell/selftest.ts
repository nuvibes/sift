/* Measuring what this machine can actually do, and reading back what it found.
 *
 * The test works the machine hard for up to 5 minutes, so starting it and reading the result
 * are two requests: the start queues the run and answers straight away, and the result is polled.
 * A second start while one is coming queues nothing, because two runs would measure each other.
 *
 * Nothing here writes a setting. The recommendations name settings and say what they should be;
 * applying goes through the ordinary settings save, so a suggestion gets the same validation as a
 * number somebody typed, and can be undone the same way.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

export type Level = components['schemas']['LevelView'];

type Measurement = components['schemas']['MeasurementView'];

export type Recommendation = components['schemas']['RecommendationView'];

export type SelfTest = components['schemas']['SelfTestView'];

/** How often the result is asked for while a test is running. The test takes tens of seconds, so
 *  this is about the screen feeling alive rather than about catching a moment. */
export const POLL_MS = 1500;

export async function startSelfTest(): Promise<SelfTest> {
	return api.post<SelfTest>('/performance/self-test', { body: {} });
}

export async function fetchSelfTest(): Promise<SelfTest> {
	return api.get<SelfTest>('/performance/self-test');
}
