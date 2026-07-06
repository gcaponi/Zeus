from apps.companies.dna_schemas import LAYER_KEYS, LAYER_TITLES, PRODUCT_LAYER_KEYS, PRODUCT_LAYER_TITLES


def _text(value) -> str:
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if isinstance(item, list):
                parts.append(f"{key}: " + "; ".join(str(v) for v in item if v))
            elif item:
                parts.append(f"{key}: {item}")
        return "\n".join(parts).strip()
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value if item).strip()
    return str(value or "").strip()


def render_sintesi_cognitiva(content: dict, title: str, *, product: bool = False) -> str:
    """Render a 6-layer DNA payload into stable Markdown for publication/export."""
    layer_keys = PRODUCT_LAYER_KEYS if product else LAYER_KEYS
    titles = PRODUCT_LAYER_TITLES if product else LAYER_TITLES
    lines = [f"# {title}", ""]
    synthesis = _text(content.get("sintesi_cognitiva", ""))
    if synthesis:
        lines.extend(["## Sintesi Cognitiva", "", synthesis, ""])
    for key in layer_keys:
        value = _text(content.get(key, ""))
        if not value:
            continue
        lines.extend([f"## {titles[key]}", "", value, ""])
    return "\n".join(lines).strip() + "\n"


def render_product_publication(product, product_dna, channel: str) -> str:
    heading = f"{product.name} — DNA Specialista"
    body = render_sintesi_cognitiva(product_dna.content or {}, heading, product=True)
    meta = [
        "---",
        f"channel: {channel}",
        f"product_id: {product.pk}",
        f"product_dna_id: {product_dna.pk}",
        f"product_dna_version: {product_dna.version}",
        "---",
        "",
    ]
    return "\n".join(meta) + body
