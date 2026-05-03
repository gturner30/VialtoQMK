#!/usr/bin/env python3
"""
vil_to_qmk.py
=============
Converts a Vial (.vil) layout file into a QMK keymap.c file for a
split_3x6_3 keyboard (e.g. Corne / CRKBD).

Usage
-----
    python3 vil_to_qmk.py <input.vil> [output.c]

If no output path is given the result is printed to stdout.

VIL layout structure
---------------------
Each layer is stored as 8 rows × 7 columns.

Rows 0-2  : left-half  main keys (cols 0-5) + centre/encoder key (col 6)
Row  3    : left-half  thumb cluster at cols 3-5; cols 0-2 and 6 are -1
Rows 4-6  : right-half main keys (cols 0-5, stored right-to-left) + centre/encoder key (col 6)
Row  7    : right-half thumb cluster at cols 3-5; cols 0-2 and 6 are -1

Layout macro auto-detection
-----------------------------
The script inspects col 6 of rows 0, 1, 4, 5 across all layers.  If any real
key (non -1) is found there the keyboard has 4 centre keys and uses
LAYOUT_split_3x6_3_ex2 (46 keys).  Otherwise it uses LAYOUT_split_3x6_3
(42 keys).

QMK LAYOUT_split_3x6_3 argument order (42 keys)
-------------------------------------------------
Row 1 (12): left[0..5],              right_rev[5..0]
Row 2 (12): left[0..5],              right_rev[5..0]
Row 3 (12): left[0..5],              right_rev[5..0]
Thumbs (6): L_thumb[3..5], R_thumb[5..3]

QMK LAYOUT_split_3x6_3_ex2 argument order (46 keys)
-----------------------------------------------------
Row 1 (14): left[0..5], centre_L, centre_R, right_rev[5..0]
Row 2 (14): left[0..5], centre_L, centre_R, right_rev[5..0]
Row 3 (12): left[0..5],                     right_rev[5..0]
Thumbs (6): L_thumb[3..5], R_thumb[5..3]

Key-code normalisation
-----------------------
Vial stores some codes with the "KC_" prefix using different aliases than
QMK's canonical names.  The KEYCODE_MAP dictionary below translates the
known differences found in this firmware.
"""

import json
import re
import sys
import argparse
import textwrap
from pathlib import Path


# Maps Vial keycode strings -> QMK keycode strings where they differ.
KEYCODE_MAP: dict[str, str] = {
    "KC_ESCAPE":   "KC_ESC",
    "KC_BSPACE":   "KC_BSPC",
    "KC_SCOLON":   "KC_SCLN",
    "KC_QUOTE":    "KC_QUOT",
    "KC_LBRACKET": "KC_LBRC",
    "KC_RBRACKET": "KC_RBRC",
    "KC_BSLASH":   "KC_BSLS",
    "KC_PSCREEN":  "KC_PSCR",
    "KC_SCROLLLOCK": "KC_SCRL",
    "KC_KP_MINUS": "KC_PMNS",
    "KC_KP_PLUS":  "KC_PPLS",
    "KC_KP_ENTER": "KC_PENT",
    "KC_LCTRL":    "KC_LCTL",
    "KC_RCTRL":    "KC_RCTL",
    "KC_LSHIFT":   "KC_LSFT",
    "KC_RSHIFT":   "KC_RSFT",
    "KC_PGDOWN":   "KC_PGDN",
    # Transparent / no-op
    "KC_TRNS":     "KC_TRNS",
    "KC_NO":       "KC_NO",
}

# Modifier-tap / layer-tap wrappers that reference inner keycodes by the
# longer Vial alias – rewrite the *inner* keycode too.
INNER_KEYCODE_MAP: dict[str, str] = {k: v for k, v in KEYCODE_MAP.items()}


