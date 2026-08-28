"""Flask entry point: serves the built frontend and the agent API."""

import hmac
import os

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

from agent_routes import bp as agent_bp

FRONTEND_BUILD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "frontend", "build")

app = Flask(__name__, static_folder=FRONTEND_BUILD, static_url_path="/")
CORS(app, resources={r"/*": {"origins": "*"}})
app.register_blueprint(agent_bp)

# One shared password over the whole app. A run is a long chain of large model
# calls billed to whoever owns OPENAI_API_KEY, so a link handed to a few people
# must not also be a link handed to anyone who finds it. Unset means no gate,
# which leaves local development exactly as it was.
PASSWORD = os.environ.get("WEIGHTTEXT_PASSWORD", "")


@app.before_request
def require_password():
    if not PASSWORD:
        return None
    # The Codex sandbox curls the checker without credentials. The endpoint
    # is read-only and gated on knowing a live session id, so exempting it
    # exposes a requirement report, not the model or the workspace.
    if request.path == "/api/agent/check":
        return None
    auth = request.authorization
    # compare_digest, not ==: the comparison is over the network and a shared
    # password is short enough for timing to matter.
    if auth and hmac.compare_digest(auth.password or "", PASSWORD):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "not authorised"}), 401, {
            "WWW-Authenticate": 'Basic realm="WeightText"'}
    return Response("Sign in to WeightText.", 401,
                    {"WWW-Authenticate": 'Basic realm="WeightText"'})


@app.route("/")
def serve_index():
    return send_from_directory(app.static_folder, "index.html")


@app.errorhandler(404)
def spa_fallback(e):
    # Client-side routes fall back to index.html; unknown API paths stay 404.
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    app.run(debug=False, threaded=True, host="0.0.0.0",
            port=int(os.environ.get("PORT", 5091)))
