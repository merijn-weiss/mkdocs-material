#!/usr/bin/env python3
import base64
import html
import json
import logging
import os
import re
import zlib
from pathlib import Path
from urllib.parse import unquote
from defusedxml import ElementTree as ET
import xml.etree.ElementTree as _ET  # typing only

#
# ---------- Configuration ----------
ROOT = Path(os.environ.get("DIAGRAM_ROOT", Path.cwd()))
OUTDIR = Path(os.environ.get("DIAGRAM_ELEMENTS_BROKER", ROOT))
OUTDIR.mkdir(parents=True, exist_ok=True)

# Scan draw.io-ish files (override via GLOB)
DRAWIO_GLOB = os.environ.get("GLOB", "**/*.drawio")

# If STRICT=1, exit non-zero if any parse errors or no output
STRICT = os.environ.get("STRICT", "0") == "1"

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Output shaping flags (default: clean/minimal)
INCLUDE_GEOMETRY = os.environ.get("INCLUDE_GEOMETRY", "0") == "1"
INCLUDE_DEBUG_STYLE = os.environ.get("INCLUDE_DEBUG_STYLE", "0") == "1"  # keep compact style hints
INCLUDE_PLACEHOLDERS = os.environ.get("INCLUDE_PLACEHOLDERS", "0") == "1"  # include background/placeholder nodes

# - exclude: keys that should never be exported
# - top_level: keys that should be lifted to node/edge top-level fields (rare; keep small)
MONDRIAN_CONFIG = Path("/opt/mondrian/tools/diagrams")

DRAWIO_ATTR_CONFIG_PATH = Path(
    os.environ.get(
        "DRAWIO_ATTR_CONFIG",
        MONDRIAN_CONFIG / "drawio_attr_config.json"
    )
)

DRAWIO_FILTER_CONFIG_PATH = Path(
    os.environ.get(
        "DRAWIO_FILTER_CONFIG",
        MONDRIAN_CONFIG / "drawio_filter_config.json"
    )
)

# Global variable to store the loaded filter config
FILTER_CONFIG: dict | None = None

_DEFAULT_EXCLUDE_ATTR_KEYS: set[str] = {
    # Wrapper-only / draw.io metadata we do not want to export
    "placeholders",
    "repoAttributes",
    "mondrianVersion",
    "templateAttributes",
    "templateAttributesMandatory",

    # These are already exported explicitly on the node/edge itself;
    # keeping them out of data_attributes avoids duplicates.
    "id",
    "label",
    "style",
}

_DEFAULT_TOP_LEVEL_ATTR_KEYS: set[str] = set()
# We already export id/label/style explicitly; keep this for future flexibility.

# Loaded at runtime (may be overridden by external config)
EXCLUDE_ATTR_KEYS: set[str] = set(_DEFAULT_EXCLUDE_ATTR_KEYS)
TOP_LEVEL_ATTR_KEYS: set[str] = set(_DEFAULT_TOP_LEVEL_ATTR_KEYS)

# Case-insensitive views (draw.io exports vary in casing like "id" vs "Id")
EXCLUDE_ATTR_KEYS_LOWER: set[str] = {k.lower() for k in EXCLUDE_ATTR_KEYS}
TOP_LEVEL_ATTR_KEYS_LOWER: set[str] = {k.lower() for k in TOP_LEVEL_ATTR_KEYS}

def _load_attr_projection_config() -> None:
    """Load attribute include/exclude rules from JSON if available.

    Expected JSON structure:
      {"exclude": ["k1", ...], "top_level": ["k2", ...]}

    Notes:
    - Missing/invalid file does NOT fail extraction; we fall back to defaults.
    - Keys are treated as case-sensitive (draw.io wrapper attrs are case-sensitive).
    """
    global EXCLUDE_ATTR_KEYS, TOP_LEVEL_ATTR_KEYS, EXCLUDE_ATTR_KEYS_LOWER, TOP_LEVEL_ATTR_KEYS_LOWER

    try:
        p = DRAWIO_ATTR_CONFIG_PATH
        if not p.exists() or not p.is_file():
            return

        cfg = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(cfg, dict):
            return

        ex = cfg.get("exclude")
        tl = cfg.get("top_level")

        if isinstance(ex, list):
            EXCLUDE_ATTR_KEYS = {str(x) for x in ex if str(x).strip()}
        if isinstance(tl, list):
            TOP_LEVEL_ATTR_KEYS = {str(x) for x in tl if str(x).strip()}

        # Always keep defaults excluded even if config forgets them
        EXCLUDE_ATTR_KEYS |= set(_DEFAULT_EXCLUDE_ATTR_KEYS)

        # Prevent excluded keys from being re-introduced as top-level lifts.
        TOP_LEVEL_ATTR_KEYS -= EXCLUDE_ATTR_KEYS

        # Refresh case-insensitive helper sets
        EXCLUDE_ATTR_KEYS_LOWER = {k.lower() for k in EXCLUDE_ATTR_KEYS}
        TOP_LEVEL_ATTR_KEYS_LOWER = {k.lower() for k in TOP_LEVEL_ATTR_KEYS}

        logging.debug(
            "Loaded draw.io attribute config: exclude=%d, top_level=%d (%s)",
            len(EXCLUDE_ATTR_KEYS),
            len(TOP_LEVEL_ATTR_KEYS),
            str(p),
        )
    except Exception as e:
        logging.warning("Failed to load DRAWIO_ATTR_CONFIG (%s): %s", str(DRAWIO_ATTR_CONFIG_PATH), e)



