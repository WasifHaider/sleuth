from urllib.parse import urlencode

import httpx

from sleuth.config import Config

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"


def build_authorize_url(state: str, config: Config) -> str:
    params = {
        "client_id": config.github_client_id,
        "redirect_uri": f"{config.frontend_url}/auth/github/callback",
        "scope": "read:user user:email",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str, config: Config) -> dict:
    """Exchange an OAuth code for a normalized GitHub profile dict.

    Raw httpx calls, no SDK: POST for the access token, then GET the profile.
    """
    with httpx.Client() as client:
        token_resp = client.post(
            ACCESS_TOKEN_URL,
            data={
                "client_id": config.github_client_id,
                "client_secret": config.github_client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        user_resp = client.get(
            USER_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        user_resp.raise_for_status()
        profile = user_resp.json()

    return {
        "github_id": profile["id"],
        "email": profile.get("email"),
        "name": profile.get("name") or profile.get("login"),
        "avatar_url": profile.get("avatar_url"),
    }
