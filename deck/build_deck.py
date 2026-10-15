"""Fill the Hacktoberfest submission template with break-forecast content.

Every number on the slides is read from a run's capsule.json, so the deck always matches the evidence.

    deck/.tools/Scripts/python.exe deck/build_deck.py runs/<run>/capsule.json --team "..." --college "..."
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

HERE = Path(__file__).resolve().parent

# Template palette (sampled from the deck) + provenance colours shared with capsule.html
RED_DARK = RGBColor(0x93, 0x0B, 0x0B)      # template title colour
RED = RGBColor(0xC8, 0x1E, 0x2B)
TINT = RGBColor(0xFB, 0xEC, 0xEC)          # light red card fill
INK = RGBColor(0x1F, 0x23, 0x28)
MUTED = RGBColor(0x5F, 0x63, 0x68)
LINE = RGBColor(0xE3, 0xD6, 0xD6)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
OBS, OBS_BG = RGBColor(0x24, 0x57, 0xC5), RGBColor(0xE8, 0xEE, 0xFB)
EXE, EXE_BG = RGBColor(0x0D, 0x7A, 0x52), RGBColor(0xE3, 0xF4, 0xEC)
AI, AI_BG = RGBColor(0x8A, 0x3F, 0xC0), RGBColor(0xF3, 0xEA, 0xFA)
WARN, WARN_BG = RGBColor(0x9A, 0x62, 0x00), RGBColor(0xFD, 0xF1, 0xD8)
FAIL, FAIL_BG = RGBColor(0xB4, 0x23, 0x18), RGBColor(0xFD, 0xE6, 0xE3)
GREY_BG = RGBColor(0xEC, 0xEC, 0xEA)
BODY_FONT = "Calibri"

# Usable area between the template's red bars, logos and footer (inches)
X0, X1, Y0, Y1 = 0.98, 10.42, 1.12, 7.12


def box(slide, x, y, w, h, *, fill=None, line=None, radius=0.06, shape=MSO_SHAPE.ROUNDED_RECTANGLE, name=None):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(0.75)
    s.shadow.inherit = False
    if name:
        s.name = name
    tf = s.text_frame
    tf.margin_left = tf.margin_right = Inches(0.08)
    tf.margin_top = tf.margin_bottom = Inches(0.04)
    return s


def text(slide, x, y, w, h, paras, *, size=12, color=INK, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, font=BODY_FONT, margin=0.0, space_after=2, name=None):
    """paras: str | list of (str | list of (text, {opts})) - one entry per paragraph."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    fill_text(tb.text_frame, paras, size=size, color=color, bold=bold, align=align, font=font,
              space_after=space_after)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Inches(margin)
    return tb


def fill_text(tf, paras, *, size=12, color=INK, bold=False, align=PP_ALIGN.LEFT, font=BODY_FONT, space_after=2,
              bullet=False):
    if isinstance(paras, str):
        paras = [paras]
    tf.word_wrap = True
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space_after)
        runs = [(para, {})] if isinstance(para, str) else para
        for t, opts in runs:
            r = p.add_run()
            r.text = t
            f = r.font
            f.name = opts.get("font", font)
            f.size = Pt(opts.get("size", size))
            f.bold = opts.get("bold", bold)
            f.italic = opts.get("italic", False)
            f.color.rgb = opts.get("color", color)
        if (bullet or (not isinstance(para, str) and any(o.get("bullet") for _, o in runs))):
            _bullet(p)


def _bullet(p, char="•"):
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", str(Emu(Inches(0.16))))
    pPr.set("indent", str(-Emu(Inches(0.14))))
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    bu = pPr.makeelement(qn("a:buChar"), {"char": char})
    pPr.append(bu)


def bullets(slide, x, y, w, h, items, *, size=12, color=INK, space_after=3, name=None):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    norm = [[(it, {})] if isinstance(it, str) else it for it in items]
    fill_text(tf, norm, size=size, color=color, space_after=space_after, bullet=True)
    return tb


