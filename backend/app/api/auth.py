"""
Authentication API — lightweight token-based auth.

Credentials are read from AUTH_USERNAME / AUTH_PASSWORD env vars.
A successful POST /api/auth/login returns a signed token that must be
sent as ``Authorization: Bearer <token>`` on every subsequent request.
"""

from flask import Blueprint, request, jsonify, current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

auth_bp = Blueprint('auth', __name__)


def _get_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])


def verify_token(token, max_age=86400 * 7):
    """Return the username embedded in *token*, or None if invalid/expired."""
    try:
        data = _get_serializer().loads(token, max_age=max_age)
        return data.get('user')
    except (BadSignature, SignatureExpired):
        return None


@auth_bp.route('/login', methods=['POST'])
def login():
    body = request.get_json(silent=True) or {}
    username = body.get('username', '')
    password = body.get('password', '')

    expected_user = current_app.config.get('AUTH_USERNAME')
    expected_pass = current_app.config.get('AUTH_PASSWORD')

    # If no credentials are configured, auth is disabled — reject all logins
    if not expected_user or not expected_pass:
        return jsonify(success=False, error='Authentication is not configured on the server'), 500

    if username == expected_user and password == expected_pass:
        token = _get_serializer().dumps({'user': username})
        return jsonify(success=True, token=token, username=username)

    return jsonify(success=False, error='Invalid username or password'), 401


@auth_bp.route('/me', methods=['GET'])
def me():
    """Return the currently authenticated user (useful for session checks)."""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        user = verify_token(auth_header[7:])
        if user:
            return jsonify(success=True, username=user)
    return jsonify(success=False, error='Not authenticated'), 401
