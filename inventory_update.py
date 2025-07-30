
import requests
import csv
import io
import os
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time

load_dotenv()

def parse_cookie_str(raw_cookie_str):
    cookie_dict = {}
    for pair in raw_cookie_str.split(";"):
        if "=" in pair:
            key, value = pair.strip().split("=", 1)
            cookie_dict[key] = value
    return cookie_dict

AGLINE_COOKIES = os.getenv("AGLINE_COOKIES", "")
COOKIES = parse_cookie_str(AGLINE_COOKIES)

HEADERS_WEB = {
    "User-Agent": "Mozilla/5.0"
}

SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_SHOP = os.getenv("SHOPIFY_SHOP")

HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_TOKEN,
    "Content-Type": "application/json"
}

CSV_URL = "https://www.agline.com/stock-level-csv/"

def download_csv():
    response = requests.get(CSV_URL)
    response.encoding = 'utf-8'
    f = io.StringIO(response.text)
    reader = csv.DictReader(f)
    print(f"📌 CSV 字段头: {reader.fieldnames}")
    return list(reader)

def get_first_location_id():
    url = f"https://{SHOPIFY_SHOP}/admin/api/2024-04/locations.json"
    res = requests.get(url, headers=HEADERS)
    if res.status_code == 200:
        locations = res.json().get("locations", [])
        for loc in locations:
            if not loc.get("legacy") and not loc.get("fulfillment_service"):
                print(f"✅ 使用库存位置: {loc['name']} → ID: {loc['id']}")
                return loc["id"]
        print("⚠️ 没有找到可用的自有库存位置")
    else:
        print(f"❌ 获取库存位置失败 → 状态码: {res.status_code} → 返回: {res.text}")
    return None

def get_all_shopify_skus():
    skus = {}
    base_url = f"https://{SHOPIFY_SHOP}/admin/api/2024-04/variants.json?limit=250"
    next_url = base_url

    while next_url:
        res = requests.get(next_url, headers=HEADERS)
        if res.status_code != 200:
            print(f"❌ 无法获取 Shopify 变体 → 状态码: {res.status_code} → 返回: {res.text}")
            break

        data = res.json()
        variants = data.get("variants", [])
        for v in variants:
            if v.get("sku"):
                skus[v["sku"].strip()] = {
                    "variant_id": v["id"],
                    "inventory_item_id": v["inventory_item_id"]
                }

        link_header = res.headers.get("Link", "")
        next_url = None
        if 'rel="next"' in link_header:
            parts = link_header.split(",")
            for part in parts:
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip()[1:-1]
                    break

    print(f"✅ 从 Shopify 获取 {len(skus)} 个 SKU")
    return skus

def update_inventory(inventory_item_id, location_id, stock, sku):
    url = f"https://{SHOPIFY_SHOP}/admin/api/2024-04/inventory_levels/set.json"
    payload = {
        "location_id": location_id,
        "inventory_item_id": inventory_item_id,
        "available": int(stock)
    }
    res = requests.post(url, json=payload, headers=HEADERS)
    if res.status_code == 200:
        print(f"✅ 已更新库存 → SKU {sku} → 数量 {stock}")
    else:
        print(f"❌ 更新库存失败 → SKU {sku} → 状态码: {res.status_code} → 返回: {res.text}")

def update_variant_weight(variant_id, weight, sku):
    url = f"https://{SHOPIFY_SHOP}/admin/api/2024-04/variants/{variant_id}.json"
    payload = {"variant": {"id": variant_id}}

    try:
        if weight and "kg" in weight.lower():
            grams = float(weight.lower().replace("kg", "").strip()) * 1000
            payload["variant"]["weight"] = grams / 1000.0
            payload["variant"]["weight_unit"] = "kg"
    except Exception as e:
        print(f"❌ weight 转换失败 → SKU {sku} → 原始值: {weight} → 错误: {e}")
        return

    res = requests.put(url, json=payload, headers=HEADERS)
    if res.status_code == 200:
        print(f"✅ 更新 variant weight → SKU {sku} → {weight}")
    else:
        print(f"❌ 更新 variant weight 失败 → SKU {sku} → 状态码: {res.status_code} → 返回: {res.text}")