def pill(slide, x, y, label, fg, bg, *, w=None, size=9, name=None):
    w = w or (0.12 + 0.068 * len(label) * size / 9)
    s = box(slide, x, y, w, 0.24, fill=bg, radius=0.5, name=name)
    tf = s.text_frame
    tf.margin_left = tf.margin_right = Inches(0.04)
    tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    fill_text(tf, [[(label, {"bold": True})]], size=size, color=fg, align=PP_ALIGN.CENTER)
    return s


PROV = {"observed": ("OBSERVED", OBS, OBS_BG), "executed": ("EXECUTED", EXE, EXE_BG),
        "ai": ("AI-GENERATED", AI, AI_BG)}


def prov(slide, x, y, kind, size=8):
    label, fg, bg = PROV[kind]
    return pill(slide, x, y, label, fg, bg, size=size, w=0.95 if kind == "ai" else 0.78)


def arrow(slide, x1, y1, x2, y2, color=MUTED):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(1.25)
    ln = c.line._get_or_add_ln()
    tail = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
    ln.append(tail)
    return c


def table(slide, x, y, w, rows, col_w, *, size=10, header_fill=RED_DARK, row_h=0.27, fills=None, name=None):
    shape = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w),
                                   Inches(row_h * len(rows)))
    if name:
        shape.name = name
    tbl = shape.table
    tbl.first_row = True
    for j, cw in enumerate(col_w):
        tbl.columns[j].width = Inches(cw)
    for i, row in enumerate(rows):
        tbl.rows[i].height = Inches(row_h)
        for j, val in enumerate(row):
            cell = tbl.cell(i, j)
            cell.margin_left = cell.margin_right = Inches(0.05)
            cell.margin_top = cell.margin_bottom = Inches(0.02)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.paragraphs[0].text = ""
            runs = val if isinstance(val, list) else [(str(val), {})]
            if i == 0:
                runs = [(t, {**o, "bold": True}) for t, o in runs]
            fill_text(tf, [runs], size=size, color=WHITE if i == 0 else INK)
            cell.fill.solid()
            if i == 0:
                cell.fill.fore_color.rgb = header_fill
            elif fills and fills.get((i, j)) is not None:
                cell.fill.fore_color.rgb = fills[(i, j)]
            else:
                cell.fill.fore_color.rgb = WHITE if i % 2 else RGBColor(0xFA, 0xF6, 0xF6)
    return shape


RESULT_STYLE = {"pass": (EXE, EXE_BG), "flaky": (WARN, WARN_BG), "warn": (WARN, WARN_BG),
                "fail": (FAIL, FAIL_BG), "import-fail": (FAIL, FAIL_BG), "install-fail": (FAIL, FAIL_BG),
                "skipped": (MUTED, GREY_BG), "verified": (EXE, EXE_BG), "verified_tests": (WARN, WARN_BG),
                "rejected": (FAIL, FAIL_BG), "no_patch": (MUTED, GREY_BG)}


