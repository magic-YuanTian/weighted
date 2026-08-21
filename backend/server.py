"""Flask entry point: serves the built frontend and the agent API."""

import os

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from agent_routes import bp as agent_bp

FRONTEND_BUILD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "frontend", "build")

app = Flask(__name__, static_folder=FRONTEND_BUILD, static_url_path="/")
CORS(app, resources={r"/*": {"origins": "*"}})
app.register_blueprint(agent_bp)


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