def update_variant_details(inventory_item_id, weight, barcode, sku):
    url = f"https://{SHOPIFY_SHOP}/admin/api/2024-04/inventory_items/{inventory_item_id}.json"
    payload = {"inventory_item": {"id": inventory_item_id}}

    if barcode:
        payload["inventory_item"]["barcode"] = barcode

    res = requests.put(url, json=payload, headers=HEADERS)
    if res.status_code == 200:
        print(f"✅ 更新 barcode → SKU {sku} → {barcode}")
    else:
        print(f"❌ 更新 barcode 失败 → SKU {sku} → 状态码: {res.status_code} → 返回: {res.text}")

def search_agline_url(sku):
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--headless=new")

    driver = webdriver.Chrome(options=options)

    try:
        driver.get("https://www.agline.com/")
        driver.execute_script(f"""
            document.querySelector('input.search-field').value = '{sku}';
            document.querySelector('form.search-form').submit();
        """)
        time.sleep(5)
        final_url = driver.current_url
        if "/product/" in final_url:
            return final_url
        else:
            print(f"⚠️ SKU {sku} 搜索后未跳转到产品页 → 当前 URL: {final_url}")
            return None
    except Exception as e:
        print(f"❌ Selenium 搜索失败 → SKU {sku} → 错误: {e}")
        return None
    finally:
        driver.quit()

def scrape_weight_barcode(product_url, sku):
    try:
        res = requests.get(product_url, headers=HEADERS_WEB, cookies=COOKIES, timeout=10)
        if res.status_code != 200:
            print(f"❌ 抓取失败 → {product_url} → 状态码: {res.status_code}")
            return None, None

        soup = BeautifulSoup(res.text, "html.parser")
        sku = sku.upper()

        for tr in soup.select("tr"):
            sku_cell = tr.find("td", class_="skucol")
            if sku_cell and sku in sku_cell.get_text(strip=True).upper():
                weight_cell = tr.find("td", class_="weight_col")
                barcode_cell = tr.find("td", class_="barcode_col")

                weight = weight_cell.get_text(strip=True) if weight_cell else None
                barcode = barcode_cell.get_text(strip=True) if barcode_cell else None

                return weight, barcode

        print(f"⚠️ SKU {sku} 没有找到匹配的行")
        return None, None

    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return None, None

def main():
    location_id = get_first_location_id()
    if not location_id:
        print("❌ 无法获取库存位置 ID")
        return

    records = download_csv()
    print(f"📦 从 CSV 读取 {len(records)} 条记录")

    sku_map = get_all_shopify_skus()

    for row in records:
        sku = row.get("SKU", "").strip()
        stock = row.get("Available", "").strip()

        if not sku or not stock.isdigit():
            continue

        if sku not in sku_map:
            continue

        variant_info = sku_map[sku]
        inventory_item_id = variant_info["inventory_item_id"]
        variant_id = variant_info["variant_id"]

        update_inventory(inventory_item_id, location_id, stock, sku)

        product_url = search_agline_url(sku)
        if product_url:
            weight, barcode = scrape_weight_barcode(product_url, sku)
            print(f"🎯 抓取结果 → SKU {sku} → weight: {weight} / barcode: {barcode}")
            if weight or barcode:
                update_variant_weight(variant_id, weight, sku)
                update_variant_details(inventory_item_id, weight, barcode, sku)
            else:
                print(f"⚠️ 未抓取到 weight/barcode → SKU {sku}")
        else:
            print(f"⚠️ 无法搜索到产品页面 → SKU {sku}")

if __name__ == "__main__":
    main()