def normalise_keycode(code: str, normalise: bool = True) -> str:
    """Translate a single Vial keycode string to its QMK equivalent.

    When *normalise* is False the original Vial alias is preserved (e.g.
    KC_BSPACE stays KC_BSPACE instead of becoming KC_BSPC).

    Conversions applied:
      LTn(KC_X)  -> LT(n, KC_X)  Vial layer-tap shorthand
      C_S(KC_X)  -> RCS(KC_X)    Vial Ctrl+Shift shorthand
      KC_PGDOWN  -> KC_PGDN      (plus other aliases in KEYCODE_MAP)
    """
    if not isinstance(code, str):
        return str(code)

    if not normalise:
        return code

    # Fast path – direct replacement
    if code in KEYCODE_MAP:
        return KEYCODE_MAP[code]

    paren = code.find("(")
    if paren != -1 and code.endswith(")"):
        wrapper = code[:paren]        # e.g. "LALT_T", "LT2", "C_S"
        inner   = code[paren + 1:-1] # e.g. "KC_SPACE"
        inner   = INNER_KEYCODE_MAP.get(inner, inner)

        # Vial layer-tap shorthand: LT2(KC_X) -> LT(2, KC_X)
        lt_match = re.fullmatch(r'LT(\d+)', wrapper)
        if lt_match:
            return f"LT({lt_match.group(1)}, {inner})"

        # Vial Ctrl+Shift shorthand: C_S(KC_X) -> RCS(KC_X)
        if wrapper == "C_S":
            return f"RCS({inner})"

        return f"{wrapper}({inner})"

    return code


def detect_layout(vil_layers: list[list[list]]) -> str:
    """
    Inspect col 6 of the centre-key rows across all layers.
    If any layer has a real key there, the board has 4 centre keys
    and needs LAYOUT_split_3x6_3_ex2 (46 keys).
    Otherwise use LAYOUT_split_3x6_3 (42 keys).
    """
    for layer in vil_layers:
        for row_i in [0, 1, 4, 5]:
            try:
                val = layer[row_i][6]
            except IndexError:
                continue
            if val != -1 and val is not None:
                return "LAYOUT_split_3x6_3_ex2"
    return "LAYOUT_split_3x6_3"


def extract_layer(vil_layer: list[list], layout_macro: str,
                  normalise: bool = True) -> list[str]:
    """
    Convert one VIL layer (8 rows × 7 cols) into an ordered flat list of
    QMK keycodes matching the given layout macro's argument order.

    LAYOUT_split_3x6_3     -> 42 keys (no centre keys)
    LAYOUT_split_3x6_3_ex2 -> 46 keys (with 4 centre keys in rows 1 & 2)
    """
    def cell(row: int, col: int) -> str:
        try:
            val = vil_layer[row][col]
        except IndexError:
            return "KC_TRNS"
        if val == -1 or val is None:
            return "KC_TRNS"
        return normalise_keycode(str(val), normalise=normalise)

    include_centre = (layout_macro == "LAYOUT_split_3x6_3_ex2")
    keys: list[str] = []

    # ---- Row 1: left cols 0-5, [centre_L, centre_R,] right cols 5..0 ----
    for c in range(6):
        keys.append(cell(0, c))
    if include_centre:
        keys.append(cell(0, 6))   # centre-left  (VIL row 0, col 6)
        keys.append(cell(4, 6))   # centre-right (VIL row 4, col 6)
    for c in range(5, -1, -1):
        keys.append(cell(4, c))

    # ---- Row 2: left cols 0-5, [centre_L, centre_R,] right cols 5..0 ----
    for c in range(6):
        keys.append(cell(1, c))
    if include_centre:
        keys.append(cell(1, 6))   # centre-left  (VIL row 1, col 6)
        keys.append(cell(5, 6))   # centre-right (VIL row 5, col 6)
    for c in range(5, -1, -1):
        keys.append(cell(5, c))

    # ---- Row 3: left cols 0-5, right cols 5..0 (no centre keys) ----
    for c in range(6):
        keys.append(cell(2, c))
    for c in range(5, -1, -1):
        keys.append(cell(6, c))

    # ---- Thumbs: left cols 3-5, right cols 5..3 ----
    for c in range(3, 6):
        keys.append(cell(3, c))
    for c in range(5, 2, -1):
        keys.append(cell(7, c))

    return keys  # 42 or 46 keycodes