# ---------- Filter Config + Helpers ----------
def _load_filter_config() -> dict | None:
    """Load optional export filter rules from JSON.

    Expected JSON structure:
      {
        "mode": "AND" | "OR",
        "nodes": {"shape": ["..."], "shapeType": ["..."]},
        "edges": {"shape": ["..."]}
      }

    Notes:
    - Missing file means: no filtering.
    - Invalid file means: no filtering (with warning).
    - Only top-level exported fields are supported for now.
    """
    try:
        p = DRAWIO_FILTER_CONFIG_PATH
        if not p.exists() or not p.is_file():
            return None

        cfg = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(cfg, dict):
            logging.warning("DRAWIO_FILTER_CONFIG is not a JSON object: %s", str(p))
            return None

        mode = str(cfg.get("mode", "AND")).strip().upper()
        if mode not in {"AND", "OR"}:
            logging.warning("Invalid filter mode '%s' in %s, falling back to AND", mode, str(p))
            mode = "AND"

        def _normalize_rules(section: object) -> dict[str, list[str]]:
            if not isinstance(section, dict):
                return {}
            out: dict[str, list[str]] = {}
            for k, v in section.items():
                key = str(k).strip()
                if not key:
                    continue
                if isinstance(v, list):
                    vals = [str(x).strip() for x in v if str(x).strip()]
                else:
                    vals = [str(v).strip()] if str(v).strip() else []
                if vals:
                    out[key] = vals
            return out

        node_rules = _normalize_rules(cfg.get("nodes"))
        edge_rules = _normalize_rules(cfg.get("edges"))

        if not node_rules and not edge_rules:
            logging.debug("Loaded draw.io filter config but no rules were defined (%s)", str(p))
            return None

        logging.debug(
            "Loaded draw.io filter config: mode=%s, node_rules=%d, edge_rules=%d (%s)",
            mode,
            len(node_rules),
            len(edge_rules),
            str(p),
        )

        return {
            "mode": mode,
            "nodes": node_rules,
            "edges": edge_rules,
        }
    except Exception as e:
        logging.warning("Failed to load DRAWIO_FILTER_CONFIG (%s): %s", str(DRAWIO_FILTER_CONFIG_PATH), e)
        return None


def _matches_filter(obj: dict, rules: dict[str, list[str]], mode: str) -> bool:
    """Return True if obj matches the configured rules under AND/OR semantics."""
    if not rules:
        return True

    results: list[bool] = []
    for field, allowed_values in rules.items():
        raw_value = obj.get(field)
        if raw_value is None:
            results.append(False)
            continue

        value = str(raw_value).strip()
        results.append(value in allowed_values)

    if not results:
        return True

    if mode == "OR":
        return any(results)
    return all(results)


def _apply_export_filters(nodes: list[dict], edges: list[dict], cfg: dict | None) -> tuple[list[dict], list[dict]]:
    """Apply optional export filters to nodes/edges.

    Order:
    1. Filter nodes
    2. Filter edges by configured edge rules
    3. Always drop edges whose source/target no longer exist after node filtering
    """
    if not cfg:
        logging.debug("No export filter config detected — skipping filtering")
        return nodes, edges

    mode = str(cfg.get("mode", "AND")).upper()
    node_rules = cfg.get("nodes") or {}
    edge_rules = cfg.get("edges") or {}

    nodes_before = len(nodes or [])
    edges_before = len(edges or [])

    filtered_nodes = [n for n in (nodes or []) if _matches_filter(n, node_rules, mode)]
    kept_node_ids = {str(n.get("id")) for n in filtered_nodes if n.get("id")}

    filtered_edges: list[dict] = []
    for e in (edges or []):
        if not _matches_filter(e, edge_rules, mode):
            continue

        src = e.get("source")
        tgt = e.get("target")

        # Keep dangling/annotation-only edges only when there are no endpoints at all.
        # If endpoints are present, both sides must survive node filtering.
        if src or tgt:
            if not src or not tgt:
                continue
            if str(src) not in kept_node_ids or str(tgt) not in kept_node_ids:
                continue

        filtered_edges.append(e)

    # Insert clear logging of actual filter effect just before returning
    nodes_after = len(filtered_nodes)
    edges_after = len(filtered_edges)

    logging.debug(
        "    [FILTER APPLIED] mode=%s | nodes %d -> %d | edges %d -> %d",
        mode,
        nodes_before,
        nodes_after,
        edges_before,
        edges_after,
    )

    return filtered_nodes, filtered_edges

# ---------- Helpers ----------

_IMAGE_DATA_RE = re.compile(r"(?:^|;)(image=data:[^;]+)(?=;|$)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")

_MXGRAPH_SNIPPET_RE = re.compile(r"(<mxGraphModel[\s\S]*?</mxGraphModel>)", re.IGNORECASE)
_PLACEHOLDER_TOKEN_RE = re.compile(r"%([^%]+)%")  # matches %Some-Attr%

def _score_mxgraphmodel_xml(mx_xml: str) -> int:
    """Score an mxGraphModel by how much real diagram content it has."""
    try:
        root = ET.fromstring(mx_xml)
    except Exception:
        return 0

    cells = root.findall(".//mxCell")
    if not cells:
        cells = root.findall(".//{*}mxCell")
    text_like = 0
    edge_label = 0
    text_with_parent = 0
    for c in cells:
        style = c.attrib.get("style", "")
        st = (style or "").lower()
        is_text = ("text" in st) or ("edgelabel" in st)
        if is_text:
            text_like += 1
            if "edgelabel" in st:
                edge_label += 1
            if c.attrib.get("parent"):
                text_with_parent += 1
    if not cells:
        return 0

    vertex = sum(1 for c in cells if c.attrib.get("vertex") == "1")
    edge = sum(1 for c in cells if c.attrib.get("edge") == "1")

    # Count cells that have mxGeometry with width/height (actual rendered boxes)
    geom_count = 0
    for c in cells:
        g = c.find("mxGeometry")
        if g is None:
            g = c.find("{*}mxGeometry")
        if g is None:
            continue
        if g.attrib.get("width") is not None or g.attrib.get("height") is not None:
            geom_count += 1

    # Weight vertices and geometry heavily; edges less.
    return vertex * 1000 + geom_count * 100 + edge * 10 + len(cells)


def _find_mxgraphmodels_in_xml(xml_text: str) -> list[str]:
    """Return all mxGraphModel blocks found in a blob of xml text."""
    t = (xml_text or "").strip()
    if not t:
        return []
    # fast regex grab
    hits = _MXGRAPH_SNIPPET_RE.findall(t)
    if hits:
        return hits

    # parse tree fallback
    try:
        root = ET.fromstring(t)
    except Exception:
        return []

    out = []
    for el in root.iter():
        tag = (el.tag or "")
        if tag == "mxGraphModel" or tag.lower().endswith("}mxgraphmodel"):
            out.append(ET.tostring(el, encoding="unicode"))
    return out


