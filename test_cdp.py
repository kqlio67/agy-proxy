import asyncio
import json
import urllib.request
import websockets

async def check_aistudio():
    version_url = "http://127.0.0.1:9222/json/version"
    try:
        with urllib.request.urlopen(version_url, timeout=3) as resp:
            version_info = json.loads(resp.read().decode())
    except Exception as e:
        print("CDP not running on 9222:", e)
        return

    tabs_url = "http://127.0.0.1:9222/json"
    with urllib.request.urlopen(tabs_url, timeout=3) as resp:
        tabs = json.loads(resp.read().decode())
    
    aistudio_tab = next((t for t in tabs if "aistudio.google.com" in t.get("url", "")), None)
    if not aistudio_tab:
        print("No aistudio tab open.")
        return

    print("Found tab:", aistudio_tab['url'])
    ws_url = aistudio_tab["webSocketDebuggerUrl"]
    
    async with websockets.connect(ws_url, ping_interval=None) as ws:
        msg = json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": "JSON.stringify({wiz: window.WIZ_global_data, ls: {...localStorage}})"
            }
        })
        await ws.send(msg)
        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        res = json.loads(raw)
        val = res.get("result", {}).get("result", {}).get("value")
        if val:
            data = json.loads(val)
            print("WIZ_global_data keys:", list(data.get('wiz', {}).keys()) if data.get('wiz') else None)
            ls = data.get('ls', {})
            print("localStorage keys:", list(ls.keys()))
            
            # Look for the magic string in WIZ
            if data.get('wiz'):
                for k, v in data['wiz'].items():
                    if isinstance(v, str) and len(v) > 500:
                        print(f"Long string in WIZ[{k}]: length {len(v)}, starts with {v[:10]}")
                    elif isinstance(v, list):
                        print(f"List in WIZ[{k}] with {len(v)} elements")

asyncio.run(check_aistudio())
