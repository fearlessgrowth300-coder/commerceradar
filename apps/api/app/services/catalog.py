"""Store catalog profile (M22): language, product dates, product formats, category.

Sources (all public, robots-checked, fetched by the product run):
  - homepage HTML: <html lang>, og:locale; app script signatures (POD / COD / dropshipping apps)
  - Shopify /products.json (≤ CATALOG_PAGES × 250 products): created_at, product_type, tags, titles,
    variants[].requires_shipping
Observed facts: language, first/latest product date, published product count, digital (no shipping).
Inferred (labelled as such in the UI): category, POD, COD, dropshipping.
"""

import json
import re
from collections import Counter
from datetime import UTC, datetime
from urllib.parse import urljoin

from app.models import Store
from app.security.ssrf import BlockedDestination, DnsFailure
from app.services.fetcher import FetchError, SafeFetcher

CATALOG_PAGES = 2  # 500 products: enough for dates/category; the oldest product may be beyond this (noted in UI)

# The team's category list: group -> subcategory -> keywords (English + common Spanish/French/German/Portuguese/Italian).
TAXONOMY: dict[str, dict[str, list[str]]] = {
    "Automotive": {
        "Car Accessories": ["car accessories", "car accessory", "car mount", "seat cover", "steering wheel", "dash cam", "car charger", "car organizer", "accesorios auto", "autozubehör", "accessoires auto"],
        "Motorcycle Gear": ["motorcycle", "motorbike", "moto", "helmet", "biker", "casco moto"],
        "Spare Parts": ["spare part", "auto parts", "car parts", "brake", "headlight", "exhaust", "repuesto", "ersatzteil", "pièces auto", "wheel rim", "tyre", "tire"],
        "Car Care": ["car care", "car wash", "car polish", "detailing", "car wax", "ceramic coating"],
    },
    "Electronics & Accessories": {
        "Computers & Laptops": ["laptop", "computer", "notebook pc", "desktop pc", "monitor", "keyboard", "mouse", "gpu", "ordenador", "portátil", "ordinateur"],
        "Mobile Devices": ["smartphone", "phone", "iphone", "android", "tablet", "ipad", "móvil", "celular", "handy", "téléphone"],
        "Appliances": ["appliance", "vacuum", "blender", "air fryer", "coffee machine", "microwave", "refrigerator", "washing machine", "electrodoméstico", "haushaltsgerät"],
        "Photography & Videography": ["camera", "lens", "tripod", "drone", "gimbal", "photography", "cámara", "kamera", "objectif"],
        "Tech Accessories": ["phone case", "charger", "charging cable", "power bank", "earbuds", "headphones", "headset", "speaker", "smartwatch", "usb", "screen protector", "electronic accessories", "funda", "cargador", "auriculares", "ladekabel", "kopfhörer"],
    },
    "Pet Supplies": {
        "Pet Food": ["dog food", "cat food", "pet food", "kibble", "pet treats", "dog treats", "comida para perro", "hundefutter", "katzenfutter", "croquettes"],
        "Pet Toys": ["dog toy", "cat toy", "pet toy", "chew toy", "juguete para perro", "hundespielzeug"],
        "Pet Health": ["pet health", "flea", "tick", "pet supplement", "dog shampoo", "pet grooming", "pet bed", "dog bed", "cat tree", "cat litter", "litter box"],
        "Pet Clothing": ["dog clothes", "dog sweater", "dog jacket", "pet clothing", "dog collar", "dog leash", "harness", "dog harness"],
    },
    "Personal Care": {
        "Health Products": ["supplement", "vitamin", "protein", "health", "wellness", "massager", "posture", "pain relief", "suplemento", "vitamina", "nahrungsergänzung"],
        "Skincare & Beauty": ["skincare", "skin care", "serum", "moisturizer", "cream", "lotion", "makeup", "make-up", "lipstick", "mascara", "eyelash", "lashes", "nail", "perfume", "fragrance", "cosmetic", "beauty", "belleza", "maquillaje", "crema", "beauté", "kosmetik", "hautpflege"],
        "Grooming Tools": ["razor", "shaver", "trimmer", "hair dryer", "straightener", "curler", "hair brush", "beard", "grooming", "hair care", "shampoo", "conditioner", "wig", "hair extension", "aseo", "barba"],
    },
    "Products for Home": {
        "Kitchenware": ["kitchen", "cookware", "knife", "frying pan", "utensil", "mug", "cup", "plate", "bakeware", "cocina", "küche", "cuisine", "cozinha"],
        "Furniture": ["sofa", "chair", "table", "bed frame", "mattress", "wardrobe", "shelf", "cabinet", "furniture", "mueble", "möbel", "meuble"],
        "Home Improvement": ["tool", "drill", "lighting", "lamp", "light bulb", "led strip", "bathroom", "faucet", "decor", "home decor", "wall art", "rug", "curtain", "candle", "bedding", "pillow", "towel", "storage", "decoración", "hogar", "wohnen", "maison", "décoration"],
        "Outdoor Essentials": ["garden", "outdoor", "camping", "tent", "bbq", "grill", "patio", "plant", "planter", "jardín", "garten", "jardin"],
    },
    "Grocery & Gourmet Food": {
        "Food & Beverages": ["coffee", "tea", "chocolate", "snack", "sauce", "spice", "honey", "wine", "beer", "juice", "food", "beverage", "drink", "gourmet", "café", "comida", "bebida", "lebensmittel", "nourriture", "bio", "organic"],
    },
    "Hobby": {
        "Books & Literature": ["book", "novel", "paperback", "hardcover", "libro", "buch", "livre"],
        "Arts & Crafts": ["craft", "paint", "canvas", "yarn", "knitting", "sewing", "embroidery", "bead", "sticker", "art print", "painting", "manualidades", "basteln"],
        "Sports Gear": ["sport", "fitness", "gym", "yoga", "running", "cycling", "bike", "football", "soccer", "basketball", "tennis", "golf", "fishing", "hunting", "dumbbell", "deporte", "deportes", "fútbol"],
        "Games & Toys": ["toy", "game", "puzzle", "board game", "lego", "doll", "action figure", "plush", "rc car", "video game", "juguete", "juego", "spielzeug", "jouet"],
    },
    "Baby": {
        "Baby Clothing": ["baby clothes", "onesie", "romper", "bodysuit", "baby outfit", "ropa de bebé", "babykleidung"],
        "Cribs & Strollers": ["crib", "stroller", "pram", "car seat", "baby carrier", "cuna", "carrito", "kinderwagen"],
        "Baby Care": ["diaper", "nappy", "baby bottle", "pacifier", "teether", "baby care", "nursing", "pañal", "biberón", "bebé", "baby"],
        "Toys for Babies": ["baby toy", "rattle", "teething toy", "play mat", "montessori"],
    },
    "Clothing & Shoes & Accessories": {
        "Clothing": ["t-shirt", "tshirt", "tee", "shirt", "hoodie", "sweatshirt", "dress", "jeans", "pants", "shorts", "skirt", "jacket", "coat", "sweater", "leggings", "lingerie", "swimwear", "bikini", "apparel", "clothing", "fashion", "streetwear", "ropa", "camiseta", "vestido", "kleidung", "vêtements", "roupa"],
        "Shoes": ["shoe", "shoes", "sneaker", "sneakers", "boot", "boots", "sandal", "heels", "slipper", "loafer", "zapato", "zapatillas", "schuhe", "chaussures", "berufsschuhe"],
        "Jewelry & Accessories": ["jewelry", "jewellery", "necklace", "bracelet", "earring", "earrings", "ring", "watch", "sunglasses", "handbag", "bag", "backpack", "wallet", "belt", "hat", "cap", "scarf", "joyería", "collar", "pendientes", "schmuck", "bijoux", "accessories"],
    },
    "Office Products": {
        "Stationery": ["stationery", "notebook", "planner", "journal", "pen", "pencil", "paper", "notepad", "papelería", "schreibwaren", "papeterie"],
        "Furniture": ["office chair", "desk", "standing desk", "office furniture", "filing cabinet"],
        "Tech Accessories": ["printer", "ink", "toner", "laptop stand", "monitor arm", "desk organizer", "webcam"],
    },
    "Digital Products": {
        "E-Books & Audiobooks": ["ebook", "e-book", "audiobook", "pdf guide", "digital download"],
        "Magazines & Newspapers": ["magazine", "newspaper", "revista", "zeitschrift"],
        "Online Courses & Tutorials": ["course", "masterclass", "tutorial", "coaching", "workshop", "training", "curso", "kurs", "formation"],
        "Media & Creative Assets": ["preset", "lightroom", "template", "mockup", "font", "svg", "printable", "canva", "stock photo", "overlay", "lut"],
        "Software & Applications": ["software", "plugin", "license key", "saas", "extension"],
        "Music & Audio": ["sample pack", "drum kit", "vinyl", "music cd", "music", "album", "música", "musik", "musique"],
        "Subscriptions & Licenses": ["subscription", "membership", "license", "gift card", "suscripción", "abo"],
    },
}
# Plain one-word matches like "dog" or "bag" are weak on their own; these lists are deliberately broad and the
# result is shown as inferred with a confidence. ponytail: keyword rules; swap for an embedding model if accuracy needs it.