def _drill_to_best_mxgraphmodel(xml_text: str) -> str:
    """
    Drill through nested wrappers:
      - <mxGraphModel> ... </mxGraphModel>
      - <mxfile> with <diagram> (text may be compressed)
      - <diagram> wrapper containing compressed payload

    Always returns the BEST mxGraphModel candidate by score.
    """
    t = (xml_text or "").strip()
    if not t:
        return ""

    # Already a model?
    if t.lstrip().startswith("<mxGraphModel"):
        return t

    # If it's XML, try to parse wrappers
    if t.lstrip().startswith("<"):
        try:
            root = ET.fromstring(t)
            tag_lower = (root.tag or "").lower()

            # mxfile wrapper: score each diagram's extracted model
            if tag_lower.endswith("mxfile"):
                best_model = ""
                best_score = 0

                for d in _find_diagrams(root):
                    model = _get_diagram_mxgraph_xml(d)
                    sc = _score_mxgraphmodel_xml(model) if model else 0
                    if sc > best_score:
                        best_score = sc
                        best_model = model

                return best_model

            # diagram wrapper: use shared extraction (handles itertext + decoding)
            if tag_lower.endswith("diagram"):
                return _get_diagram_mxgraph_xml(root)

        except Exception:
            pass

    # Otherwise: find all mxGraphModels inside and pick best by score
    candidates = _find_mxgraphmodels_in_xml(t)
    if candidates:
        return max(candidates, key=_score_mxgraphmodel_xml)

    return ""


def _extract_mxgraphmodel_from_any_xml(xml_text: str) -> str:
    """Return the best standalone <mxGraphModel>...</mxGraphModel> string."""
    return _drill_to_best_mxgraphmodel(xml_text) or ""

def _resolve_placeholders(text: str, attrs: dict) -> str:
    """Resolve draw.io placeholders like %Element-Name% using wrapper attributes."""
    if not text or "%" not in text or not attrs:
        return text or ""

    def repl(m: re.Match) -> str:
        key = (m.group(1) or "").strip()
        if not key:
            return m.group(0)

        # Exact match first
        if key in attrs:
            return str(attrs[key])

        # Common export variations: hyphen <-> underscore
        k1 = key.replace("-", "_")
        if k1 in attrs:
            return str(attrs[k1])

        k2 = key.replace("_", "-")
        if k2 in attrs:
            return str(attrs[k2])

        # No match: keep token visible
        return m.group(0)

    return _PLACEHOLDER_TOKEN_RE.sub(repl, text)

def _clean_label(raw: str) -> str:
    """Convert draw.io HTML-ish label values to plain text."""
    if raw is None:
        return ""

    # draw.io often stores labels as HTML fragments
    text = html.unescape(raw)
    text = text.replace("\u00a0", " ")  # nbsp
    text = text.replace("&nbsp;", " ")
    text = _HTML_TAG_RE.sub(" ", text)
    # collapse whitespace
    return " ".join(text.split()).strip()

def _sanitize_style(style: str) -> str:
    """Remove huge embedded base64 image payloads from draw.io style strings."""
    if not style:
        return ""

    # Replace embedded image data (image=data:...) with a small placeholder.
    # This keeps JSON output manageable while preserving the fact an image was present.
    cleaned = _IMAGE_DATA_RE.sub(";image=<embedded>", style)

    # Normalize potential leading/trailing semicolons introduced by replacement.
    return cleaned.strip(";")

def _safe_text(v: str | None) -> str:
    return (v or "").strip()

def _get_diagram_mxgraph_xml(diagram_elem: _ET.Element) -> str:
    """
    Return the best mxGraphModel for a <diagram> element, regardless of encoding/wrapping.

    IMPORTANT: ElementTree may split the <diagram> payload across multiple text nodes.
    Using itertext() ensures we capture the full compressed/base64 payload.
    """
    # 1) Prefer an embedded mxGraphModel child (uncompressed exports)
    child = diagram_elem.find("./mxGraphModel")
    if child is None:
        child = diagram_elem.find("./{*}mxGraphModel")
    if child is not None:
        child_xml = ET.tostring(child, encoding="unicode")
        best = _extract_mxgraphmodel_from_any_xml(child_xml)
        if best:
            return best

    # 2) Collect full payload text (compressed draw.io exports)
    payload = "".join(diagram_elem.itertext()).strip()
    if not payload:
        return ""

    # 3) Try decode (base64 + raw-deflate)
    try:
        decoded = _decode_drawio_diagram_text(payload)
    except Exception:
        decoded = ""

    best = _extract_mxgraphmodel_from_any_xml(decoded) if decoded else ""
    if best:
        return best

    # 4) Fallback: try extracting directly from raw payload
    return _extract_mxgraphmodel_from_any_xml(payload)



def _maybe_unquote_xml(text: str) -> str:
    """Unquote if the string still looks percent-encoded XML (%3C...%3E)."""
    t = (text or "").strip()
    if not t:
        return ""
    if "%3C" in t or "%3E" in t or "%2F" in t:
        return unquote(t)
    return t

def _try_zlib_decompress(compressed: bytes) -> str | None:
    """Try common draw.io zlib/raw-deflate variants."""
    for wbits in (-15, 15):  # raw DEFLATE, then zlib-wrapped
        try:
            xml_bytes = zlib.decompress(compressed, wbits=wbits)
            decoded = xml_bytes.decode("utf-8", errors="replace")
            decoded = _maybe_unquote_xml(decoded)
            decoded = html.unescape(decoded).strip()
            return decoded
        except Exception:
            continue
    return None

def _decode_drawio_diagram_text(diagram_text: str) -> str:
    """Decode a draw.io <diagram> payload into XML."""
    t = _safe_text(diagram_text)
    if not t:
        return ""

    # Some exports store HTML-escaped XML (e.g. &lt;mxGraphModel ...)
    if "&lt;" in t or "&gt;" in t or "&amp;" in t:
        unescaped = html.unescape(t).strip()
        if unescaped.startswith("<"):
            return unescaped

    # If it already looks like XML, return as-is
    if t.startswith("<"):
        return t

    # Common format: url-escaped base64 of (raw-)deflate-compressed XML
    try:
        b64 = unquote(t)
        compressed = base64.b64decode(b64)
        decoded = _try_zlib_decompress(compressed)
        if decoded is None:
            raise ValueError("Unsupported compression format")
        return decoded
    except Exception as e:
        raise ValueError(f"Failed to decode compressed diagram payload: {e}") from e


