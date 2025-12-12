import os
import csv
import time
import re
import statistics
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "real-time-sephora-api.p.rapidapi.com")
BASE_URL = f"https://{RAPIDAPI_HOST}"

HEADERS = {
    "X-RapidAPI-Key": RAPIDAPI_KEY,
    "X-RapidAPI-Host": RAPIDAPI_HOST,
}

# -----------------------------
# 1. API CALL HELPERS
# -----------------------------

def call_api(path: str, params: dict):
    """Generic helper to call the Sephora API with error handling."""
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"API error {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def fetch_products_for_search_term(search_term: str, max_pages: int = 3):
    """
    Fetch a list of products for a given search term.
    Returns a list of dicts with at least product id + rating.
    """
    products = []

    # TODO: Adjust this to the "list/search" endpoint from docs.
    # Example: path = "/products-search" or "/products"
    LIST_ENDPOINT_PATH = "/product-search"   # <-- Replace with actual path from RapidAPI docs

    for page in range(1, max_pages + 1):
        params = {
            # TODO: adjust to match API docs for this endpoint
            "q": search_term,     # search query
            "page": page,
        }
        data = call_api(LIST_ENDPOINT_PATH, params)

        # TODO: Adjust this depending on how the API returns items
        items = data.get("products") or data.get("items") or []
        if not items:
            break

        for p in items:
            # Adjust field names based on API response shape
            product_id = p.get("id") or p.get("productId")
            name = p.get("name")
            rating = p.get("rating") or p.get("averageRating")
            reviews_count = p.get("reviewsCount") or p.get("reviewCount")

            if product_id and rating is not None:
                products.append({
                    "id": product_id,
                    "name": name,
                    "rating": float(rating),
                    "reviews_count": reviews_count,
                })

        # be nice with rate limits
        time.sleep(0.5)

    return products


def fetch_product_details(product_id: str):
    """
    Fetch full details for a single product (including ingredients).
    """
    # TODO: Replace with the actual product-details endpoint from docs.
    DETAILS_ENDPOINT_PATH = "/product-details"   # e.g. docs show something like this

    params = {
        # TODO: replace param name with what docs say (e.g. "productId")
        "productId": product_id
    }

    data = call_api(DETAILS_ENDPOINT_PATH, params)

    # Adjust these according to response shape
    ingredients = data.get("ingredients") or data.get("ingredientList")
    # Sometimes details endpoint also includes rating:
    rating = data.get("rating") or data.get("averageRating")

    return {
        "ingredients": ingredients,
        "rating": rating,
        "raw": data,
    }

# -----------------------------
# 2. INGREDIENT ANALYSIS
# -----------------------------

def normalize_ingredient_string(ingredient_str: str):
    """
    Turn one big ingredient string into a list of normalized ingredient tokens.
    Assumes ingredients are comma-separated or semicolon-separated.
    """
    if not ingredient_str:
        return []

    # Split on commas, semicolons, parentheses, etc.
    parts = re.split(r"[;,/()]+", ingredient_str)
    cleaned = []
    for part in parts:
        token = part.strip().lower()
        # Remove extra spaces/punctuation at the ends
        token = re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", token)
        if len(token) > 2:
            cleaned.append(token)
    return cleaned


def build_ingredient_stats(products_with_details):
    """
    products_with_details: list of dicts with keys:
      - id, name, rating, ingredients (string)
    Returns dict: ingredient -> stats
    """
    ingredient_to_ratings = defaultdict(list)

    for p in products_with_details:
        rating = p.get("rating")
        ingredient_str = p.get("ingredients")
        if rating is None or not ingredient_str:
            continue

        ingredients = normalize_ingredient_string(ingredient_str)
        unique_ings = set(ingredients)

        for ing in unique_ings:
            ingredient_to_ratings[ing].append(rating)

    # Compute stats
    stats = []
    for ingredient, ratings in ingredient_to_ratings.items():
        if len(ratings) < 5:
            # Ignore ingredients that only appear in a few products
            continue

        avg_rating = statistics.mean(ratings)
        stats.append({
            "ingredient": ingredient,
            "product_count": len(ratings),
            "avg_rating_with_ingredient": round(avg_rating, 3),
        })

    # Sort ingredients by avg_rating desc then by product_count desc
    stats.sort(key=lambda x: (x["avg_rating_with_ingredient"], x["product_count"]), reverse=True)
    return stats

# -----------------------------
# 3. MAIN PIPELINE
# -----------------------------

def main():
    os.makedirs("data", exist_ok=True)

    search_terms = [
        "moisturizer",
        "serum",
        "cleanser",
        "foundation",
    ]

    print("Fetching product lists...")
    all_products = []

    for term in search_terms:
        prods = fetch_products_for_search_term(term, max_pages=3)
        print(f"  {term}: fetched {len(prods)} products")
        all_products.extend(prods)

    # Deduplicate by id
    seen = {}
    for p in all_products:
        seen[p["id"]] = p  # last one wins
    unique_products = list(seen.values())
    print(f"Total unique products: {len(unique_products)}")

    # Fetch details (ingredients) for each product
    products_with_details = []
    print("Fetching product details (ingredients)...")

    for i, p in enumerate(unique_products, start=1):
        try:
            details = fetch_product_details(p["id"])
        except Exception as e:
            print(f"  Error fetching details for {p['id']}: {e}")
            continue

        product_data = {
            "id": p["id"],
            "name": p["name"],
            "rating": details["rating"] if details["rating"] is not None else p["rating"],
            "reviews_count": p["reviews_count"],
            "ingredients": details["ingredients"],
        }
        products_with_details.append(product_data)

        if i % 20 == 0:
            print(f"  Fetched details for {i} products...")
        time.sleep(0.4)  # avoid hammering the API

    print(f"Got details for {len(products_with_details)} products")

    # Save raw product data (optional)
    raw_csv_path = "data/products_raw.csv"
    with open(raw_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["id", "name", "rating", "reviews_count", "ingredients"],
        )
        writer.writeheader()
        for p in products_with_details:
            writer.writerow(p)

    print(f"Saved raw product data to {raw_csv_path}")

    # Build ingredient stats
    print("Analyzing ingredient correlations with rating...")
    ingredient_stats = build_ingredient_stats(products_with_details)

    # Write ingredient report
    report_path = "data/ingredient_report.csv"
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["ingredient", "product_count", "avg_rating_with_ingredient"],
        )
        writer.writeheader()
        for row in ingredient_stats:
            writer.writerow(row)

    print(f"Ingredient report written to {report_path}")
    print("Done ✨")


if __name__ == "__main__":
    if not RAPIDAPI_KEY:
        raise SystemExit("Missing RAPIDAPI_KEY in .env")
    main()
