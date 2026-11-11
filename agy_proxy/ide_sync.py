import base64
import hashlib
import json
import logging
import os
import platform
import sqlite3
import time
from pathlib import Path
from typing import Any

from agy_proxy.auth.constants import (
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    GCP_TOS_CLIENT_ID,
    GCP_TOS_CLIENT_SECRET,
)
from agy_proxy.auth.token_utils import _decode_jwt_payload, _parse_expiry, parse_token_dict

logger = logging.getLogger("agy_proxy.ide_sync")

IDE_APP_DIRS = ("Antigravity IDE", "Antigravity", "Code", "Cursor")

USS_OAUTH_KEY = "antigravityUnifiedStateSync.oauthToken"
USS_OAUTH_SENTINEL = "oauthTokenInfoSentinelKey"
LEGACY_AGENT_INIT_KEY = "jetskiStateSync.agentManagerInitState"


# ---------------------------------------------------------------------------
# Pure-Python Protobuf Wire Codec for Antigravity IDE v2.6+ UnifiedStateSync
# ---------------------------------------------------------------------------


def encode_varint(value: int) -> bytes:
    """Encodes a non-negative integer as protobuf base-128 varint."""
    v = int(value) & 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while True:
        byte = v & 0x7F
        v >>= 7
        if v:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def decode_varint(buf: bytes, offset: int = 0) -> tuple[int, int]:
    """Decodes a protobuf base-128 varint starting at offset -> (value, new_offset)."""
    result = 0
    shift = 0
    pos = offset
    length = len(buf)
    while pos < length:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 70:
            raise ValueError("Varint too long")
    raise ValueError("Truncated varint in protobuf payload")


def encode_pb_field(field_num: int, wire_type: int, value: bytes | int | str) -> bytes:
    """Encodes a single protobuf field (tag + payload)."""
    tag = encode_varint((field_num << 3) | wire_type)
    if wire_type == 0:
        return tag + encode_varint(int(value))
    if wire_type == 2:
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        return tag + encode_varint(len(raw)) + raw
    raise ValueError(f"Unsupported protobuf wire_type: {wire_type}")


def decode_pb_fields(buf: bytes) -> list[tuple[int, int, Any]]:
    """Decodes top-level protobuf fields into [(field_num, wire_type, value), ...]."""
    fields: list[tuple[int, int, Any]] = []
    pos = 0
    length = len(buf)
    while pos < length:
        tag, pos = decode_varint(buf, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if field_num == 0:
            raise ValueError("Invalid protobuf field_num 0")
        if wire_type == 0:
            val, pos = decode_varint(buf, pos)
            fields.append((field_num, wire_type, val))
        elif wire_type == 1:
            if pos + 8 > length:
                raise ValueError("Truncated 64-bit protobuf field")
            val = buf[pos : pos + 8]
            pos += 8
            fields.append((field_num, wire_type, val))
        elif wire_type == 2:
            sub_len, pos = decode_varint(buf, pos)
            if pos + sub_len > length:
                raise ValueError("Truncated length-delimited protobuf field")
            val = buf[pos : pos + sub_len]
            pos += sub_len
            fields.append((field_num, wire_type, val))
        elif wire_type == 5:
            if pos + 4 > length:
                raise ValueError("Truncated 32-bit protobuf field")
            val = buf[pos : pos + 4]
            pos += 4
            fields.append((field_num, wire_type, val))
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire_type}")
    return fields


def _b64_decode_lenient(raw: str) -> bytes | None:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        padded = s + "=" * ((4 - len(s) % 4) % 4)
        if "-" in s or "_" in s:
            return base64.urlsafe_b64decode(padded.encode("ascii", errors="strict"))
        return base64.b64decode(padded.encode("ascii", errors="strict"))
    except Exception:
        return None