# --------------------------------------------------------------------------------------------- data
def facts(cap: dict) -> dict:
    deps = cap["dependencies"]
    labels = Counter(lbl for d in deps for lbl in d["classification"])
    mx = cap.get("upgrade_matrix") or []
    cells = {}
    for r in mx:
        cells.setdefault(r["cell"], {})[r["python"]] = r
    patches = cap.get("patches") or []
    best = {p["cell"]: p for p in patches if p.get("selected")}
    fc = cap.get("forecast") or []
    score = next((f for f in fc if f.get("kind") == "forecast_score"), None)
    bd = cap.get("behavioral_diffs") or []
    probes_def = [b for b in bd if b.get("kind") == "probe"]
    matrix_diffs = [b for b in bd if b.get("kind") == "matrix_probe_diff" and b.get("divergences")]
    # Strongest kind of silent change: input the old code rejected that the new dependency accepts.
    now_accepted = None
    for d in matrix_diffs:
        for x in d.get("details") or []:
            for v in x["divergences"]:
                ref, cand = v["reference"], v["candidate"]
                if isinstance(ref, dict) and "error" in ref and not (isinstance(cand, dict) and "error" in cand):
                    now_accepted = now_accepted or f"`{v['case']}` ({d['cell'].split(':')[1].split('@')[0]})"
    b = cap.get("baseline") or {}
    py_first = next(iter((b.get("pythons") or {}).values()), {})
    att = {a["name"]: a for a in py_first.get("attempts", [])}
    return {
        "deps": deps, "labels": labels, "indicators": Counter(r["indicator"] for r in cap["risk_indicators"]),
        "cells": cells, "cell_meta": {c["id"]: c for c in cap.get("cells") or []},
        "results": Counter(r["result"] for r in mx), "jobs": len(mx),
        "best": best, "patches": patches, "score": score, "probes": probes_def, "matrix_diffs": matrix_diffs,
        "now_accepted": now_accepted,
        "baseline": b, "attempts": att, "pythons": cap["meta"]["target_pythons"],
        "diagnoses": cap.get("diagnoses") or [], "reps": cap.get("replacement_suggestions") or [],
        "usage_src": sum(1 for u in cap["usage_locations"] if not u["in_tests"]),
        "limitations": cap.get("limitations") or [],
        "models": sorted({(x.get("source") or {}).get("model_used") for x in
                          (cap.get("diagnoses") or []) + patches + (cap.get("replacement_suggestions") or []) + fc
                          if (x.get("source") or {}).get("model_used")}),
    }


# ------------------------------------------------------------------------------------------- slides
def slide1(s, f, team, college):
    tb = next(sh for sh in s.shapes if sh.name == "TextBox 30")
    tb.left, tb.top, tb.width, tb.height = Inches(1.1), Inches(3.78), Inches(4.55), Inches(3.2)
    tf = tb.text_frame
    for p in list(tf.paragraphs)[1:]:
        p._p.getparent().remove(p._p)
    tf.paragraphs[0].runs[0].text = ""
    first = tf.paragraphs[0]
    for r in list(first.runs):
        r._r.getparent().remove(r._r)
    entries = [("Team Name: ", team), ("Team Members: ", ""), ("College Name: ", college),
               ("Track: ", "Track 2 – Maintenance Agent Skill")]
    for i, (label, value) in enumerate(entries):
        p = first if i == 0 else tf.add_paragraph()
        p.space_after = Pt(10)
        for t, bold in ((label, True), (value, False)):
            r = p.add_run()
            r.text = t
            r.font.name, r.font.size, r.font.bold = "Times New Roman", Pt(20), bold
            r.font.color.rgb = INK
    # Problem overview card on the right
    x, y, w, h = 5.95, 3.78, 4.42, 3.2
    box(s, x, y, w, h, fill=TINT, name="Problem card")
    text(s, x + 0.18, y + 0.14, w - 0.36, 0.34, [[("break-forecast", {"bold": True, "size": 17, "color": RED_DARK,
                                                                          "font": "Times New Roman"})]])
    text(s, x + 0.18, y + 0.5, w - 0.36, 0.5,
         "Problem: dependencies silently rot – pins stop installing, packages are abandoned, upgrades break "
         "code – and maintainers find out only when something fails.", size=11, color=INK)
    bullets(s, x + 0.18, y + 1.32, w - 0.36, 1.8, [
        [("Tests every dependency change ", {"bold": True}), ("with real installs on Python 3.12 + 3.13", {})],
        [("Repairs breakage ", {"bold": True}), ("with open-weight Gemma 4 patches", {})],
        [("Accepts a fix only ", {"bold": True}), ("when tests + behaviour probes prove it", {})],
        [("Target: ", {"bold": True}), ("github.com/rishrek/LinkWatch", {})],
    ], size=11, space_after=4)