def format_layer(layer_index: int, keys: list[str], layout_macro: str) -> str:
    """Format a single layer as a C array initialiser."""
    include_centre = (layout_macro == "LAYOUT_split_3x6_3_ex2")
    row_width = 14 if include_centre else 12

    row1   = keys[0          : row_width]
    row2   = keys[row_width  : row_width * 2]
    row3   = keys[row_width * 2 : row_width * 2 + 12]
    thumbs = keys[row_width * 2 + 12 :]

    indent = "        "
    lines = [
        f"    [{layer_index}] = {layout_macro}(",
        f"{indent}" + ", ".join(row1) + ",",
        f"{indent}" + ", ".join(row2) + ",",
        f"{indent}" + ", ".join(row3) + ",",
        f"{indent}" + ", ".join(thumbs) + ")",
    ]
    return "\n".join(lines)



def format_encoder_map(encoder_layout: list[list[list[str]]], normalise: bool = True) -> str:
    """
    Convert the VIL encoder_layout into a QMK encoder_map[] block.

    VIL encoder_layout structure:
        encoder_layout[layer][encoder_index][0 = CCW, 1 = CW]

    Each encoder entry becomes: ENCODER_CCW_CW(ccw, cw)
    """
    indent = "    "
    layer_lines: list[str] = []

    for layer_i, encoders in enumerate(encoder_layout):
        pairs = []
        for ccw, cw in encoders:
            ccw = normalise_keycode(ccw, normalise=normalise)
            cw  = normalise_keycode(cw,  normalise=normalise)
            pairs.append(f"ENCODER_CCW_CW({ccw}, {cw})")
        layer_lines.append(f"{indent}[{layer_i}] = {{ {', '.join(pairs)} }}")

    return (
        "#ifdef ENCODER_MAP_ENABLE\n"
        "const uint16_t PROGMEM encoder_map[][NUM_ENCODERS][NUM_DIRECTIONS] = {\n"
        + ",\n".join(layer_lines) + "\n"
        + "};\n"
        + "#endif"
    )


def convert(vil_path: str, normalise: bool = True) -> str:
    """Load a .vil file and return a complete keymap.c string."""
    with open(vil_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    vil_layers: list[list[list]] = data.get("layout", [])
    if not vil_layers:
        raise ValueError("No 'layout' key found in the .vil file.")

    layout_macro = detect_layout(vil_layers)

    formatted_layers: list[str] = []
    for i, vil_layer in enumerate(vil_layers):
        keys = extract_layer(vil_layer, layout_macro, normalise=normalise)
        formatted_layers.append(format_layer(i, keys, layout_macro))

    layers_block = ",\n".join(formatted_layers)

    encoder_layout = data.get("encoder_layout", [])
    encoder_block = (
        "\n" + format_encoder_map(encoder_layout, normalise=normalise)
        if encoder_layout else ""
    )

    output = textwrap.dedent(f"""\
        // Auto-generated by VialToQMK.py
        // Source: {Path(vil_path).name}
        // Detected layout: {layout_macro}

        #include QMK_KEYBOARD_H

        const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {{
        {layers_block}
        }};
        """) + encoder_block + "\n"

    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a Vial (.vil) layout file to a QMK keymap.c file."
    )
    parser.add_argument("input", help="Path to the .vil input file")
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Path for the generated keymap.c (default: print to stdout)",
    )
    parser.add_argument(
        "--no-normalise",
        dest="normalise",
        action="store_false",
        default=True,
        help=(
            "Keep Vial's original keycode aliases (e.g. KC_BSPACE, KC_QUOTE) "
            "instead of converting to canonical QMK short forms (KC_BSPC, KC_QUOT)."
        ),
    )
    args = parser.parse_args()

    result = convert(args.input, normalise=args.normalise)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(result, encoding="utf-8")
        print(f"Written to {out_path}")
    else:
        print(result)


if __name__ == "__main__":
    main()