_PATTERNS = {
    (group, sub): re.compile(r"(?<![a-z])(" + "|".join(re.escape(k) for k in sorted(kws, key=len, reverse=True)) + r")s?(?![a-z])")
    for group, subs in TAXONOMY.items()
    for sub, kws in subs.items()
}
# Generic words that point to a group but not to one of its subcategories.
GROUP_WORDS = {
    "Pet Supplies": ["dog", "cat", "pet", "puppy", "kitten", "perro", "gato", "mascota", "hund", "katze", "chien"],
    "Baby": ["baby", "bebé", "bébé", "newborn", "toddler"],
    "Electronics & Accessories": ["electronics", "electronic", "gadget", "gadgets"],
    "Automotive": ["automotive", "car", "truck", "vehicle"],
    "Products for Home": ["home", "household", "hogar", "maison", "zuhause"],
    "Clothing & Shoes & Accessories": ["wear", "outfit", "boutique"],
}
_GROUP_PATTERNS = {
    group: re.compile(r"(?<![a-z])(" + "|".join(re.escape(k) for k in sorted(kws, key=len, reverse=True)) + r")s?(?![a-z])")
    for group, kws in GROUP_WORDS.items()
}

FORMAT_SIGNATURES = {
    "pod": ["printful", "printify", "gelato", "teelaunch", "customcat", "gooten", "apliiq", "subliminator", "shineon", "print on demand"],
    "cod": ["releasit", "easysell", "cod form", "codform", "cash on delivery", "contra reembolso", "pago contra entrega", "paiement à la livraison"],
    "dropshipping": ["dsers", "cjdropshipping", "cj dropshipping", "zendrop", "autods", "spocket", "syncee", "oberlo", "dropshipman", "alidropship"],
}

