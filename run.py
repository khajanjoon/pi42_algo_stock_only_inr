import socketio, requests, time, os, hashlib, hmac, threading, json
from dotenv import load_dotenv

# ========= CONFIG =========
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("SECRET_KEY")

BASE_URL = "https://fapi.pi42.com"
WS_URL = "https://fawss.pi42.com/"

# 🔥 SYMBOL CONFIG
SYMBOL_CONFIG = {
    "HOODINR": {"qty": 0.70},
    "MSTRINR": {"qty": 0.40},
    "INTCINR": {"qty": 1.20},
    "AMZNINR": {"qty": 0.30},
    "CRCLINR": {"qty": 1.00},
    "COININR": {"qty": 0.35},
    "PLTRINR": {"qty": 0.40},
}

SYMBOLS = list(SYMBOL_CONFIG.keys())

DROP_PERCENT = 5
TP_PERCENT = 2.5
TRADE_COOLDOWN = 20

sio = socketio.Client(reconnection=True)

prices = {}
positions = {}
orders = {}
last_trade = {s:0 for s in SYMBOLS}

# ========= SIGNATURE FUNCTION - EXACT MATCH TO REFERENCE =========
def generate_signature(api_secret, data_to_sign):
    """EXACT signature function from Pi42 reference."""
    return hmac.new(
        api_secret.encode('utf-8'), 
        data_to_sign.encode('utf-8'), 
        hashlib.sha256
    ).hexdigest()

def sign(query):
    """Signature for GET requests."""
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

# ========= TARGET CALC =========
def calculate_target(entry):
    return int(round(entry * (1 + TP_PERCENT/100)))

# ========= FETCH POSITIONS =========
def fetch_positions_loop():
    while True:
        try:
            ts = str(int(time.time()*1000))
            for sym in SYMBOLS:
                query = f"symbol={sym}&sortOrder=desc&pageSize=100&timestamp={ts}"
                headers = {"api-key":API_KEY,"signature":sign(query)}
                r = requests.get(f"{BASE_URL}/v1/positions/OPEN?{query}", headers=headers)
                data = r.json()
                positions[sym] = next((p for p in data if p["contractPair"]==sym), None)
        except Exception as e:
            print("Position error:", e)
        time.sleep(10)

# ========= FETCH OPEN ORDERS =========
def fetch_orders_loop():
    while True:
        try:
            ts = str(int(time.time()*1000))
            query = f"timestamp={ts}"
            headers = {"api-key":API_KEY,"signature":sign(query)}
            r = requests.get(f"{BASE_URL}/v1/order/open-orders?{query}", headers=headers)
            data = r.json()
            for sym in SYMBOLS:
                orders[sym] = [o for o in data if o.get("symbol")==sym]
        except Exception as e:
            print("Order error:", e)
        time.sleep(12)

# ========= LOWEST SELL =========
def get_lowest_sell(sym):
    sell_prices = [float(o["price"]) for o in orders.get(sym,[])
                   if o["side"]=="SELL" and o.get("price")]
    return min(sell_prices) if sell_prices else None

# ========= BUY ORDER - EXACT MATCH TO REFERENCE =========
def place_market_buy(sym):
    """Place market buy order - EXACT match to Pi42 reference."""
    
    # Generate the current timestamp in milliseconds as STRING
    timestamp = str(int(time.time() * 1000))

    # Define the order parameters - EXACT match to reference
    params = {
        'timestamp': timestamp,
        'placeType': 'ORDER_FORM',
        'quantity': SYMBOL_CONFIG[sym]["qty"],  # Keep as is, don't format
        'side': 'BUY',
        'price': 0,
        'symbol': sym,
        'type': 'MARKET',  # CRITICAL: 'type' not 'orderType'
        'reduceOnly': False,
        'marginAsset': 'INR',
        'deviceType': 'WEB',
        'userCategory': 'EXTERNAL',  # 🔥 ADD THIS - MISSING IN YOUR CODE
        'takeProfitPrice': calculate_target(prices[sym])  # Add take profit
    }

    # Convert the parameters to a JSON string to sign - EXACT separators
    data_to_sign = json.dumps(params, separators=(',', ':'))

    # Generate the signature for authentication
    signature = generate_signature(API_SECRET, data_to_sign)

    # Define the headers including the API key and the signature
    headers = {
        'api-key': API_KEY,
        'signature': signature,
        'Content-Type': 'application/json'  # Add content type
    }

    print(f"\n🚀 ORDER SENT for {sym}")
    print(f"Body: {data_to_sign}")
    print(f"Signature: {signature}")
    print(f"API Key: {API_KEY[:10]}...{API_KEY[-5:] if API_KEY else 'None'}")

    try:
        # Send the POST request to place the order - EXACT match
        response = requests.post(
            f'{BASE_URL}/v1/order/place-order', 
            json=params,  # Use json parameter
            headers=headers,
            timeout=15
        )

        # Raise an HTTPError if the response status is 4xx or 5xx
        response.raise_for_status()

        # Parse the JSON response data
        response_data = response.json()

        # Print the success message with the order details
        print('✅ Order placed successfully:', json.dumps(response_data, indent=4))
        last_trade[sym] = time.time()
        return True

    except requests.exceptions.HTTPError as err:
        print(f"❌ HTTP Error: {err.response.text if err.response else err}")
        return False
    except Exception as e:
        print(f"❌ An unexpected error occurred: {str(e)}")
        return False

# ========= TRADE LOGIC =========
def trade_logic(sym):
    if sym not in prices:
        return
    if time.time() - last_trade[sym] < TRADE_COOLDOWN:
        return

    lowest = get_lowest_sell(sym)
    if not lowest:
        return

    trigger = lowest * (1 - DROP_PERCENT/100)

    if prices[sym] <= trigger:
        print(f"📉 {sym} BUY trigger!")
        place_market_buy(sym)
        last_trade[sym] = time.time()

# ========= DISPLAY =========
def display_loop():
    while True:
        print("\n========== DASHBOARD ==========")
        for sym in SYMBOLS:
            price = prices.get(sym)
            pos = positions.get(sym)
            print(f"{sym} LTP:{price}")
            if pos:
                qty = float(pos["quantity"])
                entry = float(pos["entryPrice"])
                pnl = (price-entry)*qty if price else 0
                print(f"  Pos:{qty} Entry:{entry} PnL:{round(pnl,2)}")
            else:
                print("  No position")
        time.sleep(4)

# ========= WEBSOCKET =========
@sio.event
def connect():
    print("WS Connected")
    subs = [f"{s.lower()}@markPrice" for s in SYMBOLS]
    sio.emit('subscribe', {'params': subs})

@sio.on('markPriceUpdate')
def on_price(data):
    sym = data.get('s','').upper()
    if sym in SYMBOLS:
        prices[sym] = float(data['p'])
        trade_logic(sym)

# ========= MAIN =========
if __name__ == "__main__":
    threading.Thread(target=fetch_positions_loop, daemon=True).start()
    threading.Thread(target=fetch_orders_loop, daemon=True).start()
    threading.Thread(target=display_loop, daemon=True).start()
    sio.connect(WS_URL)
    sio.wait()