def decode_oauth_token_info_pb(raw_bytes: bytes) -> dict[str, Any]:
    """
    Decodes exa.language_server_pb.OAuthTokenInfo wire format:
      1: string access_token
      2: string token_type
      3: string refresh_token
      4: google.protobuf.Timestamp expiry (1: seconds, 2: nanos)
      6: bool is_gcp_tos
      7: bool enable_business_login
    """
    result: dict[str, Any] = {}
    if not raw_bytes:
        return result
    for fnum, wtype, val in decode_pb_fields(raw_bytes):
        if fnum == 1 and wtype == 2:
            result["access_token"] = val.decode("utf-8", errors="replace")
        elif fnum == 2 and wtype == 2:
            result["token_type"] = val.decode("utf-8", errors="replace")
        elif fnum == 3 and wtype == 2:
            result["refresh_token"] = val.decode("utf-8", errors="replace")
        elif fnum == 4 and wtype == 2:
            secs = 0
            nanos = 0
            for tfnum, twtype, tval in decode_pb_fields(val):
                if tfnum == 1 and twtype == 0:
                    secs = int(tval)
                elif tfnum == 2 and twtype == 0:
                    nanos = int(tval)
            if secs > 0:
                result["expiry_timestamp"] = float(secs) + (float(nanos) / 1e9)
        elif fnum == 5 and wtype == 2:
            # Guarded: if id_token JWT stored in optional extension field
            txt = val.decode("utf-8", errors="ignore")
            if txt.startswith("ey"):
                result["id_token"] = txt
        elif fnum == 6 and wtype == 0:
            result["is_gcp_tos"] = bool(val)
        elif fnum == 7 and wtype == 0:
            result["enable_business_login"] = bool(val)
    return result


def encode_oauth_token_info_pb(
    access_token: str = "",
    token_type: str = "Bearer",
    refresh_token: str = "",
    expiry_timestamp: float = 0.0,
    is_gcp_tos: bool = False,
    enable_business_login: bool = False,
) -> bytes:
    """Encodes OAuthTokenInfo protobuf bytes matching Antigravity IDE v2.6+."""
    chunks: list[bytes] = []
    if access_token:
        chunks.append(encode_pb_field(1, 2, access_token))
    if token_type:
        chunks.append(encode_pb_field(2, 2, token_type))
    if refresh_token:
        chunks.append(encode_pb_field(3, 2, refresh_token))
    if expiry_timestamp and expiry_timestamp > 0:
        secs = int(expiry_timestamp)
        nanos = int((float(expiry_timestamp) - secs) * 1e9)
        ts_bytes = encode_pb_field(1, 0, secs)
        if nanos > 0:
            ts_bytes += encode_pb_field(2, 0, nanos)
        chunks.append(encode_pb_field(4, 2, ts_bytes))
    if is_gcp_tos:
        chunks.append(encode_pb_field(6, 0, 1))
    if enable_business_login:
        chunks.append(encode_pb_field(7, 0, 1))
    return b"".join(chunks)


def decode_uss_topic_pb(raw_bytes: bytes) -> list[dict[str, Any]]:
    """
    Decodes exa.unified_state_sync_pb.Topic wire format:
      Topic { repeated DataEntry/map<string, Row> entries = 1; }
      DataEntry { string key = 1; Row value = 2; }
      Row { string value = 1; int64 e_tag = 2; }
    """
    entries: list[dict[str, Any]] = []
    if not raw_bytes:
        return entries
    for fnum, wtype, val in decode_pb_fields(raw_bytes):
        if fnum == 1 and wtype == 2:
            entry_key = ""
            row_value = ""
            e_tag = 0
            for dfnum, dwtype, dval in decode_pb_fields(val):
                if dfnum == 1 and dwtype == 2:
                    entry_key = dval.decode("utf-8", errors="replace")
                elif dfnum == 2 and dwtype == 2:
                    for rfnum, rwtype, rval in decode_pb_fields(dval):
                        if rfnum == 1 and rwtype == 2:
                            row_value = rval.decode("utf-8", errors="replace")
                        elif rfnum == 2 and rwtype == 0:
                            e_tag = int(rval)
            if entry_key or row_value:
                entries.append({"key": entry_key, "value": row_value, "e_tag": e_tag})
    return entries


