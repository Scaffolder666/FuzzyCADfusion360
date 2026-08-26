# Tool icons (hand-drawn set)

`toolbar.html` renders each tool button's icon from a PNG in this folder. Save the
hand-drawn images here with these EXACT names (a transparent background is
preferred so the white sketch paper doesn't show a square on hover/active):

| File | Tool button | What it shows |
|---|---|---|
| `move.png` | Move / Rotate | cube + dashed target + orange diagonal arrow |
| `scale-all.png` | Scale All | cube + three orange arrows spreading from the centre |
| `scale-xyz.png` | Scale X/Y/Z | cube + labelled Z / Y / X axes |
| `axis-rotate.png` | Axis Rotate | cylinder on a disc + dashed axis + orange revolve arrows |
| `extrude.png` | Extrude | cube with an orange top face + up arrow + dashed original |
| `fillet.png` | Fillet | one edge rounded in orange + dashed sharp original |
| `rough.png` | Rough Shape | cube with the whole silhouette flagged in orange |
| `compare.png` | Compare | two cubes — grey solid + orange dashed alternative |
| `hole.png` | Hole | *(pending — still uses the inline SVG until this is added)* |

Square-ish images work best (they render at ~38x38 px, object-fit: contain).
After dropping the files, reload the add-in — no code change needed.
