import os
import re
import asyncio
import aiohttp
import json
import time
import random
import logging
import ssl
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('steam_checker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Flask app setup
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Global variables
stats = {
    'hits': 0,
    'twofa': 0,
    'bad': 0,
    'total_games': 0,
    'total_balance': 0.0,
    'captcha': 0,
    'checked': 0,
    'total': 0
}
stats_lock = Lock()
logs = []
logs_lock = Lock()
settings = {
    'use_proxies': True,
    'use_captcha': False,
    'threads': 5,
    'captcha_api_key': ''
}
sessions = {}  # Store session data: {session_id: {combos, proxies, output_dir, last_access}}
executor = ThreadPoolExecutor(max_workers=10)
session_timeout = 3600  # 1 hour in seconds

# Ensure tmp directory exists
os.makedirs('/tmp/sessions', exist_ok=True)

def clean_sessions():
    """Remove expired session directories."""
    now = time.time()
    for session_id in list(sessions.keys()):
        if now - sessions[session_id]['last_access'] > session_timeout:
            session_dir = sessions[session_id]['output_dir']
            if os.path.exists(session_dir):
                for file in os.listdir(session_dir):
                    os.remove(os.path.join(session_dir, file))
                os.rmdir(session_dir)
            del sessions[session_id]
            logger.info(f"Cleaned up session {session_id}")

def update_session_access(session_id):
    """Update last access time for a session."""
    if session_id in sessions:
        sessions[session_id]['last_access'] = time.time()

async def fetch_steamdb_calculator(session, steamid, proxy=None):
    """Fetch Steam account value from SteamDB."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        url = f'https://steamdb.info/calculator/{steamid}/'
        async with session.get(url, headers=headers, proxy=proxy, timeout=10) as response:
            if response.status == 200:
                text = await response.text()
                soup = BeautifulSoup(text, 'html.parser')
                games_elem = soup.select_one('div.stats a[href*="games"] span.number')
                value_elem = soup.select_one('div.stats a[href*="value"] span.number')
                games = int(games_elem.text.replace(',', '')) if games_elem else 0
                balance = float(value_elem.text.replace('$', '').replace(',', '')) if value_elem else 0.0
                return games, balance
            else:
                logger.warning(f"SteamDB fetch failed for SteamID {steamid}: HTTP {response.status}")
                return 0, 0.0
    except Exception as e:
        logger.error(f"Error fetching SteamDB for SteamID {steamid}: {str(e)}")
        return 0, 0.0

async def get_steamid_from_profile(session, username, proxy=None):
    """Fetch SteamID from Steam profile page."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        url = f'https://steamcommunity.com/id/{username}'
        async with session.get(url, headers=headers, proxy=proxy, timeout=10) as response:
            if response.status == 200:
                text = await response.text()
                steamid_match = re.search(r'"steamid":"(\d+)"', text)
                if steamid_match:
                    return steamid_match.group(1)
                logger.warning(f"No SteamID found in profile for {username}")
                return None
            else:
                logger.warning(f"Profile fetch failed for {username}: HTTP {response.status}")
                return None
    except Exception as e:
        logger.error(f"Error fetching profile for {username}: {str(e)}")
        return None

async def check_account(session, combo, proxy=None, session_id=None):
    """Check a single Steam account."""
    try:
        username, password = combo.split(':')
        logger.info(f"Checking account: {username}")

        # Simulate login (replace with actual Steam login logic)
        login_url = 'https://steamcommunity.com/login/home/'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        async with session.get(login_url, headers=headers, proxy=proxy, timeout=10) as response:
            if response.status == 200:
                # Simulate successful login
                if random.random() < 0.8:  # 80% success rate for demo
                    logger.info(f"Success: {username}")
                    steamid = await get_steamid_from_profile(session, username, proxy)
                    if not steamid:
                        logger.warning(f"No SteamID found for {username} after trying all methods")
                        with stats_lock:
                            stats['hits'] += 1
                            stats['checked'] += 1
                        with logs_lock:
                            logs.append(f"[{datetime.now()}] Success: {username} - No SteamID")
                        return

                    games, balance = await fetch_steamdb_calculator(session, steamid, proxy)
                    status = 'Private' if games == 0 else 'Valid'
                    result = f"Saved result: {status} - {username}, Games: {games}, Balance: ${balance}, SteamID: {steamid}"
                    
                    # Save result to file
                    session_dir = sessions[session_id]['output_dir']
                    with open(os.path.join(session_dir, f"{status.lower()}.txt"), 'a', encoding='utf-8') as f:
                        f.write(f"{username}:{password} | Games: {games} | Balance: ${balance} | SteamID: {steamid}\n")
                    
                    with stats_lock:
                        stats['hits'] += 1
                        stats['total_games'] += games
                        stats['total_balance'] += balance
                        stats['checked'] += 1
                    with logs_lock:
                        logs.append(f"[{datetime.now()}] {result}")
                else:
                    logger.info(f"Invalid credentials: {username}")
                    with stats_lock:
                        stats['bad'] += 1
                        stats['checked'] += 1
                    with logs_lock:
                        logs.append(f"[{datetime.now()}] Invalid credentials: {username}")
                        session_dir = sessions[session_id]['output_dir']
                        with open(os.path.join(session_dir, 'bad.txt'), 'a', encoding='utf-8') as f:
                            f.write(f"{username}:{password}\n")
            else:
                logger.error(f"Login failed for {username}: HTTP {response.status}")
                with stats_lock:
                    stats['bad'] += 1
                    stats['checked'] += 1
                with logs_lock:
                    logs.append(f"[{datetime.now()}] Invalid credentials: {username}")
                    session_dir = sessions[session_id]['output_dir']
                    with open(os.path.join(session_dir, 'bad.txt'), 'a', encoding='utf-8') as f:
                        f.write(f"{username}:{password}\n")
    except Exception as e:
        logger.error(f"Error checking account {combo}: {str(e)}")
        with stats_lock:
            stats['bad'] += 1
            stats['checked'] += 1
        with logs_lock:
            logs.append(f"[{datetime.now()}] Error checking {combo}: {str(e)}")
            session_dir = sessions[session_id]['output_dir']
            with open(os.path.join(session_dir, 'bad.txt'), 'a', encoding='utf-8') as f:
                f.write(f"{combo}\n")

async def process_accounts(combos, proxies, session_id):
    """Process all accounts concurrently."""
    async with aiohttp.ClientSession() as session:
        tasks = []
        for combo in combos:
            proxy = random.choice(proxies) if proxies and settings['use_proxies'] else None
            tasks.append(check_account(session, combo, proxy, session_id))
            if len(tasks) >= settings['threads']:
                await asyncio.gather(*tasks, return_exceptions=True)
                tasks = []
                await asyncio.sleep(1)  # Prevent rate-limiting
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    with logs_lock:
        logs.append(f"[{datetime.now()}] Check complete [output_dir: {sessions[session_id]['output_dir']}]")

@app.route('/')
def serve_index():
    """Serve the index.html file."""
    return send_file('index.html')

@app.route('/api/load_combo_file', methods=['POST'])
def load_combo_file():
    """Handle combo file upload."""
    clean_sessions()
    if 'file' not in request.files or 'session_id' not in request.form:
        return jsonify({'success': False, 'error': 'Missing file or session ID'}), 400
    
    file = request.files['file']
    session_id = request.form['session_id']
    
    if not file.filename.endswith('.txt'):
        return jsonify({'success': False, 'error': 'File must be a .txt file'}), 400
    if file.content_length > 1024 * 1024:  # 1MB limit
        return jsonify({'success': False, 'error': 'File size exceeds 1MB'}), 400

    try:
        # Initialize session
        if session_id not in sessions:
            session_dir = os.path.join('/tmp/sessions', session_id)
            os.makedirs(session_dir, exist_ok=True)
            sessions[session_id] = {
                'combos': [],
                'proxies': [],
                'output_dir': session_dir,
                'last_access': time.time()
            }
        else:
            update_session_access(session_id)

        # Save and process file
        combo_path = os.path.join(sessions[session_id]['output_dir'], 'combo.txt')
        file.save(combo_path)
        with open(combo_path, 'r', encoding='utf-8') as f:
            combos = [line.strip() for line in f if ':' in line]
        
        if not combos:
            return jsonify({'success': False, 'error': 'No valid combos found'}), 400
        
        sessions[session_id]['combos'] = combos
        with stats_lock:
            stats['total'] = len(combos)
            stats['checked'] = 0
            stats['hits'] = 0
            stats['bad'] = 0
            stats['twofa'] = 0
            stats['total_games'] = 0
            stats['total_balance'] = 0.0
            stats['captcha'] = 0
        
        logger.info(f"Loaded {len(combos)} combos for session {session_id}")
        return jsonify({'success': True, 'count': len(combos)})
    except Exception as e:
        logger.error(f"Error loading combo file for session {session_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/load_proxy_file', methods=['POST'])
def load_proxy_file():
    """Handle proxy file upload."""
    clean_sessions()
    if 'file' not in request.files or 'session_id' not in request.form:
        return jsonify({'success': False, 'error': 'Missing file or session ID'}), 400
    
    file = request.files['file']
    session_id = request.form['session_id']
    
    if not file.filename.endswith('.txt'):
        return jsonify({'success': False, 'error': 'File must be a .txt file'}), 400
    if file.content_length > 1024 * 1024:  # 1MB limit
        return jsonify({'success': False, 'error': 'File size exceeds 1MB'}), 400

    try:
        if session_id not in sessions:
            return jsonify({'success': False, 'error': 'Invalid session ID'}), 400
        update_session_access(session_id)

        proxy_path = os.path.join(sessions[session_id]['output_dir'], 'proxy.txt')
        file.save(proxy_path)
        with open(proxy_path, 'r', encoding='utf-8') as f:
            proxies = [line.strip() for line in f if line.strip()]
        
        sessions[session_id]['proxies'] = proxies
        logger.info(f"Loaded {len(proxies)} proxies for session {session_id}")
        return jsonify({'success': True, 'count': len(proxies)})
    except Exception as e:
        logger.error(f"Error loading proxy file for session {session_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/update_settings', methods=['POST'])
def update_settings():
    """Update checker settings."""
    clean_sessions()
    data = request.get_json()
    session_id = data.get('session_id')
    
    if not session_id or session_id not in sessions:
        return jsonify({'success': False, 'error': 'Invalid session ID'}), 400
    
    update_session_access(session_id)
    
    try:
        settings['use_proxies'] = data.get('use_proxies', settings['use_proxies'])
        settings['use_captcha'] = data.get('use_captcha', settings['use_captcha'])
        settings['threads'] = min(max(int(data.get('threads', settings['threads'])), 1), 10)
        settings['captcha_api_key'] = data.get('captcha_api_key', settings['captcha_api_key'])
        logger.info(f"Settings updated for session {session_id}: {settings}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error updating settings for session {session_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/start_checking', methods=['POST'])
def start_checking():
    """Start the checking process."""
    clean_sessions()
    data = request.get_json()
    session_id = data.get('session_id')
    
    if not session_id or session_id not in sessions:
        return jsonify({'success': False, 'error': 'Invalid session ID'}), 400
    if not sessions[session_id]['combos']:
        return jsonify({'success': False, 'error': 'No combos loaded'}), 400
    
    update_session_access(session_id)
    
    try:
        combos = sessions[session_id]['combos']
        proxies = sessions[session_id]['proxies']
        
        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(process_accounts(combos, proxies, session_id))
            loop.close()
        
        executor.submit(run_async)
        logger.info(f"Started checking {len(combos)} accounts for session {session_id}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error starting check for session {session_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/get_stats')
def get_stats():
    """Get current stats."""
    clean_sessions()
    session_id = request.args.get('session_id')
    
    if not session_id or session_id not in sessions:
        return jsonify({'success': False, 'error': 'Invalid session ID'}), 400
    
    update_session_access(session_id)
    
    with stats_lock:
        return jsonify(stats)

@app.route('/api/get_logs')
def get_logs():
    """Get current logs."""
    clean_sessions()
    session_id = request.args.get('session_id')
    
    if not session_id or session_id not in sessions:
        return jsonify({'success': False, 'error': 'Invalid session ID'}), 400
    
    update_session_access(session_id)
    
    with logs_lock:
        return jsonify(logs)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_DEBUG', 'False').lower() == 'true')