STAGES = [("1", "Inspect", "manifests · PyPI · AST", "observed"),
          ("2", "Baseline", "+ era reconstruction", "executed"),
          ("4", "Plan cells", "uv resolver co-upgrades", "observed"),
          ("3", "Blind forecast", "locked before tests", "ai"),
          ("8", "Probes", "behaviour scripts", "ai"),
          ("5", "Matrix", "3.12 + 3.13, normal + strict", "executed"),
          ("6", "Diagnose", "root cause", "ai"),
          ("7", "Patch + gate", "11 rules, 3 tries", "ai"),
          ("9", "Exit plan", "all changes together", "executed"),
          ("10", "Capsule", "JSON · HTML · MD", "observed")]


def slide2(s, f):
    text(s, X0, 1.1, X1 - X0, 0.36,
         [[("One command – ", {"bold": True}), ("bf run <repo-url>", {"bold": True, "font": "Consolas",
                                                                     "color": RED_DARK}),
           (" – takes any Python repo through ten stages and writes a Dependency Exit Capsule.", {})]], size=12)
    # 2 rows x 5 stages, snake order: row 1 left -> right, row 2 right -> left
    cw, ch, gx = 1.64, 0.92, 0.27
    for i, (num, title, sub, kind) in enumerate(STAGES):
        row, k = divmod(i, 5)
        col = k if row == 0 else 4 - k
        cx = X0 + 0.05 + col * (cw + gx)
        cy = 1.62 + row * 1.32
        fg = {"observed": OBS, "executed": EXE, "ai": AI}[kind]
        bg = {"observed": OBS_BG, "executed": EXE_BG, "ai": AI_BG}[kind]
        box(s, cx, cy, cw, ch, fill=bg, line=fg, radius=0.12, name=f"Stage {num}")
        text(s, cx + 0.08, cy + 0.08, cw - 0.16, 0.3, [[(f"{num}  ", {"bold": True, "color": fg}),
                                                        (title, {"bold": True})]], size=12)
        text(s, cx + 0.08, cy + 0.42, cw - 0.16, 0.45, sub, size=10, color=MUTED)
        if k < 4:
            if row == 0:
                arrow(s, cx + cw + 0.02, cy + ch / 2, cx + cw + gx - 0.02, cy + ch / 2)
            else:
                arrow(s, cx - 0.02, cy + ch / 2, cx - gx + 0.02, cy + ch / 2)
    # turn: last stage of row 1 -> first stage of row 2 (same column, below)
    tx = X0 + 0.05 + 4 * (cw + gx) + cw / 2
    arrow(s, tx, 1.62 + ch + 0.02, tx, 1.62 + 1.32 - 0.02)
    text(s, X0 + 0.05, 4.22, 6, 0.26, [[("Legend:  ", {"bold": True})]], size=10, color=MUTED)
    prov(s, X0 + 0.75, 4.23, "observed")
    prov(s, X0 + 1.6, 4.23, "executed")
    prov(s, X0 + 2.45, 4.23, "ai")
    text(s, X0 + 3.5, 4.22, 6, 0.26, "every field in the capsule carries one of these tags", size=10, color=MUTED)

    cols = [("Key technologies & data", [
        "uv: isolated envs, resolver, era-limited installs",
        "pytest + JUnit XML per-test results",
        "Python AST: imports, calls, private APIs, monkeypatches",
        "PyPI JSON + GitHub REST APIs, release notes",
    ]), ("AI integration (Gemma 4, open-weight)", [
        "Proposes replacements for frozen packages",
        "Forecasts each change before it runs",
        "Writes behaviour probes and diagnoses failures",
        "Writes patches – only kept if the gate passes",
    ]), ("Verification & outputs", [
        "Gate: tamper checks, no regressions, target fixed, strict run, probes",
        "Exit plan: all patches + upgrades together",
        "capsule.json / .html / .md",
        "bf verify-capsule --rerun reproduces results",
    ])]
    w = (X1 - X0 - 0.3) / 3
    for i, (title, items) in enumerate(cols):
        x = X0 + i * (w + 0.15)
        box(s, x, 4.62, w, 2.42, fill=TINT, name=f"Column {i + 1}")
        text(s, x + 0.14, 4.72, w - 0.28, 0.3, [[(title, {"bold": True, "color": RED_DARK})]], size=12)
        bullets(s, x + 0.14, 5.1, w - 0.28, 1.9, items, size=11, space_after=5)