_LANG = re.compile(r"<html[^>]*?\blang\s*=\s*[\"']?([A-Za-z]{2})(?:[-_][A-Za-z]{2,4})?", re.I)
_OG_LOCALE = re.compile(r"<meta[^>]+property=[\"']og:locale[\"'][^>]+content=[\"']([A-Za-z]{2})", re.I)


def language(html: str) -> str | None:
    m = _LANG.search(html[:20000]) or _OG_LOCALE.search(html[:50000])
    return m.group(1).lower() if m else None


def formats(html: str, products: list[dict]) -> dict[str, bool]:
    """POD/COD/dropshipping: app or wording signatures in the homepage HTML or product tags/types (inferred).
    Digital: most published products need no shipping (observed in /products.json)."""
    text = (html or "").lower() + " " + " ".join(
        " ".join([str(p.get("product_type") or ""), " ".join(_tags(p)), str(p.get("vendor") or "")]) for p in products
    ).lower()
    out = {k: any(s in text for s in sigs) for k, sigs in FORMAT_SIGNATURES.items()}
    with_variants = [p for p in products if p.get("variants")]
    digital = sum(1 for p in with_variants if not any(v.get("requires_shipping", True) for v in p["variants"]))
    out["digital"] = bool(with_variants) and digital / len(with_variants) >= 0.5
    return out


