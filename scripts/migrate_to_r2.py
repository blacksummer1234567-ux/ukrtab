import os
import sys
import re
import base64
import json
import urllib.request
from urllib.parse import urlparse
import psycopg
import boto3
from botocore.config import Config

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 1. Parse .env
env = {}
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
with open(env_path, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"\'')

R2_ACCOUNT_ID = env.get('R2_ACCOUNT_ID')
R2_ACCESS_KEY_ID = env.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = env.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = env.get('R2_BUCKET_NAME', 'ukrtab-images')
R2_PUBLIC_URL = env.get('R2_PUBLIC_URL', '').rstrip('/')
DB_URL = env.get('POSTGRES_URL_NON_POOLING') or env.get('POSTGRES_PRISMA_URL') or env.get('DATABASE_URL')

print("=" * 60, flush=True)
print("🚀 UkrTab: Prom.ua & Base64 -> Cloudflare R2 Migration", flush=True)
print(f"📦 Bucket:     {R2_BUCKET_NAME}", flush=True)
print(f"🌐 Public URL: {R2_PUBLIC_URL}", flush=True)
print("=" * 60, flush=True)

# 2. Setup S3 Client for Cloudflare R2
s3 = boto3.client(
    's3',
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(signature_version='s3v4')
)

uploaded_cache = {}

def upload_image_to_r2(img_input, folder="products", fallback_name="item"):
    if not img_input:
        return img_input
    
    # Already on R2
    if R2_PUBLIC_URL and R2_PUBLIC_URL in img_input:
        return img_input
    
    # Check cache
    if img_input in uploaded_cache:
        return uploaded_cache[img_input]

    # Case 1: Base64 data URI
    if img_input.startswith("data:image/"):
        try:
            m = re.match(r"^data:image/([a-zA-Z0-9+]+);base64,(.+)$", img_input)
            if not m:
                return img_input
            ext = "jpg" if m.group(1).lower() in ("jpeg", "jpg") else m.group(1).lower()
            raw_bytes = base64.b64decode(m.group(2))
            clean_name = re.sub(r'[^a-zA-Z0-9._-]', '_', fallback_name)
            key = f"{folder}/{clean_name}.{ext}"
            content_type = f"image/{m.group(1)}"

            print(f"   ⬆️ Uploading Base64 image to R2: {key} ({len(raw_bytes)//1024} KB)", flush=True)
            s3.put_object(
                Bucket=R2_BUCKET_NAME,
                Key=key,
                Body=raw_bytes,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable"
            )
            new_url = f"{R2_PUBLIC_URL}/{key}"
            uploaded_cache[img_input] = new_url
            return new_url
        except Exception as e:
            print(f"   ❌ Base64 upload failed: {e}", flush=True)
            return img_input

    # Case 2: Prom.ua URL
    if "prom.ua" in img_input:
        try:
            parsed = urlparse(img_input)
            basename = os.path.basename(parsed.path) or f"{fallback_name}.jpg"
            clean_name = re.sub(r'[^a-zA-Z0-9._-]', '_', basename)
            key = f"{folder}/{clean_name}"

            print(f"   ⬇️ Downloading from Prom: {img_input}", flush=True)
            req = urllib.request.Request(
                img_input,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                data = resp.read()

            print(f"   ⬆️ Uploading to R2: {key} ({len(data)//1024} KB)", flush=True)
            s3.put_object(
                Bucket=R2_BUCKET_NAME,
                Key=key,
                Body=data,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable"
            )
            new_url = f"{R2_PUBLIC_URL}/{key}"
            uploaded_cache[img_input] = new_url
            return new_url
        except Exception as e:
            print(f"   ❌ Failed to migrate {img_input}: {e}", flush=True)
            return img_input

    return img_input

# 3. Connect to Database and Process
print("\n🔌 Connecting to PostgreSQL...", flush=True)
with psycopg.connect(DB_URL) as conn:
    print("✅ Connected to Database.", flush=True)
    with conn.cursor() as cur:
        # A. PRODUCTS
        cur.execute('SELECT id, name, slug, image, images FROM "Product"')
        products = cur.fetchall()
        print(f"\n📦 Found {len(products)} products.", flush=True)

        updated_products = 0
        for idx, (p_id, name, slug, main_image, images_json) in enumerate(products):
            changed = False

            # Main image
            new_main_image = main_image
            if main_image and ("prom.ua" in main_image or main_image.startswith("data:image/")):
                print(f"\n[{idx+1}/{len(products)}] Product: {name}", flush=True)
                new_main_image = upload_image_to_r2(main_image, "products", f"{slug or p_id}_main")
                if new_main_image != main_image:
                    changed = True

            # Extra images
            new_images_list = []
            if images_json:
                try:
                    imgs = json.loads(images_json) if isinstance(images_json, str) else images_json
                    if isinstance(imgs, list):
                        for img_idx, img_url in enumerate(imgs):
                            if isinstance(img_url, str) and ("prom.ua" in img_url or img_url.startswith("data:image/")):
                                if not changed:
                                    print(f"\n[{idx+1}/{len(products)}] Product: {name}", flush=True)
                                migrated = upload_image_to_r2(img_url, "products", f"{slug or p_id}_{img_idx}")
                                new_images_list.append(migrated)
                                if migrated != img_url:
                                    changed = True
                            else:
                                new_images_list.append(img_url)
                except Exception as e:
                    print(f"   ⚠️ Could not parse images JSON: {e}", flush=True)

            if changed:
                cur.execute(
                    'UPDATE "Product" SET image = %s, images = %s WHERE id = %s',
                    (new_main_image, json.dumps(new_images_list), p_id)
                )
                updated_products += 1
                conn.commit()
                print(f"   💾 Saved to Database.", flush=True)

        # B. CATEGORIES
        print("\n📁 Checking Categories...", flush=True)
        cur.execute('SELECT id, name, slug, image FROM "Category"')
        categories = cur.fetchall()
        for c_id, c_name, c_slug, c_image in categories:
            if c_image and ("prom.ua" in c_image or c_image.startswith("data:image/")):
                print(f"Category: {c_name}", flush=True)
                new_img = upload_image_to_r2(c_image, "categories", f"cat_{c_slug or c_id}")
                if new_img != c_image:
                    cur.execute('UPDATE "Category" SET image = %s WHERE id = %s', (new_img, c_id))
                    conn.commit()
                    print(f"   💾 Category updated.", flush=True)

        # C. BANNERS
        print("\n🎨 Checking Banners...", flush=True)
        cur.execute('SELECT id, title, image FROM "Banner"')
        banners = cur.fetchall()
        for b_id, b_title, b_image in banners:
            if b_image and ("prom.ua" in b_image or b_image.startswith("data:image/")):
                print(f"Banner: {b_title or b_id}", flush=True)
                new_img = upload_image_to_r2(b_image, "banners", f"banner_{b_id}")
                if new_img != b_image:
                    cur.execute('UPDATE "Banner" SET image = %s WHERE id = %s', (new_img, b_id))
                    conn.commit()
                    print(f"   💾 Banner updated.", flush=True)

print("\n" + "=" * 60, flush=True)
print("🎉 MIGRATION FINISHED SUCCESSFULLY!", flush=True)
print(f"Products updated: {updated_products} / {len(products)}", flush=True)
print(f"Total unique images stored in R2: {len(uploaded_cache)}", flush=True)
print("=" * 60, flush=True)
