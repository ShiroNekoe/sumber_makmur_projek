import json
import urllib.request
import urllib.error

url = "https://mainnet.helius-rpc.com/?api-key=00f9de1e-3d75-46e0-9e7e-fee21a442a51"
payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getTransaction",
    "params": [
        "4YRhatbTDzLctTkmP9j1S6yYQh4m6yH7hX7Q2Y8z",
        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
    ]
}

req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read().decode("utf-8")
        print("RPC RESPONSE BODY:")
        print(body)
except urllib.error.HTTPError as e:
    print("HTTP ERROR:", e.code, e.reason)
    print(e.read().decode("utf-8"))
