#!/usr/bin/env python3
"""
Source of truth for nautilus-git-status emblem artwork.

Each emblem layers two signals on one 64x64 canvas:
  * outer disk -> repo status   (dirty / behind / ahead / clean)
  * inner dot  -> ownership tier (primary / secondary / tertiary)

The tier dot is small, black-outlined, and slightly offset down-right so
the surrounding status color reads as a ring around it. The 'external'
tier has no inner dot at all — the plain status disk signals "no
ownership to declare", matching the pre-ownership look.

There are two outline styles for the inner dot:
  * normal  — soft near-black outline. Used when the repo has a remote
              (any remote, not just origin).
  * noremote — bolder crimson outline. Flags repos that exist purely
              locally with no remote configured, so the tier dot reads
              as "owned but not pushed anywhere".

Run this script to (re)write the emblem SVGs into this directory.
"""

import os

STATUS_COLORS = {
    'dirty':  '#e8a23a',
    'behind': '#d04a3a',
    'ahead':  '#3aa856',
    'clean':  '#ffffff',
}

# Inner-dot color per ownership tier. Black outline does the heavy lifting
# for separation, so colors are picked for hue distinctness more than for
# contrast against the status color underneath.
TIER_DOT_COLORS = {
    'primary':   '#f5c419',  # gold
    'secondary': '#19c0e0',  # cyan
    'tertiary':  '#a855f7',  # purple
    # 'external' is rendered without an inner dot.
}

# Outline shared by status disk and the normal tier dot — almost-black,
# slightly softened so it doesn't read as harsh at small render sizes.
STROKE = '#1a1a1a'

# Bolder crimson outline used when a tiered repo has no remote.
# Picked to stay visibly red on every status disk including the muted
# brick-red 'behind' disk (#d04a3a), without leaning orange enough to
# blend with the 'dirty' disk (#e8a23a).
NOREMOTE_STROKE = '#e11d48'

# Inner tier dot is nudged ~2px down-right from the disk center. Just
# enough offset to read as deliberate without breaking the eye's read
# of "centered inside the status disk".
SVG_WITH_TIER = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <circle cx="32" cy="32" r="10" fill="{dot}" stroke="{stroke}" stroke-width="1.25" stroke-opacity="0.6"/>
  <circle cx="34" cy="34" r="5"  fill="{tier}" stroke="{stroke}" stroke-width="1.2"  stroke-opacity="0.85"/>
</svg>
'''

SVG_WITH_TIER_NOREMOTE = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <circle cx="32" cy="32" r="10" fill="{dot}" stroke="{stroke}" stroke-width="1.25" stroke-opacity="0.6"/>
  <circle cx="34" cy="34" r="5"  fill="{tier}" stroke="{noremote}" stroke-width="2.0" stroke-opacity="1.0"/>
</svg>
'''

SVG_EXTERNAL = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <circle cx="32" cy="32" r="10" fill="{dot}" stroke="{stroke}" stroke-width="1.25" stroke-opacity="0.6"/>
</svg>
'''


def main():
    out_dir = os.path.dirname(os.path.abspath(__file__))
    written = 0
    for status, dot in STATUS_COLORS.items():
        # External: one variant, no inner dot.
        svg = SVG_EXTERNAL.format(dot=dot, stroke=STROKE)
        name = f'emblem-git-{status}-external.svg'
        with open(os.path.join(out_dir, name), 'w') as fh:
            fh.write(svg)
        print(f'wrote {name}')
        written += 1
        # Tiered: two variants each — normal and -noremote.
        for tier, tier_color in TIER_DOT_COLORS.items():
            svg = SVG_WITH_TIER.format(dot=dot, tier=tier_color, stroke=STROKE)
            name = f'emblem-git-{status}-{tier}.svg'
            with open(os.path.join(out_dir, name), 'w') as fh:
                fh.write(svg)
            print(f'wrote {name}')
            written += 1

            svg = SVG_WITH_TIER_NOREMOTE.format(
                dot=dot, tier=tier_color, stroke=STROKE,
                noremote=NOREMOTE_STROKE,
            )
            name = f'emblem-git-{status}-{tier}-noremote.svg'
            with open(os.path.join(out_dir, name), 'w') as fh:
                fh.write(svg)
            print(f'wrote {name}')
            written += 1
    print(f'wrote {written} emblems total')


if __name__ == '__main__':
    main()