def encode_uss_topic_pb(entries: list[dict[str, Any]]) -> bytes:
    """Encodes a list of {'key': str, 'value': str, 'e_tag': int} into exa.unified_state_sync_pb.Topic bytes."""
    topic_chunks: list[bytes] = []
    for entry in entries:
        row_chunks: list[bytes] = []
        val_str = str(entry.get("value") or "")
        e_tag = int(entry.get("e_tag") or 0)
        if val_str:
            row_chunks.append(encode_pb_field(1, 2, val_str))
        if e_tag:
            row_chunks.append(encode_pb_field(2, 0, e_tag))
        row_bytes = b"".join(row_chunks)
        data_entry = encode_pb_field(1, 2, str(entry.get("key") or "")) + encode_pb_field(2, 2, row_bytes)
        topic_chunks.append(encode_pb_field(1, 2, data_entry))
    return b"".join(topic_chunks)


def extract_oauth_entries_from_uss_value(raw_value: str) -> list[dict[str, Any]]:
    """Extracts normalized OAuth credential dicts from an Antigravity UnifiedStateSync base64/protobuf value."""
    extracted: list[dict[str, Any]] = []
    raw_bytes = _b64_decode_lenient(raw_value)
    if not raw_bytes:
        return extracted

    # 1. Decode as UnifiedStateSync Topic
    try:
        entries = decode_uss_topic_pb(raw_bytes)
        for ent in entries:
            ekey = (ent.get("key") or "").lower()
            rval = (ent.get("value") or "").strip()
            if not rval:
                continue
            if "oauthtoken" in ekey or "token" in ekey or not ekey:
                # Row.value might be JSON or base64(OAuthTokenInfo)
                if rval.startswith("{"):
                    try:
                        parsed = parse_token_dict(json.loads(rval))
                        if parsed:
                            extracted.append(parsed)
                            continue
                    except Exception:
                        pass
                inner_bytes = _b64_decode_lenient(rval)
                if inner_bytes:
                    try:
                        tok_pb = decode_oauth_token_info_pb(inner_bytes)
                        if tok_pb.get("refresh_token") or tok_pb.get("access_token"):
                            extracted.append(tok_pb)
                    except Exception:
                        pass
    except Exception:
        pass

    # 2. Fallback: raw_bytes itself might be OAuthTokenInfo protobuf directly
    if not extracted:
        try:
            tok_pb = decode_oauth_token_info_pb(raw_bytes)
            if tok_pb.get("refresh_token") or (
                isinstance(tok_pb.get("access_token"), str) and tok_pb["access_token"].startswith("ya29.")
            ):
                extracted.append(tok_pb)
        except Exception:
            pass

    return extracted


# ---------------------------------------------------------------------------
# IDE state.vscdb Discovery & Sync
# ---------------------------------------------------------------------------


def _candidate_vscdb_paths() -> list[Path]:
    system = platform.system()
    home = Path.home()
    candidates: list[Path] = []
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            for app_dir in IDE_APP_DIRS:
                candidates.append(Path(appdata) / app_dir / "User" / "globalStorage" / "state.vscdb")
    elif system == "Darwin":
        for app_dir in IDE_APP_DIRS:
            candidates.append(
                home / "Library" / "Application Support" / app_dir / "User" / "globalStorage" / "state.vscdb"
            )
    else:
        for app_dir in IDE_APP_DIRS:
            candidates.append(home / ".config" / app_dir / "User" / "globalStorage" / "state.vscdb")
    return candidates


def get_all_vscdb_paths() -> list[Path]:
    """Returns all existing IDE state.vscdb paths in priority order (Antigravity IDE -> Antigravity -> Code -> Cursor)."""
    return [p for p in _candidate_vscdb_paths() if p.exists()]


def get_vscdb_path() -> Path | None:
    """Returns the highest-priority existing Antigravity IDE / VS Code state.vscdb path, or None."""
    for p in _candidate_vscdb_paths():
        if p.exists():
            return p
    return None