def slide3(s, f):
    # Left: stack table
    rows = [["Layer", "Choice"],
            ["Language", "Python 3.11+ (targets 3.12 & 3.13)"],
            ["Environments", "uv venv / pip compile / --exclude-newer-package"],
            ["Tests", "pytest, normal + -W error::DeprecationWarning"],
            ["Models", "gemma-4-31b-it → gemma-4-26b-a4b-it (Gemini API) → gemma4:12b (Ollama)"],
            ["Format", "Agent Skill (SKILL.md) + CLI + JSON-schema capsule"],
            ["Viewer", "self-contained HTML, no network"]]
    table(s, X0, 1.15, 4.55, rows, [1.15, 3.4], size=9.5, row_h=0.36, name="Stack table")

    # Right: algorithms
    x = X0 + 4.75
    w = X1 - x
    text(s, x, 1.12, w, 0.3, [[("Methodology – original algorithms", {"bold": True, "color": RED_DARK})]], size=13)
    algos = [
        ("Era reconstruction", "each transitive dep is capped at the release date of the pinned package that "
                               "needs it (uv --exclude-newer-package)"),
        ("Resolver co-upgrades", "start fully locked; free the target's subtree → all transitives → pins the "
                                 "resolver names; record what moved"),
        ("Blind forecast", "prediction hashed + time-stamped before Stage 5; scored with a confusion matrix"),
        ("Differential probes", "same script on old and new env; normalised JSON diff of the repo's own functions"),
        ("Evidence gate", "6 static + 5 executed rules on both Pythons; failures fed back for up to 3 attempts"),
    ]
    yy = 1.48
    for name, desc in algos:
        text(s, x, yy, w, 0.5, [[(name + ": ", {"bold": True}), (desc, {})]], size=10.5)
        yy += 0.56

    # Bottom: implementation + testing
    y = 4.45
    box(s, X0, y, X1 - X0, 2.55, fill=TINT, name="Testing card")
    text(s, X0 + 0.15, y + 0.1, 4, 0.3, [[("Implementation, testing & validation", {"bold": True,
                                                                                 "color": RED_DARK})]], size=13)
    stats = [(str(f["modules"]), "Python modules in src/break_forecast"),
             (str(f["unit_tests"]), "unit tests, incl. every gate rule"),
             ("2", "real repos: LinkWatch + a second fixture"), (str(f["jobs"]), "matrix jobs executed on LinkWatch")]
    sw = (X1 - X0 - 0.3) / 4
    for i, (n, lbl) in enumerate(stats):
        sx = X0 + 0.15 + i * sw
        text(s, sx, y + 0.5, sw - 0.1, 0.55, [[(n, {"bold": True, "size": 28, "color": RED_DARK})]], size=28)
        text(s, sx, y + 1.08, sw - 0.15, 0.45, lbl, size=10, color=MUTED)
    bullets(s, X0 + 0.15, y + 1.6, X1 - X0 - 0.3, 0.9, [
        "Gate tests try to cheat on purpose (skip markers, warning filters, sys.modules stubs, broad except, "
        "test edits, quiet downgrades) and must be rejected",
        "Every capsule is validated against its JSON schema; verify-capsule --rerun re-executes verified results"
        + (f" – {f['reproduced']} reproduced on LinkWatch from the capsule alone" if f.get("reproduced") else ""),
    ], size=11, space_after=4)


