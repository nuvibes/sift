// SPDX-License-Identifier: AGPL-3.0-or-later
import { defineCollection } from 'astro:content';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';
import { z } from 'astro/zod';

export const collections = {
	docs: defineCollection({
		loader: docsLoader(),
		schema: docsSchema({
			// The home page's paragraph on what Sift is, drawn in its hero (src/components/Hero.astro).
			extend: z.object({ about: z.string().optional() })
		})
	})
};