def _extract_accounts_from_json_value(raw_val: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw_val)
    except Exception:
        return []

    found: list[dict[str, Any]] = []
    items_to_check: list[Any] = []
    if isinstance(data, list):
        items_to_check.extend(data)
    elif isinstance(data, dict):
        if "accounts" in data and isinstance(data["accounts"], list):
            items_to_check.extend(data["accounts"])
        if "oauthTokenInfo" in data and isinstance(data["oauthTokenInfo"], dict):
            items_to_check.append(data["oauthTokenInfo"])
        items_to_check.append(data)

    for item in items_to_check:
        parsed = parse_token_dict(item)
        if parsed and (parsed.get("refresh_token") or parsed.get("access_token")):
            found.append(parsed)
    return found


def sync_ide_accounts(pool, vscdb_paths: list[Path] | None = None) -> int:
    """
    Reads Antigravity IDE v2.6+ / VS Code state.vscdb (both protobuf UnifiedStateSync
    and legacy JSON ItemTable keys) and imports Google Cloud Code accounts into AccountPool.
    Returns the number of newly imported accounts.
    """
    if vscdb_paths is None:
        primary_vscdb = get_vscdb_path()
        if not primary_vscdb:
            logger.debug("IDE state.vscdb not found.")
            return 0
        paths_to_scan = [primary_vscdb]
        # When running against real host paths (not unit-test patched single path), check sibling IDE DBs too
        real_candidates = get_all_vscdb_paths()
        if primary_vscdb in real_candidates:
            for extra in real_candidates:
                if extra not in paths_to_scan:
                    paths_to_scan.append(extra)
    else:
        paths_to_scan = [Path(p) for p in vscdb_paths if p and Path(p).exists()]
        if not paths_to_scan:
            return 0

    imported_count = 0
    for vscdb_path in paths_to_scan:
        conn = None
        try:
            conn = sqlite3.connect(f"file:{vscdb_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT key, value FROM ItemTable "
                "WHERE key LIKE '%cloudcode%' OR key LIKE '%google%' "
                "OR key LIKE '%antigravity%' OR key LIKE '%jetski%'"
            )
            rows = cursor.fetchall()

            for key, value in rows:
                if not value or not isinstance(value, str):
                    continue

                extracted_items: list[dict[str, Any]] = []
                val_stripped = value.strip()
                if val_stripped.startswith("{") or val_stripped.startswith("["):
                    extracted_items.extend(_extract_accounts_from_json_value(val_stripped))

                if not extracted_items and (
                    key == USS_OAUTH_KEY
                    or key == LEGACY_AGENT_INIT_KEY
                    or "oauthtoken" in key.lower()
                    or "unifiedstatesync" in key.lower()
                    or not val_stripped.startswith("{")
                ):
                    extracted_items.extend(extract_oauth_entries_from_uss_value(val_stripped))

                for acc_data in extracted_items:
                    refresh_token = (acc_data.get("refresh_token") or "").strip()
                    access_token = acc_data.get("access_token")
                    id_token = acc_data.get("id_token")
                    jwt_claims = _decode_jwt_payload(id_token) if id_token else {}
                    email = acc_data.get("email") or jwt_claims.get("email")
                    name = acc_data.get("name") or jwt_claims.get("name")
                    is_gcp_tos = bool(acc_data.get("is_gcp_tos", False))
                    expiry_ts = _parse_expiry(acc_data.get("expiry_timestamp") or acc_data.get("expiry") or 0.0)

                    if not refresh_token:
                        continue

                    exists = False
                    for existing_acc in pool.accounts.values():
                        if existing_acc.auth_method != "consumer":
                            continue
                        if existing_acc.refresh_token == refresh_token:
                            exists = True
                            break
                        if email and existing_acc.email and existing_acc.email.lower() == email.lower():
                            exists = True
                            break

                    if not exists:
                        seed = email if email else refresh_token
                        acc_id = f"ide_{hashlib.md5(seed.encode('utf-8')).hexdigest()[:8]}"

                        from agy_proxy.auth.base import AccountSession

                        client_id = acc_data.get("client_id") or (
                            GCP_TOS_CLIENT_ID if is_gcp_tos else DEFAULT_CLIENT_ID
                        )
                        client_secret = acc_data.get("client_secret") or (
                            GCP_TOS_CLIENT_SECRET if client_id == GCP_TOS_CLIENT_ID else DEFAULT_CLIENT_SECRET
                        )

                        acc = AccountSession(
                            account_id=acc_id,
                            refresh_token=refresh_token,
                            access_token=access_token,
                            expiry_timestamp=expiry_ts,
                            email=email,
                            name=name,
                            id_token=id_token,
                            auth_method="consumer",
                            client_id=client_id,
                            client_secret=client_secret,
                            is_gcp_tos=is_gcp_tos,
                            enabled=True,
                            on_token_refreshed=pool.save_accounts,
                        )
                        pool.accounts[acc_id] = acc
                        imported_count += 1
                        logger.info("Imported IDE account %s from %s (%s)", email or acc_id, key, vscdb_path.name)
        except Exception as e:
            logger.warning("Failed to sync IDE state from %s: %s", vscdb_path, e)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    if imported_count > 0:
        pool.save_accounts()

    return imported_count


