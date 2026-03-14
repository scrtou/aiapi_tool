from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from libs.core.config import env_bool, env_list


def _normalized(values: list[str], fallback: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values if value.strip()]
    return cleaned or fallback


def configure_cors(app: FastAPI):
    allow_origins = _normalized(env_list('CORS_ALLOW_ORIGINS', ['*']), ['*'])
    allow_methods = _normalized(env_list('CORS_ALLOW_METHODS', ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']), ['*'])
    allow_headers = _normalized(env_list('CORS_ALLOW_HEADERS', ['*']), ['*'])
    expose_headers = _normalized(env_list('CORS_EXPOSE_HEADERS', []), [])
    allow_credentials = env_bool('CORS_ALLOW_CREDENTIALS', False)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
        expose_headers=expose_headers,
        max_age=600,
    )
