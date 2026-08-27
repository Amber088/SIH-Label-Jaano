#!/usr/bin/env python3
"""
Generate demo label fixtures for the extraction pipeline's MOCK mode.

Produces, alongside this script (backend/samples/):
    label_front.png  + label_front.png.mock.json
    label_back.png   + label_back.png.mock.json

The PNGs are readable rendered labels. Each sidecar carries:
  * ocr_words   -> the ACTUAL drawn word boxes (so mock OCR == where the text is)
  * extraction  -> (front only) the structured read Gemini would return
  * a suggested `reference` + pdp_bbox so Rule 8 font-height is demonstrable

The manual mm_per_px is computed so the net-quantity glyphs come out to ~2.6 mm and
the front panel to a small-pack area, i.e. a clean COMPLIANT demo. Real photos would
use a card/ArUco reference instead of the manual scale.
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
OUT.mkdir(exist_ok=True)

INK = (25, 25, 25)
PAPER = (250, 249, 245)
ACCENT = (196, 30, 58)


def font(size):
    # Pillow >=10 scalable default font — no TTF file needed.
    return ImageFont.load_default(size=size)


def draw_text(draw, xy, text, f, fill=INK):
    """Draw text and return its pixel bbox [x, y, w, h]."""
    x, y = xy
    l, t, r, b = draw.textbbox((x, y), text, font=f)
    draw.text((x, y), text, font=f, fill=fill)
    return [int(l), int(t), int(r - l), int(b - t)]


def words_from(draw, xy, text, f, fill=INK):
    """Draw a run of words; return one ocr_word box per whitespace token."""
    x, y = xy
    out = []
    space = draw.textlength(" ", font=f)
    for tok in text.split(" "):
        if not tok:
            x += space
            continue
        box = draw_text(draw, (x, y), tok, f, fill=fill)
        out.append({"text": tok, "bbox": box, "confidence": 0.98})
        x += draw.textlength(tok, font=f) + space
    return out


# --------------------------------------------------------------------------- #
# FRONT panel — principal display panel
# --------------------------------------------------------------------------- #
def build_front():
    W, H = 900, 1300
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, W - 8, H - 8], outline=(210, 205, 195), width=3)

    ocr = []
    ocr += words_from(d, (70, 90), "Brand Foods", font(58), fill=ACCENT)
    ocr += words_from(d, (70, 250), "GLUCOSE BISCUITS", font(78))
    ocr += words_from(d, (70, 430), "Rich in Energy", font(40), fill=(90, 90, 90))

    # Net quantity — the glyphs Rule 8 cares about.
    netqty = words_from(d, (70, 720), "Net Qty 500 g", font(64))
    ocr += netqty

    ocr += words_from(d, (70, 900), "MRP Rs 45", font(56))
    ocr += words_from(d, (70, 980), "(inclusive of all taxes)", font(30), fill=(90, 90, 90))

    # Veg mark (green square + dot) — a symbol the engine knows.
    d.rectangle([70, 1120, 130, 1180], outline=(0, 120, 0), width=4)
    d.ellipse([88, 1138, 112, 1162], fill=(0, 120, 0))

    # A reference card drawn in-frame (so `card` calibration is demonstrable too).
    card = [560, 1090, 300, 190]  # x, y, w, h  (85.6 mm wide)
    d.rectangle([card[0], card[1], card[0] + card[2], card[1] + card[3]],
                outline=(120, 120, 160), width=3)
    d.text((card[0] + 20, card[1] + 80), "REF CARD 85.6mm", font=font(22),
           fill=(120, 120, 160))

    # Pick manual scale so net-qty ~= 2.6 mm (median of the net-qty glyph heights).
    heights = [w["bbox"][3] for w in netqty if w["text"] in ("500", "Net", "Qty", "g")]
    heights.sort()
    med = heights[len(heights) // 2]
    mm_per_px = round(2.6 / med, 4)
    pdp_bbox = [40, 40, W - 80, H - 80]
    area_cm2 = round((pdp_bbox[2] * mm_per_px) * (pdp_bbox[3] * mm_per_px) / 100.0, 1)

    extraction = {
        "category": "packaged_food",
        "fields": {
            "manufacturer_details": {"value": "Brand Foods Pvt Ltd, Plot 5, MIDC, Pune, Maharashtra 411001", "panel": "back", "source_image": 1, "confidence": 0.95},
            "generic_name": {"value": "Glucose Biscuits", "panel": "front", "source_image": 0, "confidence": 0.97},
            "net_quantity": {"value": "Net Qty 500 g", "panel": "front", "source_image": 0, "confidence": 0.96},
            "manufacture_date": {"value": "MFG 03/2026", "panel": "back", "source_image": 1, "confidence": 0.9},
            "mrp": {"value": "MRP Rs 45 inclusive of all taxes", "panel": "front", "source_image": 0, "confidence": 0.95},
            "consumer_care": {"value": "Consumer Care: care@brand.com, 1800-123-4567", "panel": "back", "source_image": 1, "confidence": 0.9},
            "ingredients_list": {"value": "Wheat Flour, Sugar, Edible Vegetable Oil, Milk Solids, Salt", "panel": "back", "source_image": 1, "confidence": 0.92},
            "nutritional_info": {"value": "Energy 450 kcal, Protein 7 g per 100 g", "panel": "back", "source_image": 1, "confidence": 0.9},
            "fssai_license": {"value": "FSSAI Lic No 10012345678901", "panel": "back", "source_image": 1, "confidence": 0.95},
            "date_marking": {"value": "Best before 9 months from manufacture. Use by 12/2026", "panel": "back", "source_image": 1, "confidence": 0.9},
            "lot_batch_number": {"value": "Batch No L2026-045", "panel": "back", "source_image": 1, "confidence": 0.9},
        },
        "symbols_detected": ["veg_nonveg_mark", "fssai_logo"],
        "context": {"is_imported": False, "is_single_ingredient": False,
                    "has_additives": False, "has_allergens": False,
                    "dimension_relevant": False},
        "raw_text": ("Brand Foods GLUCOSE BISCUITS Rich in Energy Net Qty 500 g "
                     "MRP Rs 45 inclusive of all taxes"),
        "model": "mock",
    }

    sidecar = {
        "image_width": W, "image_height": H,
        "ocr_words": ocr,
        "extraction": extraction,
        "_calibration_hint": {
            "note": "front panel: use either the manual scale or the drawn card bbox",
            "reference_manual": {"type": "manual", "mm_per_px": mm_per_px,
                                 "pdp_bbox": pdp_bbox},
            "reference_card": {"type": "card", "width_mm": 85.6, "bbox": card,
                               "pdp_bbox": pdp_bbox},
            "expected_netqty_mm": 2.6, "expected_pdp_area_cm2": area_cm2,
        },
    }
    img.save(OUT / "label_front.png")
    (OUT / "label_front.png.mock.json").write_text(
        json.dumps(sidecar, indent=2), encoding="utf-8")
    return mm_per_px, pdp_bbox, area_cm2, card


# --------------------------------------------------------------------------- #
# BACK panel — the detailed declarations
# --------------------------------------------------------------------------- #
def build_back():
    W, H = 900, 1300
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, W - 8, H - 8], outline=(210, 205, 195), width=3)

    ocr = []
    y = 70
    lines = [
        "INGREDIENTS: Wheat Flour, Sugar,",
        "Edible Vegetable Oil, Milk Solids, Salt.",
        "",
        "NUTRITIONAL INFORMATION (per 100 g):",
        "Energy 450 kcal  Protein 7 g",
        "Carbohydrate 70 g  Fat 15 g",
        "",
        "MFG 03/2026    Use by 12/2026",
        "Best before 9 months from manufacture",
        "Batch No L2026-045",
        "",
        "FSSAI Lic No 10012345678901",
        "Consumer Care: care@brand.com",
        "Toll-free 1800-123-4567",
        "",
        "Marketed by Brand Foods Pvt Ltd,",
        "Plot 5, MIDC, Pune, Maharashtra 411001",
    ]
    f = font(38)
    for ln in lines:
        if ln:
            ocr += words_from(d, (60, y), ln, f)
        y += 66

    sidecar = {"image_width": W, "image_height": H, "ocr_words": ocr}
    img.save(OUT / "label_back.png")
    (OUT / "label_back.png.mock.json").write_text(
        json.dumps(sidecar, indent=2), encoding="utf-8")


if __name__ == "__main__":
    mm_per_px, pdp_bbox, area, card = build_front()
    build_back()
    print("wrote samples/label_front.png (+ .mock.json)")
    print("wrote samples/label_back.png  (+ .mock.json)")
    print(f"manual mm_per_px = {mm_per_px}  (net-qty ~= 2.6 mm)")
    print(f"pdp_bbox = {pdp_bbox}  -> pdp_area ~= {area} cm^2")
    print(f"drawn reference card bbox = {card} (85.6 mm)")
