Synthetic HTML fixtures (hand-written, no real sites). Served through httpx.MockTransport in tests.

- `store_home.html`: store homepage with a cart link, add-to-cart forms, product links and prices. No JSON-LD.
- `store_product.html`: product page with Product + Offer JSON-LD.
- `blog.html`: blog that talks about "shop", "products" and "cart" in prose. Must NOT classify as ecommerce.
- `catalogue.html`: product links + prices + cart link, no add-to-cart and no structured data. Expected: probable.
