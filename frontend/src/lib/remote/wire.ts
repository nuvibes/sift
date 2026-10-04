/*
 * The remote's wire shapes, as the server declares them. Aliases of the generated schema, so a
 * change on the server reaches every reader here the moment the declarations are rebuilt; nothing
 * is written out a second time (the one-server-type gate's rule).
 */
import type { components } from '$lib/api/schema';

export type RemoteCommand = components['schemas']['RemoteCommand'];
export type ScreenReport = components['schemas']['ScreenReport'];
export type ScreenOut = components['schemas']['ScreenOut'];
export type RemoteScreens = components['schemas']['RemoteScreens'];
export type CommandSent = components['schemas']['CommandSent'];