def write_ide_vscdb_oauth_token(account: Any, vscdb_path: Path | None = None) -> bool:
    """
    Writes/updates the active OAuth token into Antigravity IDE v2.6+ state.vscdb
    (`antigravityUnifiedStateSync.oauthToken` protobuf Topic -> `oauthTokenInfoSentinelKey`).
    Returns True if updated, False if no state.vscdb found or write skipped.
    """
    target_db = Path(vscdb_path) if vscdb_path else get_vscdb_path()
    if not target_db or not target_db.is_file():
        return False

    refresh_token = getattr(account, "refresh_token", "") or ""
    access_token = getattr(account, "access_token", "") or ""
    if not refresh_token and not access_token:
        return False

    expiry_ts = float(getattr(account, "expiry_timestamp", 0.0) or 0.0)
    is_gcp_tos = bool(getattr(account, "is_gcp_tos", False))
    token_pb = encode_oauth_token_info_pb(
        access_token=access_token,
        token_type="Bearer",
        refresh_token=refresh_token,
        expiry_timestamp=expiry_ts,
        is_gcp_tos=is_gcp_tos,
        enable_business_login=is_gcp_tos,
    )
    row_value_b64 = base64.b64encode(token_pb).decode("ascii")
    new_etag = int(time.time() * 1000)

    conn = None
    try:
        conn = sqlite3.connect(str(target_db))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ItemTable'")
        if not cursor.fetchone():
            return False

        cursor.execute("SELECT value FROM ItemTable WHERE key = ?", (USS_OAUTH_KEY,))
        row = cursor.fetchone()
        entries: list[dict[str, Any]] = []
        if row and row[0]:
            raw_existing = _b64_decode_lenient(str(row[0]))
            if raw_existing:
                try:
                    entries = decode_uss_topic_pb(raw_existing)
                except Exception:
                    entries = []

        updated = False
        for ent in entries:
            if ent.get("key") == USS_OAUTH_SENTINEL:
                ent["value"] = row_value_b64
                ent["e_tag"] = new_etag
                updated = True
                break
        if not updated:
            entries.append({"key": USS_OAUTH_SENTINEL, "value": row_value_b64, "e_tag": new_etag})

        topic_b64 = base64.b64encode(encode_uss_topic_pb(entries)).decode("ascii")
        cursor.execute(
            "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
            (USS_OAUTH_KEY, topic_b64),
        )
        conn.commit()
        logger.info("Updated %s in %s for %s", USS_OAUTH_KEY, target_db, getattr(account, "email", "account"))
        return True
    except Exception as e:
        logger.warning("Failed writing IDE state.vscdb OAuth token to %s: %s", target_db, e)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
