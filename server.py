#!/usr/bin/env python3
import json
import random
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent

SAMPLE_MIDIS = [48, 51, 54, 57, 60, 63, 66, 69, 72]
FIXED_C4_ROOT = 60
TRIADS = [
    {"degree": 0, "intervals": [0, 4, 7], "hebrew": "I"},
    {"degree": 1, "intervals": [2, 5, 9], "hebrew": "ii"},
    {"degree": 2, "intervals": [4, 7, 11], "hebrew": "iii"},
    {"degree": 3, "intervals": [5, 9, 12], "hebrew": "IV"},
    {"degree": 4, "intervals": [7, 11, 14], "hebrew": "V"},
    {"degree": 5, "intervals": [9, 12, 16], "hebrew": "vi"},
    {"degree": 6, "intervals": [11, 14, 17], "hebrew": "vii°"},
]
COMMON_PROGRESSIONS = {
    0: [0, 4, 3],
    1: [1, 4, 5],
    2: [2, 5, 6],
    3: [3, 0, 4],
    4: [4, 0, 1],
    5: [5, 1, 2],
    6: [6, 0, 2],
}


def get_valid_root(min_note=60, max_note=72, white_keys_only=False):
    if min_note >= max_note:
        return None
    white_keys = {0, 2, 4, 5, 7, 9, 11}
    valid = []
    for midi in range(min_note, max_note + 1):
        if (not white_keys_only) or (midi % 12 in white_keys):
            valid.append(midi)
    return random.choice(valid) if valid else None


def get_c_root_in_range(min_note=60, max_note=72):
    if min_note >= max_note:
        return None
    c_notes = [m for m in range(min_note, max_note + 1) if (m % 12 == 0)]
    return random.choice(c_notes) if c_notes else None


def select_next_degree(last_degree=None, bias_strength=50, adaptive_strength=50, degree_stats=None):
    all_degrees = list(range(7))
    weights = {d: 1.0 for d in all_degrees}

    # Harmonic progression bias: increase weights instead of hard-restricting choices.
    if last_degree is not None and bias_strength > 0:
        progression = COMMON_PROGRESSIONS.get(last_degree, all_degrees)
        progression_boost = 1.0 + (bias_strength / 100) * 1.8
        for d in progression:
            weights[d] *= progression_boost

    # Adaptive weakness focus: boost weaker degrees, still keep exploration.
    if adaptive_strength > 0 and degree_stats:
        acc_items = []
        for d in all_degrees:
            ds = degree_stats.get(str(d), degree_stats.get(d, {}))
            asked = int(ds.get("asked", 0))
            correct = int(ds.get("correct", 0))
            acc = (correct / asked) if asked > 0 else 0
            acc_items.append((acc, d))
        acc_items.sort(key=lambda x: x[0])
        weakest = [d for _, d in acc_items[: max(1, int(7 * 0.4))]]
        adaptive_boost = 1.0 + (adaptive_strength / 100) * 1.6
        for d in weakest:
            weights[d] *= adaptive_boost

    # Anti-repeat: reduce immediate repeats for more variety.
    if last_degree is not None:
        weights[last_degree] *= 0.3

    degrees = list(weights.keys())
    degree_weights = [weights[d] for d in degrees]
    return random.choices(degrees, weights=degree_weights, k=1)[0]


class EarTrainerHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        raw_path = urlparse(path).path
        if raw_path == "/":
            return str(ROOT / "index.html")
        return str(ROOT / raw_path.lstrip("/"))

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _send_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        payload = self._read_json()

        if path == "/api/generate-third":
            min_note = int(payload.get("minNote", 60))
            max_note = int(payload.get("maxNote", 72))
            white = bool(payload.get("whiteKeysOnly", False))
            root = get_valid_root(min_note, max_note, white)
            if root is None:
                return self._send_json(
                    {"error": "NO_VALID_ROOT", "message": "אין תווים זמינים בטווח שנבחר."},
                    status=HTTPStatus.BAD_REQUEST,
                )
            is_major = random.random() < 0.5
            notes = [root, root + 4] if is_major else [root, root + 3]
            return self._send_json(
                {"type": "third", "quality": "major" if is_major else "minor", "root": root, "notes": notes, "inversion": 0}
            )

        if path == "/api/generate-triad":
            min_note = int(payload.get("minNote", 60))
            max_note = int(payload.get("maxNote", 72))
            # Triad mode stays in C major, but octave/root follows the selected range.
            root = get_c_root_in_range(min_note, max_note)
            if root is None:
                return self._send_json(
                    {"error": "NO_C_IN_RANGE", "message": "אין דו (C) בטווח שנבחר."},
                    status=HTTPStatus.BAD_REQUEST,
                )
            degree = select_next_degree(
                payload.get("lastDegree"),
                int(payload.get("biasStrength", 50)),
                int(payload.get("adaptiveStrength", 50)),
                payload.get("degreeStats", {}),
            )
            triad = TRIADS[degree]
            notes = [root + i for i in triad["intervals"]]
            use_inv = bool(payload.get("useRandomInversions", False))
            inversion = random.randint(0, 2) if use_inv else 0
            return self._send_json(
                {
                    "type": "triad",
                    "degree": degree,
                    "triadName": triad["hebrew"],
                    "root": root,
                    "notes": notes,
                    "inversion": inversion,
                }
            )

        if path == "/api/check-third":
            expected = payload.get("expected")
            guess = payload.get("guess")
            return self._send_json({"correct": expected == guess})

        if path == "/api/check-triad":
            expected = int(payload.get("expected", -1))
            guess = int(payload.get("guess", -2))
            return self._send_json({"correct": expected == guess})

        if path == "/api/play-degree":
            degree = int(payload.get("degree", 0))
            # Exploration/play-degree is intentionally fixed:
            # J/K/L/;/U/I/O => C, Dm, Em, F, G, Am, Bdim (always same voicing).
            root = FIXED_C4_ROOT
            triad = TRIADS[max(0, min(6, degree))]
            notes = [root + i for i in triad["intervals"]]
            inversion = 0
            return self._send_json({"degree": degree, "root": root, "notes": notes, "inversion": inversion})

        return self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)


def run_server(port=4173):
    server = ThreadingHTTPServer(("127.0.0.1", port), EarTrainerHandler)
    print(f"Serving on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