def _to_float(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _find_diagrams(mxfile_root: _ET.Element) -> list[_ET.Element]:
    """Find <diagram> elements (namespace-safe)."""
    diagrams = mxfile_root.findall(".//diagram")
    if not diagrams:
        diagrams = mxfile_root.findall(".//{*}diagram")
    return diagrams


def _build_parent_map(root: _ET.Element) -> dict[_ET.Element, _ET.Element]:
    """ElementTree nodes don't have getparent(); build a child->parent map."""
    parent_map: dict[_ET.Element, _ET.Element] = {}
    for p in root.iter():
        for ch in list(p):
            parent_map[ch] = p
    return parent_map


def _extract_wrapped_value_and_style(
    cell: _ET.Element,
    parent_map: dict[_ET.Element, _ET.Element],
) -> tuple[str, str, dict]:
    value_raw = cell.attrib.get("value", "")
    style_raw = cell.attrib.get("style", "")

    wrapper_attrs: dict = {}
    wrapper = parent_map.get(cell)
    if wrapper is None:
        return value_raw, style_raw, wrapper_attrs

    tag_lower = (wrapper.tag or "").lower()

    # Common wrapper tags: UserObject, object (sometimes namespaced)
    if tag_lower.endswith("userobject") or tag_lower.endswith("object"):
        wrapper_attrs = dict(wrapper.attrib)

        # Prefer semantic wrapper attributes when mxCell@value is empty
        preferred = (
            wrapper.attrib.get("Element-Name")
            or wrapper.attrib.get("Element_Name")
            or wrapper.attrib.get("Interface-Name")
            or wrapper.attrib.get("Interface_Name")
            or wrapper.attrib.get("label")
            or wrapper.attrib.get("name")
            or wrapper.attrib.get("text")
            or wrapper.attrib.get("value")
            or ""
        )

        if not (value_raw or "").strip():
            value_raw = preferred

        # Some exports put style on wrapper as well
        if not (style_raw or "").strip():
            style_raw = wrapper.attrib.get("style", "")

        # Resolve placeholders like %Element-Name% anywhere in the final value
        if "%" in (value_raw or ""):
            value_raw = _resolve_placeholders(value_raw, wrapper.attrib)

    return value_raw, style_raw, wrapper_attrs




def _parse_style_kv(style: str) -> dict[str, str]:
    """Parse draw.io style key=value;key=value into a dict (lowercased keys)."""
    out: dict[str, str] = {}
    s = (style or "").strip(";")
    if not s:
        return out
    for part in s.split(";"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out[(k or "").strip().lower()] = (v or "").strip()
        else:
            # boolean-ish style flags like "rounded" may appear without '='
            out[(part or "").strip().lower()] = "1"
    return out

def _style_summary(style: str) -> dict | None:
    """Return a compact subset of style keys useful for debugging."""
    if not style:
        return None
    kv = _parse_style_kv(style)
    out: dict[str, str] = {}

    for k in ("shape", "shapetype", "colorfamily", "dashed", "strokecolor"):
        if k in kv:
            out[k] = kv[k]

    return out or None


# --- Mondrian shapeType helper ---
def _mondrian_shape_type(shape: str | None, style_kv: dict[str, str]) -> str | None:
    """Return shapeType only for Mondrian shapes.

    Ticket requirement:
    - `shape` is always exported when present in style.
    - `shapeType` is only exported when the element uses a Mondrian shape.

    We treat any `shape` containing "mondrian" (case-insensitive) as Mondrian.
    """
    if not shape:
        return None

    if "mondrian" not in shape.lower():
        return None

    st = (style_kv.get("shapetype") or "").strip()
    return st or None


def _infer_kind(layer: str | None) -> str:
    """High-level semantic kind based on the top-level layer/container."""
    if layer == "Tools":
        return "tool"
    if layer == "Functions":
        return "function"
    if layer == "Scope":
        return "scope_item"
    return "node"


# Helper to project wrapper attributes into top_level and data_attributes
def _project_wrapper_attributes(wrapper_attrs: dict | None) -> tuple[dict[str, str], dict[str, str]]:
    """Split wrapper attributes into (top_level_attrs, data_attributes).

    - EXCLUDE_ATTR_KEYS: skipped entirely (case-insensitive)
    - TOP_LEVEL_ATTR_KEYS: lifted to top-level (case-insensitive)
    - everything else: goes into data_attributes

    Values are stringified and whitespace-trimmed; empty values are dropped.
    """
    top_level: dict[str, str] = {}
    data: dict[str, str] = {}

    if not wrapper_attrs:
        return top_level, data

    for k, v in (wrapper_attrs or {}).items():
        k_norm = (k or "").strip()
        if not k_norm:
            continue
        k_lower = k_norm.lower()

        # Exclude/top-level decisions are case-insensitive; output preserves original key.
        if k_lower in EXCLUDE_ATTR_KEYS_LOWER:
            continue

        sv = str(v).strip() if v is not None else ""
        if not sv:
            continue

        if k_lower in TOP_LEVEL_ATTR_KEYS_LOWER:
            top_level[k_norm] = sv
        else:
            data[k_norm] = sv

    return top_level, data


def _public_node(n: dict) -> dict | None:
    """Project an internal node record to a clean, stable public schema."""
    label = (n.get("label") or "").strip()

    # Drop background/placeholder nodes by default (e.g., Mondrian frames)
    if not INCLUDE_PLACEHOLDERS:
        wrapper = n.get("wrapper") or {}
        if not label and wrapper.get("placeholders"):
            return None

    wrapper = n.get("wrapper") or {}
    top_level_attrs, data_attributes = _project_wrapper_attributes(wrapper)

    icon = (wrapper.get("Icon-Name") or wrapper.get("Icon_Name") or "").strip() or None

    containers = n.get("container_path") or []
    layer = containers[0] if containers else None

    # --- Style parsing for shape and shapeType ---
    style = n.get("style") or ""
    style_kv = _parse_style_kv(style)
    shape = (style_kv.get("shape") or "").strip() or None

    # For non-Mondrian draw.io elements, `shape` may be absent in style.
    # Export a stable generic value so consumers can distinguish "no style" from "non-Mondrian".
    if not shape and style.strip():
        shape = "mxcell"

    # shapeType is only available/meaningful for Mondrian shapes
    shape_type = _mondrian_shape_type(shape, style_kv)

    obj = {
        "id": n.get("id"),
        "label": label,
        "element_id": (n.get("element_id") or None),
        "icon": icon,
        "kind": _infer_kind(layer),
        "shape": shape,
        "shapeType": shape_type,
        "containers": containers,
        "layer": layer,
        "data_attributes": data_attributes or None,
    }

    # Merge any lifted attrs into the node top-level (rare; controlled by config)
    for k, v in (top_level_attrs or {}).items():
        if k not in obj:
            obj[k] = v

    if INCLUDE_GEOMETRY:
        obj["geometry"] = n.get("geometry")

    if INCLUDE_DEBUG_STYLE:
        obj["style"] = _style_summary(n.get("style") or "")

    return {k: v for k, v in obj.items() if v not in (None, "", [])}


def _public_edge(e: dict) -> dict:
    """Project an internal edge record to a clean, stable public schema."""
    style = e.get("style") or ""
    kv = _parse_style_kv(style)
    shape = (kv.get("shape") or "").strip() or None

    # For non-Mondrian draw.io connectors, `shape` may be absent in style.
    if not shape and style.strip():
        shape = "mxcell"

    # shapeType is only available/meaningful for Mondrian shapes
    shape_type = _mondrian_shape_type(shape, kv)

    wrapper = e.get("wrapper") or {}
    top_level_attrs, data_attributes = _project_wrapper_attributes(wrapper)

    obj = {
        "id": e.get("id"),
        "source": e.get("source"),
        "target": e.get("target"),
        "label": (e.get("label") or "").strip(),
        "dashed": (kv.get("dashed") == "1"),
        "shape": shape,
        "shapeType": shape_type,
        "kind": "connector",
        "data_attributes": data_attributes or None,
    }

    # Merge any lifted attrs into edge top-level (rare; controlled by config)
    for k, v in (top_level_attrs or {}).items():
        if k not in obj:
            obj[k] = v

    if INCLUDE_DEBUG_STYLE:
        obj["style"] = _style_summary(style)

    return {k: v for k, v in obj.items() if v not in (None, "", [])}


# ---- Outline & Links helpers ----

def _node_outline_key(n: dict) -> tuple:
    """Stable sort key for outline output."""
    return (
        (n.get("layer") or ""),
        (n.get("kind") or ""),
        (n.get("label") or ""),
        (n.get("element_id") or ""),
        (n.get("id") or ""),
    )


def _build_outline(nodes: list[dict]) -> dict:
    """Build a nested outline grouped by container path.

    Output structure:
      {"items": [ {"name": <container>, "items": [...] }, ... ]}
    Leaf items are node dicts with only the public node fields.
    """

    root: dict = {"items": []}

    def get_child_container(parent: dict, name: str) -> dict:
        for it in parent.get("items", []):
            if isinstance(it, dict) and it.get("name") == name and "items" in it:
                return it
        child = {"name": name, "items": []}
        parent.setdefault("items", []).append(child)
        return child

    for n in sorted(nodes or [], key=_node_outline_key):
        containers = n.get("containers") or []
        cur = root
        for c in containers:
            if not c:
                continue
            cur = get_child_container(cur, c)
        cur.setdefault("items", []).append(n)

    return root


def _build_links(nodes: list[dict], edges: list[dict]) -> tuple[dict, list[dict]]:
    """Build adjacency map + list of cross-container links.

    Returns:
      - links: {node_id: {"out": [target_ids], "in": [source_ids]}}
      - cross_container_links: list of edges that connect nodes in different top-level layers
    """

    # Defensive typing for static checkers: never treat inputs as optional.
    node_list: list[dict] = nodes if nodes is not None else []
    edge_list: list[dict] = edges if edges is not None else []

    # Build lookup map for node metadata used in cross-container link enrichment.
    node_by_id: dict[str, dict] = {}
    for n in node_list:
        if not isinstance(n, dict):
            continue
        nid = n.get("id")
        if isinstance(nid, str) and nid:
            node_by_id[nid] = n

    links: dict[str, dict] = {}

    def ensure(nid: str) -> dict:
        if nid not in links:
            links[nid] = {"out": [], "in": []}
        return links[nid]

    def add_unique(lst: list[str], v: str | None) -> None:
        if not v:
            return
        if v not in lst:
            lst.append(v)

    cross: list[dict] = []

    for e in edge_list:
        if not isinstance(e, dict):
            continue

        s = e.get("source")
        t = e.get("target")
        if not isinstance(s, str) or not isinstance(t, str) or not s or not t:
            continue

        ensure(s)
        ensure(t)
        add_unique(links[s]["out"], t)
        add_unique(links[t]["in"], s)

        sn = node_by_id.get(s)
        tn = node_by_id.get(t)
        if sn is None or tn is None:
            continue

        s_layer = sn.get("layer")
        t_layer = tn.get("layer")

        # Cross-container means their top-level layer differs (or one missing)
        if s_layer != t_layer:
            cross.append({
                "id": e.get("id"),
                "label": (e.get("label") or ""),
                "dashed": bool(e.get("dashed", False)),
                "source": {
                    "id": s,
                    "label": (sn.get("label") or ""),
                    "layer": s_layer,
                    "containers": (sn.get("containers") or []),
                },
                "target": {
                    "id": t,
                    "label": (tn.get("label") or ""),
                    "layer": t_layer,
                    "containers": (tn.get("containers") or []),
                },
            })

    # Make adjacency deterministic
    for nid, v in links.items():
        v["out"] = sorted(v["out"])
        v["in"] = sorted(v["in"])

    # Sort cross links for stable diffs
    cross.sort(
        key=lambda x: (
            (x.get("source", {}).get("layer") or ""),
            (x.get("target", {}).get("layer") or ""),
            (x.get("source", {}).get("label") or ""),
            (x.get("target", {}).get("label") or ""),
            (x.get("id") or ""),
        )
    )

    return links, cross

def _is_edge_label_helper_style(style: str) -> bool:
    st = (style or "").lower()
    # draw.io uses edgeLabel children with edgeChild=1/2 as helper vertices.
    return ("edgelabel" in st) and ("edgechild=" in st)


def _is_real_image_style(style: str) -> bool:
    """Return True only if the cell actually references an image."""
    s = (style or "")
    sl = s.lower()

    # Explicit image shape is always an image
    if "shape=image" in sl:
        return True

    kv = _parse_style_kv(s)
    img = (kv.get("image") or "").strip()
    if not img:
        return False

    # Our sanitizer may replace embedded payloads with a marker
    if img == "<embedded>":
        return True

    # Data URIs or file/path refs
    if img.startswith("data:"):
        return True

    # Any other non-empty image ref counts
    return True


def _classify_cell_type(is_vertex: bool, is_edge: bool, style: str) -> str:
    """Classify an mxCell into a high-level type.

    IMPORTANT:
    Many *vertex* cells include the substring "text" in their style (text formatting),
    so we must NOT classify purely on substring matches.

    draw.io label-only cells typically have neither vertex nor edge.
    """
    s = (style or "").lower()

    if is_edge:
        return "edge"

    if is_vertex:
        # images/icons (STRICT)
        if _is_real_image_style(style):
            return "image"
        # containers/groups/swimlanes
        if ("group" in s or "swimlane" in s) and "edge" not in s:
            return "group"
        return "shape"

    # Non-vertex/non-edge: often label/annotation cells
    if "edgelabel" in s:
        return "text"

    # Some exports use explicit text shapes for standalone annotations.
    # Restrict to non-vertex/non-edge only.
    if "text" in s and "shape=" not in s:
        return "text"

    return "shape"


def _classify_node_role(node_type: str, label: str, geometry: dict | None, parent: str | None) -> str:
    # Container/grouping elements
    if node_type == "group":
        return "container"

    # Heuristic: small/no-label shapes are often annotations
    label_clean = (label or "").strip()
    geom = geometry or {}
    w = geom.get("w")
    h = geom.get("h")

    if not label_clean:
        # If geometry looks like a tiny helper node/text
        if (w is not None and w <= 80) or (h is not None and h <= 40):
            return "annotation"

    # If it has no geometry and looks like a placeholder/tab label
    if (w is None and h is None) and parent == "0" and label_clean:
        return "annotation"

    return "primary"



def _derive_display_fields(label_clean: str, wrapper_attrs: dict) -> tuple[str, str, str]:
    """Return (name, element_id, display_label)."""
    name = (wrapper_attrs or {}).get("Element-Name") or (wrapper_attrs or {}).get("Element_Name") or ""
    element_id = (wrapper_attrs or {}).get("Element-ID") or (wrapper_attrs or {}).get("Element_ID") or ""

    name = _clean_label(str(name)) if name else ""
    element_id = _clean_label(str(element_id)) if element_id else ""

    if not name:
        name = (label_clean or "").strip()

    if element_id:
        display = f"{name}\n{element_id}".strip()
    else:
        display = name

    return name, element_id, display



def _is_container_item(item: dict) -> bool:
    return bool(
        item.get("is_layer")
        or item.get("node_role") == "container"
        or item.get("node_type") in ("group", "layer")
    )


def _bbox_contains(container: dict, node: dict) -> bool:
    cg = container.get("geometry") or {}
    ng = node.get("geometry") or {}
    if None in (cg.get("x"), cg.get("y"), cg.get("w"), cg.get("h")):
        return False
    if None in (ng.get("x"), ng.get("y"), ng.get("w"), ng.get("h")):
        return False

    cx1 = cg["x"]
    cy1 = cg["y"]
    cx2 = cx1 + cg["w"]
    cy2 = cy1 + cg["h"]

    nx = ng["x"] + ng["w"] / 2
    ny = ng["y"] + ng["h"] / 2
    return cx1 <= nx <= cx2 and cy1 <= ny <= cy2


def _infer_container_paths(nodes: list[dict], edges: list[dict]) -> None:
    """Populate container_path/container_path_ids for nodes using parent chain + geometry containment."""

    # Build id->item map (nodes + edges for parent traversal safety)
    id_map: dict[str, dict] = {}
    for it in (nodes or []):
        if it.get("id"):
            id_map[it["id"]] = it
    for it in (edges or []):
        if it.get("id"):
            id_map[it["id"]] = it

    # 1) Parent-chain containers
    for node in (nodes or []):
        if _is_container_item(node):
            node["container_path"] = []
            node["container_path_ids"] = []
            continue

        seen: set[str] = set()
        cur_parent = node.get("parent")
        path_labels: list[str] = []
        path_ids: list[str] = []

        for _ in range(60):
            if not cur_parent or cur_parent in ("0", "1"):
                break
            if cur_parent in seen:
                break
            seen.add(cur_parent)

            p = id_map.get(cur_parent)
            if p is None:
                break

            if _is_container_item(p):
                lbl = (p.get("label") or "").strip()
                if lbl:
                    path_labels.insert(0, lbl)
                pid = (p.get("id") or "").strip()
                if pid:
                    path_ids.insert(0, pid)

            cur_parent = p.get("parent")

        node["container_path"] = path_labels
        node["container_path_ids"] = path_ids

    # 2) Geometry containment containers (adds additional container context)
    containers = [
        n
        for n in (nodes or [])
        if _is_container_item(n)
        and n.get("geometry", {}).get("w") is not None
        and n.get("geometry", {}).get("h") is not None
    ]

    for node in (nodes or []):
        if _is_container_item(node):
            node.setdefault("container_path", [])
            node.setdefault("container_path_ids", [])
            continue

        matches = [c for c in containers if _bbox_contains(c, node)]
        matches.sort(key=lambda c: (c["geometry"]["w"] * c["geometry"]["h"]))

        geom_labels = [c["label"] for c in matches if (c.get("label") or "").strip()]
        geom_ids = [c["id"] for c in matches if (c.get("id") or "").strip()]

        merged_labels: list[str] = []
        for x in (node.get("container_path") or []) + geom_labels:
            if x and x not in merged_labels:
                merged_labels.append(x)

        merged_ids: list[str] = []
        for x in (node.get("container_path_ids") or []) + geom_ids:
            if x and x not in merged_ids:
                merged_ids.append(x)

        node["container_path"] = merged_labels
        node["container_path_ids"] = merged_ids


def _parse_mxgraphmodel(mx_xml: str) -> dict:
    """
    Parse a single <mxGraphModel> XML string into nodes/edges.

    IMPORTANT:
    draw.io stores diagram structure as a flat list of mxCell entries.
    Hierarchy is encoded via mxCell@parent, and visible labels are often stored
    as separate text cells whose parent points to the owning vertex/edge.

    Strategy:
      1) Build a full id->cell record map for ALL mxCells.
      2) Attach text/edgeLabel children to their owning parent (edge or vertex).
      3) Emit edges from edge="1" cells, and nodes from non-edge element cells.
         Drop label-only text cells once attached.
    """
    if not mx_xml:
        return {"nodes": [], "edges": []}

    mx_xml = _extract_mxgraphmodel_from_any_xml(mx_xml)
    root = ET.fromstring(mx_xml)

    cells = root.findall(".//mxCell")
    if not cells:
        cells = root.findall(".//{*}mxCell")

    parent_map = _build_parent_map(root)

    # ---------- Pass 1: build full cell map ----------
    id_map: dict[str, dict] = {}

    for c in cells:
        cid = c.attrib.get("id")

        # In many draw.io exports, the wrapper (<UserObject>/<object>) has the id,
        # while the nested <mxCell> has no id.
        if not cid:
            wrapper = parent_map.get(c)
            if wrapper is not None:
                cid = wrapper.attrib.get("id")

        if not cid:
            continue

        value_raw, style_raw, wrapper_attrs = _extract_wrapped_value_and_style(c, parent_map)
        value_clean = _clean_label(value_raw)
        style = _sanitize_style(style_raw)

        parent = c.attrib.get("parent")
        source = c.attrib.get("source")
        target = c.attrib.get("target")

        geom = c.find("mxGeometry")
        if geom is None:
            geom = c.find("{*}mxGeometry")
        x = y = w = h = None
        if geom is not None:
            x = geom.attrib.get("x")
            y = geom.attrib.get("y")
            w = geom.attrib.get("width")
            h = geom.attrib.get("height")

        geometry_out = {"x": _to_float(x), "y": _to_float(y), "w": _to_float(w), "h": _to_float(h)}

        is_edge = c.attrib.get("edge") == "1"
        is_vertex = c.attrib.get("vertex") == "1"

        node_type = _classify_cell_type(is_vertex=bool(is_vertex), is_edge=bool(is_edge), style=style)
        node_role = _classify_node_role(node_type, value_clean, geometry_out, parent)

        # Derive semantic label + element id (from wrapper attrs when available)
        name, element_id, _ = _derive_display_fields(value_clean, wrapper_attrs)


        rec = {
            "id": cid,
            "label": name,
            "element_id": element_id or None,
            "style": style,
            "wrapper": wrapper_attrs or None,
            "parent": parent,
            "source": source,
            "target": target,
            "is_edge": bool(is_edge),
            "is_vertex": bool(is_vertex),
            "node_type": node_type,
            "node_role": node_role,
            "geometry": geometry_out,
            "is_layer": False,
            # internal-only (not emitted)
            "_raw_value": value_raw,
        }

        id_map[cid] = rec

    # ---------- Pass 2: detect layers ----------
    for cid, rec in id_map.items():
        if rec.get("is_edge"):
            continue
        if cid in ("0", "1"):
            continue

        parent = rec.get("parent")
        geom = rec.get("geometry") or {}
        is_layer = (
            parent == "0"
            and bool((rec.get("label") or "").strip())
            and geom.get("x") is None and geom.get("y") is None
            and geom.get("w") is None and geom.get("h") is None
        )
        if is_layer:
            rec["is_layer"] = True
            rec["node_type"] = "layer"
            rec["node_role"] = "container"

    # ---------- Pass 3: attach child label cells to their owning vertex/edge ----------
    # draw.io often stores the visible label as a separate mxCell whose @parent points to the owning shape/edge.
    # Some exports do NOT include explicit "text" in style, so we also treat non-vertex/non-edge children
    # with a label and a parent that IS a vertex/edge as label candidates.
    consumed_label_cell_ids: set[str] = set()

    def _is_placeholder_label(lbl: str) -> bool:
        t = (lbl or "").strip()
        return ("%" in t) and (t.count("%") >= 2)

    def _attach_label_if_needed(parent_id: str, label: str, label_raw: str) -> None:
        if not parent_id or parent_id not in id_map:
            return
        if not label:
            return
        p = id_map[parent_id]

        # Overwrite parent label if it's empty OR clearly a placeholder token string
        cur = (p.get("label") or "").strip()
        if (not cur) or _is_placeholder_label(cur):
            p["label"] = label
            p["_raw_value"] = label_raw
            # Recompute display fields now that label changed
            wrapper_attrs = p.get("wrapper") or {}
            name, element_id, _display_label = _derive_display_fields(label, wrapper_attrs)
            p["label"] = name
            p["element_id"] = element_id or None

    def _is_label_candidate(cell_rec: dict) -> bool:
        if not cell_rec:
            return False
        style = (cell_rec.get("style") or "")
        st = style.lower()
        is_edgelabel = "edgelabel" in st

        if cell_rec.get("is_edge"):
            return False
        if cell_rec.get("is_vertex") and not is_edgelabel:
            return False

        parent_id = cell_rec.get("parent")
        if not parent_id or parent_id not in id_map:
            return False
        if parent_id in ("0", "1"):
            return False
        label = (cell_rec.get("label") or "").strip()
        if not label:
            # Empty edgeLabel helper vertices exist; they are not content labels.
            # We do not attach them (nothing to attach), but we also shouldn't keep them as nodes.
            return False

        if "swimlane" in st or "group" in st:
            return False
        # Explicit text/edgeLabel styles are label cells (edgeLabel can be vertex="1" in draw.io).
        if is_edgelabel:
            return True
        if ("text" in st) or ("edgelabel" in st):
            return True

        # If the parent is a container/layer, don't auto-consume this child as a label cell.
        p = id_map.get(parent_id) or {}
        if p.get("node_role") == "container" or p.get("node_type") in ("layer", "group"):
            return False

        # Heuristic: if the parent is a vertex/edge, this is very likely a label-only child.
        if p.get("is_edge") or p.get("is_vertex"):
            return True

        return False

    # Attach all label candidates to their parent and mark them for exclusion from output.
    for cid, rec in id_map.items():
        if cid in ("0", "1"):
            continue
        if not _is_label_candidate(rec):
            continue

        parent_id = rec.get("parent") or ""
        if not isinstance(parent_id, str) or not parent_id:
            continue

        label = (rec.get("label") or "").strip()
        label_raw = rec.get("_raw_value") or ""
        style = rec.get("style") or ""

        # If it is an edgeLabel OR the parent is an edge, attach to the edge.
        parent_rec = id_map.get(parent_id)
        if parent_rec and (parent_rec.get("is_edge") or ("edgelabel" in (style or "").lower())):
            cur_edge_label = (parent_rec.get("label") or "").strip()
            if (not cur_edge_label) or _is_placeholder_label(cur_edge_label):
                parent_rec["label"] = label
                parent_rec["_raw_value"] = label_raw

            consumed_label_cell_ids.add(cid)
            continue

        # Otherwise, attach to vertex/group/layer if parent label is empty or placeholder.
        _attach_label_if_needed(parent_id, label, label_raw)
        consumed_label_cell_ids.add(cid)

    # ---------- Emit nodes and edges ----------
    nodes: list[dict] = []
    edges: list[dict] = []

    for cid, rec in id_map.items():
        if cid in ("0", "1"):
            continue

        # Drop edgeLabel helper vertices (edgeChild=1/2). They are routing/label positioning helpers, not diagram content.
        if _is_edge_label_helper_style(rec.get("style") or ""):
            continue

        if rec.get("is_edge"):
            edges.append({
                "id": rec["id"],
                "label": rec.get("label") or "",
                "style": rec.get("style") or "",
                "parent": rec.get("parent"),
                "source": rec.get("source"),
                "target": rec.get("target"),
                "wrapper": rec.get("wrapper") or None,
            })
            continue

        # Drop label-only cells after attaching (many exports use style without explicit "text")
        if cid in consumed_label_cell_ids:
            continue

        geom = rec.get("geometry") or {}
        has_geom = any(geom.get(k) is not None for k in ("x", "y", "w", "h"))
        has_content = bool((rec.get("label") or "").strip()) or bool((rec.get("style") or "").strip())

        if not (rec.get("is_vertex") or has_geom or has_content):
            continue

        node_type = rec.get("node_type")
        node_role = rec.get("node_role")
        if rec.get("is_layer"):
            node_type = "layer"
            node_role = "container"

        nodes.append({
            "id": rec["id"],
            "label": rec.get("label") or "",
            "element_id": rec.get("element_id"),
            "style": rec.get("style") or "",
            "wrapper": rec.get("wrapper"),
            "parent": rec.get("parent"),
            "node_type": node_type,
            "node_role": node_role,
            "geometry": rec.get("geometry") or {"x": None, "y": None, "w": None, "h": None},
            "is_layer": bool(rec.get("is_layer")),
        })

    _infer_container_paths(nodes, edges)

    public_nodes: list[dict] = []
    for n in nodes:
        pn = _public_node(n)
        if pn is not None:
            public_nodes.append(pn)

    public_edges = [_public_edge(e) for e in edges]

    return {"nodes": public_nodes, "edges": public_edges}

def extract_drawio_file(path: Path, relpath: Path) -> dict | None:
    """
    Returns JSON structure for one .drawio file with pages.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        tree = ET.fromstring(content)
    except Exception as e:
        raise ValueError(f"XML parse error: {e}") from e

    # draw.io root is usually <mxfile> with one or more <diagram> pages
    if not ((tree.tag or "").lower().endswith("mxfile")):
        # Some exports may already be mxGraphModel
        if (tree.tag or "").lower().endswith("mxgraphmodel"):
            parsed = _parse_mxgraphmodel(content)
            nodes = parsed.get("nodes", []) or []
            edges = parsed.get("edges", []) or []

            nodes, edges = _apply_export_filters(nodes, edges, FILTER_CONFIG)

            links, cross = _build_links(nodes, edges)
            outline = _build_outline(nodes)

            return {
                "source": str(relpath),
                "summary": {
                    "pages": 1,
                    "nodes": len(nodes),
                    "edges": len(edges),
                },
                "pages": [
                    {
                        "page_index": 0,
                        "page_name": "default",
                        "summary": {"nodes": len(nodes), "edges": len(edges)},
                        "nodes": nodes,
                        "edges": edges,
                        "outline": outline,
                        "links": links,
                        "cross_container_links": cross,
                    }
                ],
            }
        return None

    pages = []

    # draw.io typically uses <diagram> children under <mxfile>. Some files may include namespaces,
    # so we try both the plain and namespace-agnostic patterns.
    diagrams = _find_diagrams(tree)

    for idx, d in enumerate(diagrams):
        page_name = d.attrib.get("name") or "page"
        logging.debug("[PAGE] %s", page_name)
        mx_xml = _get_diagram_mxgraph_xml(d)
        parsed = _parse_mxgraphmodel(mx_xml)
        nodes = parsed.get("nodes", []) or []
        edges = parsed.get("edges", []) or []

        nodes, edges = _apply_export_filters(nodes, edges, FILTER_CONFIG)

        links, cross = _build_links(nodes, edges)
        outline = _build_outline(nodes)

        pages.append({
            "page_index": idx,
            "page_name": page_name,
            "summary": {"nodes": len(nodes), "edges": len(edges)},
            "nodes": nodes,
            "edges": edges,
            "outline": outline,
            "links": links,
            "cross_container_links": cross,
        })

    total_nodes = sum(len(p.get("nodes", []) or []) for p in pages)
    total_edges = sum(len(p.get("edges", []) or []) for p in pages)

    return {
        "source": str(relpath),
        "summary": {
            "pages": len(pages),
            "nodes": total_nodes,
            "edges": total_edges,
        },
        "pages": pages,
    }


def main() -> int:
    produced = 0
    scanned = 0
    errors = 0
    out = []

    logging.debug("Draw.io extraction started (glob=%s)", DRAWIO_GLOB)

    _load_attr_projection_config()

    global FILTER_CONFIG
    FILTER_CONFIG = _load_filter_config()

    for path in sorted(ROOT.rglob(DRAWIO_GLOB)):
        if not path.is_file():
            continue
        scanned += 1
        relpath = path.relative_to(ROOT)

        try:
            data = extract_drawio_file(path, relpath)
            if not data:
                continue
            out.append(data)
            produced += 1
            logging.info("[FILE] %s", relpath)
        except Exception as e:
            errors += 1
            logging.warning("Failed: %s (%s)", e, relpath)

    out_file = OUTDIR / "diagram_elements_broker.json"
    out_file.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    logging.info("Wrote %d files -> %s", produced, out_file)

    if produced == 0:
        logging.warning("No draw.io diagrams found.")

    logging.info("Finished: produced=%d, files_scanned=%d, errors=%d, strict=%s",
                 produced, scanned, errors, "on" if STRICT else "off")

    if STRICT and (errors > 0 or produced == 0):
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())