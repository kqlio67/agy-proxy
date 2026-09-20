import asyncio
from agy_proxy.auth.gemini_web import GeminiWebSession

async def main():
    acc = GeminiWebSession(
        account_id="gw_test",
        name="Gemini Web",
        cdp_port=9222,
        enabled=False,
    )
    print("Initial enabled:", acc.enabled)
    print("Has cookies:", bool(acc._cookies.get("__Secure-1PSID")))
    
    token = await acc.get_valid_token()
    print("After get_valid_token enabled:", acc.enabled)
    print("Has cookies now:", bool(acc._cookies.get("__Secure-1PSID")), "count:", len(acc._cookies))
    print("AT token:", bool(acc._at_token))
    
    save_dict = acc.to_save_dict()
    print("to_save_dict enabled:", save_dict.get("enabled"))

if __name__ == "__main__":
    asyncio.run(main())
