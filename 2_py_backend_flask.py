from flask import Flask, request, jsonify

app = Flask(__name__)

@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

@app.get("/")
def index():
    greet = request.args.get("greet", "")
    name = request.args.get("name", "")
    return f"{greet} {name}"

@app.get("/json")
def json_endpoint():
    greet = request.args.get("greet", "")
    name = request.args.get("name", "")
    return jsonify({"greet": greet, "name": name})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

