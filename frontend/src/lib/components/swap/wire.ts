// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Manifest's shapes, by the names the swap screens use, all from the generated schema. */

import type { components } from '$lib/api/schema';

type Schemas = components['schemas'];

export type ManifestGroup = Schemas['ManifestGroup'];
export type ManifestFile = Schemas['ManifestFile'];
export type GroupFiles = Schemas['GroupFiles'];
export type Crossing = Schemas['CrossingNow'];
export type RefusedFile = Schemas['RefusedFile'];
export type Learns = Schemas['Learns'];
export type TokenRead = Schemas['TokenRead'];
export type GroupKind = ManifestGroup['kind'];
export type GroupState = ManifestGroup['state'];

export type Session = Schemas['SwapSession'];
export type Weight = Schemas['SwapWeight'];
export type Tunnel = Schemas['SwapTunnel'];
