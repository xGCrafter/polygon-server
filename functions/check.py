import json
from flask import Flask, request, jsonify
import os
import hmac
import hashlib
import time

app = Flask(__name__)

# Secret key for HMAC (store in Netlify environment variables)
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")

# Whitelist IPs (optional, add your client IPs)
WHITELIST_IPS = os.getenv("WHITELIST_IPS", "").split(",")

def verify_request():
    # Check IP (optional)
    client_ip = request.remote_addr
    if WHITELIST_IPS and client_ip not in WHITELIST_IPS:
        return False, "IP not whitelisted"

    # Check API key with HMAC and timestamp
    api_key = request.headers.get("X-API-Key")
    timestamp = request.headers.get("X-Timestamp")
    if not api_key or not timestamp:
        return False, "Missing API key or timestamp"

    # Prevent replay attacks by checking timestamp (within 5 minutes)
    try:
        request_time = int(timestamp)
        current_time = int(time.time())
        if abs(current_time - request_time) > 300:
            return False, "Request timestamp expired"
    except ValueError:
        return False, "Invalid timestamp"

    # Verify HMAC signature
    expected_hmac = hmac.new(
        SECRET_KEY.encode(),
        (timestamp + request.path).encode(),
        hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(api_key, expected_hmac):
        return False, "Invalid API key"

    return True, None

@app.route("/check", methods=["POST"])
def check_accounts():
    # Verify the request
    is_valid, error = verify_request()
    if not is_valid:
        return jsonify({"error": error}), 401

    data = request.get_json()
    combos = data.get("combos", [])
    proxies = data.get("proxies", [])
    use_captcha_solver = data.get("use_captcha_solver", False)
    nopecha_api_key = data.get("nopecha_api_key", "")
    max_tasks = data.get("max_tasks", 1)

    # Minimal logic: Return a placeholder response
    results = [{"status": "Processed", "combo": combo} for combo in combos]
    stats = {"processed": len(combos)}

    return jsonify({"results": results, "stats": stats})

def handler(event, context):
    body = json.loads(event["body"]) if event.get("body") else {}
    environ = {
        "REQUEST_METHOD": event["httpMethod"],
        "PATH_INFO": event["path"],
        "SERVER_PROTOCOL": "HTTP/1.1",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "CONTENT_TYPE": "application/json",
        "wsgi.input": json.dumps(body),
        "wsgi.errors": None,
        "wsgi.version": (1, 0),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "REMOTE_ADDR": event.get("requestContext", {}).get("identity", {}).get("sourceIp", ""),
    }
    headers = {}
    response = app(environ, lambda s, r: headers.update(dict(r)))
    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps(response)
    }