def slide4(s, f):
    title = next(sh for sh in s.shapes if sh.name == "TextBox 17")
    for r in title.text_frame.paragraphs[0].runs:  # fix the template's "Feasability" typo, keep its styling
        r.text = r.text.replace("Feasability", "Feasibility")
    quads = [
        ("Technical feasibility & resources", [
            "Runs on a laptop: Windows 11 or Linux, uv, git",
            "No GPU needed: free Gemma 4 API tier",
            "Optional offline mode: local Ollama Gemma 4 12B",
            "Python 3.12 / 3.13 provisioned automatically by uv",
            "Every stage implemented, unit-tested and run on 2 repos",
        ]),
        ("Challenges, limitations & risks", [
            "Hosted model overloads (HTTP 500/503) → retries, 31B → 26B → local chain, answer cache",
            "pytest-based repos only; probes avoid network code",
            "AI patches can be wrong → never trusted without the gate",
            "Forecast accuracy measured on few cells → confusion matrix, not one %",
        ]),
        ("Scalability", [
            "Every cell isolated → parallel workers (--workers N)",
            "Resolver-guided groups instead of all pairs: O(n), not O(n²)",
            "uv wheel cache makes each extra env take seconds",
            "Cached PyPI/GitHub fetches and model answers",
            "Same command runs in CI on a schedule",
        ]),
        ("Sustainability & long-term viability", [
            "Apache-2.0; deps are MIT/Apache; model is Apache-2.0",
            "Generic – no repo-specific logic; shown on a 2nd repo",
            "Model swappable via env vars (any open-weight model)",
            "Agent Skills standard → usable by many agent harnesses",
            "Capsules are re-runnable artefacts for future upgrades",
        ]),
    ]
    w = (X1 - X0 - 0.2) / 2
    h = 2.88
    for i, (title, items) in enumerate(quads):
        r, c = divmod(i, 2)
        x = X0 + c * (w + 0.2)
        y = 1.15 + r * (h + 0.14)
        box(s, x, y, w, h, fill=TINT if i != 1 else WARN_BG, name=f"Quadrant {i + 1}")
        text(s, x + 0.15, y + 0.12, w - 0.3, 0.34, [[(title, {"bold": True, "color": RED_DARK if i != 1 else WARN})]],
             size=14)
        bullets(s, x + 0.15, y + 0.58, w - 0.3, h - 0.7, items, size=12.5, space_after=7)


