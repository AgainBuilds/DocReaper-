
"""Google Drive OAuth and upload helpers."""
from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import secrets
import time
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"


def authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/drive.file",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def create_state(secret: str) -> str:
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    payload = f"{timestamp}.{nonce}"

    signature = hmac.new(
        secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    return f"{payload}.{signature}"


def verify_state(state: str, secret: str, max_age: int = 600) -> bool:
    try:
        timestamp, nonce, signature = state.split(".", 2)
        payload = f"{timestamp}.{nonce}"

        age = int(time.time()) - int(timestamp)
        if age < 0 or age > max_age:
            return False

        expected = hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


async def exchange_code(
    *,
    code: str,
    client_id: str,
    client_secret: str = "",
    redirect_uri: str,
) -> dict:
    """Exchange an authorization code for Google tokens."""
    data = {
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    # Google currently permits this field to be omitted.
    if client_secret.strip():
        data["client_secret"] = client_secret.strip()

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(TOKEN_URL, data=data)
        response.raise_for_status()
        return response.json()


def upload_bytes(
    data: bytes,
    filename: str,
    *,
    client_id: str,
    refresh_token: str,
    client_secret: str = "",
    folder_id: str = "",
) -> dict:
    """Refresh the access token and upload a file to Google Drive."""
    token_data = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    if client_secret.strip():
        token_data["client_secret"] = client_secret.strip()

    with httpx.Client(timeout=120) as client:
        token_response = client.post(
            TOKEN_URL,
            data=token_data,
        )
        token_response.raise_for_status()

        access_token = token_response.json()["access_token"]

        metadata = {"name": filename}
        if folder_id:
            metadata["parents"] = [folder_id]

        mime_type = (
            mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )

        boundary = "docreaper_upload_boundary"

        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--".encode()

        response = client.post(
            f"{UPLOAD_URL}?uploadType=multipart&fields=id,name,mimeType,size,webViewLink",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
            content=body,
        )
        response.raise_for_status()

        result = response.json()
        result["webViewLink"] = result.get(
            "webViewLink",
            f"https://drive.google.com/file/d/{result['id']}/view",
        )

        return result
