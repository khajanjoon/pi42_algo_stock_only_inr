import socketio, requests, time, os, hashlib, hmac, threading, json , math
from dotenv import load_dotenv

# ========= CONFIG =========
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("SECRET_KEY")

BASE_URL = "https://fapi.pi42.com"
WS_URL = "https://fawss.pi42.com/"

SYMBOLS = ["HOODINR","MSTRINR","INTCINR","AMZNINR","CRCLINR","COININR","PLTRINR"]

# ₹ capital per trade
CAPITAL_PER_TRADE = 6000  

# Exchange minimum lot sizes
MIN_QTY = {
    "HOODINR": 0.08,
    "MSTRINR": 0.05,
    "INTCINR": 0.13,
    "AMZNINR": 0.03,
    "CRCLINR": 0.10,
    "COININR": 0.04,
    "PLTRINR": 0.05,
}

DROP_PERCENT = 5
TP_PERCENT = 2.5
TRADE_COOLDOWN = 20

sio = socketio.Client(reconnection=True)

prices, positions, orders = {}, {}, {}
last_trade = {s:0 for s in SYMBOLS}

# ========= SIGNATURE =========
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
    min_qty = MIN_QTY.get(sym, 0.01)

    steps = int(raw_qty / min_qty)
    qty = round(steps * min_qty, 8)
    

    if qty < min_qty:
        return None
    return f"{qty:.2f}"

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

# ========= LOWEST SELL =========
def get_lowest_sell(sym):
    sell_prices = [float(o["price"]) for o in orders.get(sym,[])
                   if o["side"]=="SELL" and o.get("price")]
    return min(sell_prices) if sell_prices else None

# ========= PLACE ORDER =========
def place_market_buy(sym):
    qty = calculate_order_qty(sym)
    if not qty:
        print(f"Qty too small for {sym}")
        return False

    timestamp = str(int(time.time()*1000))
    target = calculate_target(prices[sym])

    params = {
        "timestamp": timestamp,
        "placeType": "ORDER_FORM",
        "quantity": f"{qty:.2f}",
        "side": "BUY",
        "price": 0,
        "symbol": sym,
        "type": "MARKET",
        "reduceOnly": False,
        "marginAsset": "INR",
        "deviceType": "WEB",
        "userCategory": "EXTERNAL",
        "takeProfitPrice": target
    }

    body = json.dumps(params, separators=(',', ':'))
    signature = generate_signature(API_SECRET, body)

    headers = {"api-key":API_KEY,"signature":signature,"Content-Type":"application/json"}

    r = requests.post(f"{BASE_URL}/v1/order/place-order", data=body, headers=headers, timeout=15)
    print(f"\n🚀 ORDER {sym} Qty:{qty} TP:{target}")
    print("Response:", r.text)
    return True

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
        print(f"📉 {sym} BUY trigger")
        if place_market_buy(sym):
            last_trade[sym] = time.time()

# ========= DASHBOARD =========
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