def slide5(s, f):
    res = f["results"]
    n_verified = sum(1 for p in f["best"].values() if p["status"] == "verified" and not p.get("combined_from"))
    n_tests = sum(1 for p in f["best"].values() if p["status"] == "verified_tests" and not p.get("combined_from"))
    sc = f["score"]
    stats = [
        (str(len(f["deps"])), "dependencies inspected"),
        (str(f["jobs"]), f"test runs ({len(f['cells'])} changes × {len(f['pythons'])} Pythons)"),
        (str(n_verified + n_tests), "AI patches that passed the gate"),
        (f"{sc['correct']}/{sc['scored_cells']}" if sc else "–", "forecasts correct before testing"),
    ]
    sw = (X1 - X0 - 0.45) / 4
    for i, (n, lbl) in enumerate(stats):
        x = X0 + i * (sw + 0.15)
        box(s, x, 1.15, sw, 1.32, fill=TINT, name=f"Stat {i + 1}")
        text(s, x + 0.12, 1.2, sw - 0.24, 0.7, [[(n, {"bold": True, "size": 32, "color": RED_DARK})]], size=32)
        text(s, x + 0.12, 1.88, sw - 0.24, 0.55, lbl, size=10.5, color=MUTED)

    # Before / after on LinkWatch
    y = 2.68
    text(s, X0, y, 5.2, 0.3, [[("Measured on LinkWatch (from the capsule)", {"bold": True, "color": RED_DARK})]],
         size=13)
    att = f["attempts"]
    aw = att.get("as_written", {}).get("tests", {}).get("counts", {})
    rc = att.get("reconstructed", {}).get("tests", {}).get("counts", {})
    items = [
        [("Pins as written: ", {"bold": True}),
         (f"{aw.get('passed', '–')}/{aw.get('passed', 0) + aw.get('failed', 0) + aw.get('error', 0)} tests pass", {}),
         (" → era-reconstructed baseline: ", {"bold": True}),
         (f"{rc.get('passed', '–')}/{rc.get('passed', 0) + rc.get('failed', 0) + rc.get('error', 0)}", {})],
        [("Found without hints: ", {"bold": True}),
         (f"{f['labels'].get('frozen', 0)} frozen, {f['labels'].get('unused', 0)} unused, "
          f"{f['labels'].get('undeclared', 0)} undeclared deps, a private-API import and a monkeypatch", {})],
        [("Matrix: ", {"bold": True}),
         (f"{res.get('pass', 0)} pass · {res.get('warn', 0)} warn · "
          f"{res.get('fail', 0) + res.get('import-fail', 0) + res.get('install-fail', 0)} break · "
          f"{res.get('skipped', 0)} skipped", {})],
        [("Silent changes caught: ", {"bold": True}),
         (f"{len(f['matrix_diffs'])} runs where tests passed but probes saw behaviour change", {})
         ] + ([(f" – e.g. {f['now_accepted']} was rejected before the upgrade and is accepted after it", {})]
              if f.get("now_accepted") else []),
    ]
    bullets(s, X0, y + 0.38, 5.3, 2.2, items, size=11, space_after=6)

    # Users + impact
    x = X0 + 5.55
    w = X1 - x
    box(s, x, y, w, 2.2, fill=TINT, name="Users card")
    text(s, x + 0.14, y + 0.1, w - 0.28, 0.3, [[("Target users", {"bold": True, "color": RED_DARK})]], size=13)
    bullets(s, x + 0.14, y + 0.46, w - 0.28, 1.7, [
        "Open-source maintainers with old pins",
        "Teams moving to Python 3.12 / 3.13",
        "AI agents that need a safe upgrade skill",
        "Reviewers who want evidence, not claims",
    ], size=11, space_after=5)
    y2 = 5.05
    box(s, X0, y2, X1 - X0, 2.0, fill=EXE_BG, name="Impact card")
    text(s, X0 + 0.15, y2 + 0.1, 6, 0.3, [[("Social & economic impact · long-term benefits",
                                             {"bold": True, "color": EXE})]], size=13)
    bullets(s, X0 + 0.15, y2 + 0.48, X1 - X0 - 0.3, 1.45, [
        "Less maintainer burnout: dependency chores become one command with a reviewable report",
        "Fewer broken downstream users: changes are proven on both Pythons before release",
        "Trustworthy AI help: every AI claim is separated from evidence and re-runnable by anyone",
        "Reusable capsules turn one migration into a template for the next upgrade",
    ], size=11.5, space_after=5)


REFERENCES = [
    ("Specs & docs", [
        ("Agent Skills specification", "agentskills.io/specification"),
        ("Gemma 4 on the Gemini API", "ai.google.dev/gemma/docs/core/gemma_on_gemini_api"),
        ("Gemma 4 under Apache 2.0 (Google Open Source Blog)",
         "opensource.googleblog.com/2026/03/gemma-4-expanding-the-gemmaverse-with-apache-20.html"),
        ("uv resolution: exclude-newer", "docs.astral.sh/uv/concepts/resolution"),
        ("PyPI JSON API", "docs.pypi.org/api/json"),
        ("importlib.metadata.packages_distributions", "docs.python.org/3/library/importlib.metadata.html"),
        ("pytest warnings & -W", "docs.pytest.org/en/stable/how-to/capture-warnings.html"),
    ]),
    ("Research", [
        ("Jimenez et al., SWE-bench: Can Language Models Resolve Real-World GitHub Issues?, ICLR 2024",
         "arxiv.org/abs/2310.06770"),
        ("McKeeman, Differential Testing for Software, Digital Technical Journal, 1998", None),
        ("Feathers, Working Effectively with Legacy Code (characterization tests), 2004", None),
        ("Ochoa et al., Breaking bad? Semantic versioning and impact of breaking changes in Maven Central, "
         "EMSE 2022", None),
    ]),
    ("Existing tools – and the gap", [
        ("Dependabot / Renovate: open upgrade PRs, rely on your CI – no repair, no evidence capsule", None),
        ("deptry: finds unused/missing deps – no execution or migration", None),
        ("break-forecast adds: era reconstruction, gated AI repair, probes, re-runnable capsule", None),
    ]),
]


