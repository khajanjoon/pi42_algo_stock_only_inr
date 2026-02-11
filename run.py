import socketio, requests, time, os, hashlib, hmac, threading, json
from dotenv import load_dotenv

# ========= CONFIG =========
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("SECRET_KEY")

BASE_URL = "https://fapi.pi42.com"
WS_URL = "https://fawss.pi42.com/"

SYMBOLS = ["HOODINR","MSTRINR","INTCINR","AMZNINR","CRCLINR","COININR","PLTRINR"]

CAPITAL_PER_TRADE = 6000
DROP_PERCENT = 5
TP_PERCENT = 2.5
TRADE_COOLDOWN = 20

MIN_QTY = {
    "HOODINR": 0.08,
    "MSTRINR": 0.05,
    "INTCINR": 0.13,
    "AMZNINR": 0.03,
    "CRCLINR": 0.10,
    "COININR": 0.04,
    "PLTRINR": 0.05,
}

sio = socketio.Client(reconnection=True)

prices, positions, orders = {}, {}, {}
last_trade = {s: 0 for s in SYMBOLS}

# ========= SIGN =========
def generate_signature(secret, message):
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

def sign(query):
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

# ========= TARGET =========
def calculate_target(entry):
    return int(round(entry * (1 + TP_PERCENT/100)))

# ========= DYNAMIC QTY =========
def calculate_order_qty(sym):
    price = prices.get(sym)
    if not price:
        return None
    raw_qty = CAPITAL_PER_TRADE / price
    step = MIN_QTY.get(sym, 0.01)
    qty = int(raw_qty / step) * step
    return round(qty, 2)

# ========= LOWEST SELL =========
def get_lowest_sell(sym):
    sells = [float(o["price"]) for o in orders.get(sym,[])
             if o["side"]=="SELL" and o.get("price")]
    return min(sells) if sells else None

# ========= TRIGGER =========
def get_trigger_price(sym):
    lowest = get_lowest_sell(sym)
    if not lowest:
        return None
    return round(lowest * (1 - DROP_PERCENT/100), 2)

# ========= PLACE ORDER =========
def place_market_buy(sym):
    qty = calculate_order_qty(sym)
    if not qty:
        return False

    timestamp = str(int(time.time()*1000))
    tp = calculate_target(prices[sym])

    params = {
        "timestamp": timestamp,
        "placeType": "ORDER_FORM",
        "quantity": qty,
        "side": "BUY",
        "price": 0,
        "symbol": sym,
        "type": "MARKET",
        "reduceOnly": False,
        "marginAsset": "INR",
        "deviceType": "WEB",
        "userCategory": "EXTERNAL",
        "takeProfitPrice": tp
    }

    body = json.dumps(params, separators=(',', ':'))
    signature = generate_signature(API_SECRET, body)

    headers = {"api-key":API_KEY,"signature":signature,"Content-Type":"application/json"}

    r = requests.post(f"{BASE_URL}/v1/order/place-order", data=body, headers=headers, timeout=15)
    print(f"\n🚀 BUY {sym} Qty:{qty} TP:{tp}")
    print("Response:", r.text)
    return True

# ========= TRADE LOGIC =========
def trade_logic(sym):
    if sym not in prices:
        return
    if time.time() - last_trade[sym] < TRADE_COOLDOWN:
        return

    trigger = get_trigger_price(sym)
    if not trigger:
        return

    if prices[sym] <= trigger:
        print(f"📉 {sym} BUY @ {prices[sym]} | Trigger {trigger}")
        if place_market_buy(sym):
            last_trade[sym] = time.time()

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

# ========= FETCH ORDERS =========
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

# ========= DASHBOARD =========
def display_loop():
    while True:
        print("\n========== DASHBOARD ==========")
        for sym in SYMBOLS:
            price = prices.get(sym)
            pos = positions.get(sym)
            lowest = get_lowest_sell(sym)
            trigger = get_trigger_price(sym)
            qty = calculate_order_qty(sym)

            print(f"\n🔹 {sym}")
            print(f"LTP: {price}")
            print(f"Lowest Sell: {lowest}")
            print(f"Trigger Price: {trigger}")
            print(f"Next Qty: {qty}")

            if pos:
                entry = float(pos["entryPrice"])
                q = float(pos["quantity"])
                pnl = (price-entry)*q if price else 0
                print(f"Position → Qty:{q} Entry:{entry} PnL:{round(pnl,2)}")
            else:
                print("Position → None")
        time.sleep(4)

# ========= WEBSOCKET =========
@sio.event
def connect():
    print("WS Connected")
    sio.emit('subscribe', {'params':[f"{s.lower()}@markPrice" for s in SYMBOLS]})

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

    while True:
        try:
            sio.connect(WS_URL)
            sio.wait()
        except Exception as e:
            print("WS reconnecting...", e)
            time.sleep(5)