def _tags(p: dict) -> list[str]:
    t = p.get("tags") or []
    return [s.strip() for s in t.split(",")] if isinstance(t, str) else [str(s) for s in t]


def classify(store_name: str | None, products: list[dict]) -> tuple[str | None, str | None, float]:
    """→ (group, subcategory, confidence 0..1). Weighted keyword hit counts: product types 3, tags 2, titles 1, name 2."""
    scores: Counter = Counter()  # (group, subcategory) -> weight
    extra: Counter = Counter()  # group-only words

    def add(text: str, weight: float) -> None:
        t = text.lower()
        if not t.strip():
            return
        for key, pat in _PATTERNS.items():
            if n := len(pat.findall(t)):
                scores[key] += weight * n
        for g, pat in _GROUP_PATTERNS.items():
            if n := len(pat.findall(t)):
                extra[g] += weight * n

    if store_name:
        add(store_name, 2)
    # one pass per text kind (not per product): each pattern runs 3 times per store, not 3 x products
    add(" | ".join(str(p.get("product_type") or "") for p in products), 3)
    add(" | ".join(" | ".join(_tags(p)) for p in products), 2)
    add(" | ".join(str(p.get("title") or "") for p in products), 1)
    groups: Counter = Counter()
    for (g, _), v in scores.items():
        groups[g] += v
    for g, v in extra.items():
        groups[g] += v
    if not groups:
        return None, None, 0.0
    group, top = groups.most_common(1)[0]
    if top < 3:  # one weak hit is not enough to label a store
        return None, None, 0.0
    subs = [(s, v) for (g, s), v in scores.items() if g == group]
    sub = max(subs, key=lambda x: x[1]) if subs else None
    return group, (sub[0] if sub and sub[1] >= 2 else None), round(top / sum(groups.values()), 2)


def _ts(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


def summarize(products: list[dict]) -> dict:
    created = sorted(t for t in (_ts(p.get("created_at")) for p in products) if t)
    return {"published_products": len(products), "first_product_at": created[0] if created else None,
            "latest_product_at": created[-1] if created else None}  # fmt: skip


def shopify_products(fetcher: SafeFetcher, base: str, robots, ua: str, pages: int = CATALOG_PAGES) -> list[dict] | None:
    """Public /products.json, newest first by Shopify's default order is not guaranteed; we read up to `pages` pages."""
    url = urljoin(base, "/products.json")
    if not robots.can_fetch(ua, url):
        return None
    out: list[dict] = []
    for page in range(1, pages + 1):
        try:
            r = fetcher.fetch(f"{url}?limit=250&page={page}")
        except (FetchError, BlockedDestination, DnsFailure):
            break
        if r.status != 200:
            break
        try:
            batch = json.loads(r.text).get("products") or []
        except (ValueError, AttributeError):
            break
        out += [p for p in batch if isinstance(p, dict)]
        if len(batch) < 250:
            break
    return out


def update_catalog(store: Store, fetcher: SafeFetcher, base: str, robots, ua: str) -> bool:
    """Refresh the store's catalog profile. One homepage request (+ /products.json pages for Shopify)."""
    html = ""
    if robots.can_fetch(ua, base):
        try:
            home = fetcher.fetch(base)
            if home.status < 400 and "html" in home.content_type:
                html = home.text
        except (FetchError, BlockedDestination, DnsFailure):
            pass
    products = (shopify_products(fetcher, base, robots, ua) or []) if store.primary_platform == "shopify" else []
    if not html and not products:
        return False
    store.language = language(html) if html else store.language
    if products:
        s = summarize(products)
        store.published_products, store.first_product_at, store.latest_product_at = s["published_products"], s["first_product_at"], s["latest_product_at"]
    f = formats(html, products)
    store.is_pod, store.is_cod, store.is_dropshipping, store.is_digital = f["pod"], f["cod"], f["dropshipping"], f["digital"]
    group, sub, conf = classify(store.store_name, products)
    store.category_group, store.category, store.category_confidence = group, sub, conf or None
    store.catalog_checked_at = datetime.now(UTC)
    return True