def slide6(s, f):
    y = 1.12
    for title, refs in REFERENCES:
        text(s, X0, y, X1 - X0, 0.3, [[(title, {"bold": True, "color": RED_DARK})]], size=13)
        y += 0.36
        items = []
        for label, url in refs:
            run = [(label, {})]
            if url:
                run += [(" – ", {"color": MUTED}), (url, {"color": OBS, "size": 10, "link": True})]
            items.append(run)
        tb = bullets(s, X0, y, X1 - X0, 0.27 * len(items), items, size=10.5, space_after=3)
        for p, (_, url) in zip(tb.text_frame.paragraphs, refs, strict=True):
            if url:
                p.runs[-1].hyperlink.address = "https://" + url
        lines = sum(1 + (len(label) + len(url or "")) // 185 for label, url in refs)
        y += 0.235 * lines + 0.28
    text(s, X0, 6.78, X1 - X0, 0.3, [[("Source code, capsule and README: ", {"bold": True}),
                                       ("Apache-2.0 – see repository break-forecast", {})]], size=10, color=MUTED)


def build(capsule_path: Path, out: Path, team: str, college: str, unit_tests: int,
          rerun_path: Path | None = None) -> Path:
    cap = json.loads(capsule_path.read_text(encoding="utf-8"))
    f = facts(cap)
    if rerun_path and rerun_path.exists():
        rr = json.loads(rerun_path.read_text(encoding="utf-8"))
        f["reproduced"] = f"{sum(c['reproduced'] for c in rr['checks'])}/{len(rr['checks'])}"
    f["modules"] = len([p for p in (HERE.parent / "src" / "break_forecast").glob("*.py") if p.name != "__init__.py"])
    f["unit_tests"] = unit_tests
    prs = Presentation(str(HERE / "template.pptx"))
    s = prs.slides
    slide1(s[0], f, team, college)
    slide2(s[1], f)
    slide3(s[2], f)
    slide4(s[3], f)
    slide5(s[4], f)
    slide6(s[5], f)
    for i, sl in enumerate(s):
        sl.notes_slide.notes_text_frame.text = NOTES[i]
    prs.save(str(out))
    return out


NOTES = [
    "Introduce the team and Track 2. One sentence on the problem: dependency rot is found only when things break.",
    "Walk the ten stages left to right. Point at the colours: blue = observed, green = executed, purple = AI.",
    "Highlight the original algorithms: era reconstruction, resolver co-upgrades, the evidence gate.",
    "Be honest about the risks, especially the hosted model's overloads and how the fallback chain handles them.",
    "Read the measured numbers: they all come from the capsule of the LinkWatch run shown in the demo.",
    "Close with the gap versus Dependabot/Renovate/deptry, then switch to the live demo.",
]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("capsule", type=Path)
    ap.add_argument("--team", default="", help="left blank by default; fill it in PowerPoint")
    ap.add_argument("--college", default="", help="left blank by default; fill it in PowerPoint")
    ap.add_argument("--unit-tests", type=int, required=True, help="number collected by `pytest --collect-only -q`")
    ap.add_argument("--rerun", type=Path, help="rerun.json from `bf verify-capsule --rerun`")
    ap.add_argument("-o", "--out", type=Path, default=HERE / "break-forecast-submission.pptx")
    a = ap.parse_args()
    print(build(a.capsule, a.out, a.team, a.college, a.unit_tests, a.rerun))
