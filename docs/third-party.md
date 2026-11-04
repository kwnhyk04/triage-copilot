# Third-party work

Designs and code that Triage Copilot's web app is built from. None is installed as a package: each was ported into TC's own CSS (`apps/web/src/app/globals.css`) and components, with TC's colour tokens, its own icons and no Tailwind, shadcn, Radix, TanStack or framer-motion. Checked on 2026-11-04.

| What TC built from it | Source | Author | Licence |
| --- | --- | --- | --- |
| The header's navbar, from the tabs in the component's demo (`TabsDemo`): 16 px items with 20 px sides in 40 px boxes, a 2 px underline 8 px below the list shown, and on hover or keyboard focus a 6 px-cornered tint and a second underline that slide between items (`components/header.tsx`, `components/client.tsx`). The demo slides them with framer-motion's `layoutId`. TC matches that with CSS transitions and a small script, so framer-motion (MIT) is not installed. The dropdown menus were left out. | [Dorpdown Navigation](https://21st.dev/@ln-dev7/components/dorpdown-navigation) on 21st.dev, its demo's tabs | LN (@ln-dev7) | MIT |
| The investigations table's look and the pager: 44 px muted header, 12 px cells, row hover, 40 px outlined and ghost page buttons, the "Page N of M" line and the per-page select (`components/investigation-list.tsx`, `components/pager.tsx`). Row selection, column sorting, Radix Select and TanStack Table were left out. | [Numeric pagination made with TanStack Table](https://21st.dev/@originui/components/table/numeric-pagination-made-with-tan-stack-table) on 21st.dev, built on shadcn/ui's table, button, pagination and select | Origin UI (now coss.com); shadcn | MIT |
| The report's cards: 16 px corners, a hairline, a glow from the top left, a shadow and a lift on hover, a round stroke icon, an uppercase title and a tag (`components/report.tsx`). The animated icons, entrance animations, page backdrop and bento grid were left out. | [Bento Monochrome](https://21st.dev/@larsen66/components/bento-monochrome) on 21st.dev | larsen66 | Listed as "License: unknown" on 21st.dev (2026-11-04). No code was copied, only the look; ask the author or drop the look before relying on it. |

| The landing's hero: the large headline, one button, and at the foot a wide disc whose rim glows like a horizon (`components/hero.tsx`). Its colours come from TC's accent and canvas in both themes. The eyebrow pill, the faint grid behind the headline (removed 2026-11-04 at the user's request), the gradient on the headline text, the bottom fade layer and the Button and lucide-react dependencies were left out. | `hero-1.tsx` (demo "Build smarter tools for modern teams") on 21st.dev | Not confirmed: several 21st.dev components are named "hero 1" and none found on 2026-11-04 showed this code. Add the author and link when known. | MIT, as given with the component |
| The framed product preview under the hero's headline: an 8 px tinted frame round a card with a lighter top edge and a deep shadow, from its `MockupFrame` and `Mockup`. TC frames a saved report there instead of a screenshot. Its headline, badge, buttons, orange glow, dashboard image, next-themes and the `class-variance-authority` dependency were left out. | [Hero Section](https://21st.dev/@mikolajdobrucki/components/hero-section) on 21st.dev, from Launch UI, with its `mockup` component | Mikolaj Dobrucki | MIT |

## MIT licence notices

Copyright (c) LN (@ln-dev7), published on 21st.dev under the MIT licence.

Copyright (c) 2025 coss.com. Originally Copyright (c) 2025 Origin UI.

Copyright (c) 2023 shadcn.

Copyright (c) Mikolaj Dobrucki (Launch UI), published on 21st.dev under the MIT licence.

Copyright (c) the author of `hero-1` on 21st.dev (not yet confirmed), published under the MIT licence.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
