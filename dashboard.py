"""
Dashboard Pemantau Kualitas Udara - SIC 8 (Kivvi)
Jalankan: pip install -r requirements.txt && python dashboard.py
Buka: http://127.0.0.1:8050
"""
import json
import os
import threading
from collections import deque
from datetime import datetime

import paho.mqtt.client as mqtt
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html

# ====== Konfigurasi ======
MQTT_HOST = os.getenv("MQTT_HOST", "broker.hivemq.com")
MQTT_PORT = 1883
TOPIC_DATA = "kivvi-sic8-isis/airquality/data"      # sama dengan sketch.ino
TOPIC_STATUS = "kivvi-sic8-isis/airquality/status"  # sama dengan sketch.ino

THRESH_WASPADA = 1500
THRESH_BAHAYA = 2500
MAX_POINTS = 300
TRAFFIC_WINDOW = int(os.getenv("TRAFFIC_WINDOW", "30"))  # default 30, bisa dipercepat saat demo
TRAFFIC_SHARE = 0.8

COLORS = {"NORMAL": "#2e7d32", "WASPADA": "#f9a825", "BAHAYA": "#c62828"}
GREY = "#757575"

# ====== Data bersama (thread MQTT dan Dash) ======
lock = threading.Lock()
times = deque(maxlen=MAX_POINTS)
gas_vals = deque(maxlen=MAX_POINTS)
state = {"status": "-", "level": "-", "latch": False, "gas": None, "device": "belum ada data"}


# ====== MQTT ======
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print("MQTT gagal terhubung:", reason_code)
        return
    print("MQTT terhubung ke", MQTT_HOST)
    client.subscribe([(TOPIC_DATA, 0), (TOPIC_STATUS, 0)])  # diulang tiap reconnect


def on_disconnect(client, userdata, flags, reason_code, properties):
    print("MQTT terputus, mencoba menyambung ulang...", reason_code)


def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8", errors="replace")

    if msg.topic == TOPIC_STATUS:
        with lock:
            state["device"] = payload  # "online" / "offline"
        return

    try:
        data = json.loads(payload)
        gas = int(data["gas"])
    except (ValueError, KeyError, TypeError):
        print("Payload tidak valid:", payload)
        return

    with lock:
        times.append(datetime.now())
        gas_vals.append(gas)
        state.update(
            gas=gas,
            level=data.get("level", "-"),
            status=data.get("status", "-"),
            latch=bool(data.get("latch", False)),
        )


def start_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="kivvi-dashboard-isis")
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    return client


# ====== Indikasi lalu lintas (UC-07) ======
def traffic_hint():
    recent = list(gas_vals)[-TRAFFIC_WINDOW:]
    if len(recent) < TRAFFIC_WINDOW:
        return "Data belum cukup untuk indikasi lalu lintas."
    share = sum(v >= THRESH_WASPADA for v in recent) / len(recent)
    if share >= TRAFFIC_SHARE:
        return "Polusi tinggi konsisten: indikasi awal ruas padat (belum terbukti, perlu data pendukung)."
    return "Pola polusi tidak menunjukkan indikasi kepadatan."


# ====== Layout ======
app = Dash(__name__)
app.title = "Pemantau Kualitas Udara - Kivvi"

app.layout = html.Div(
    style={"fontFamily": "Arial, sans-serif", "maxWidth": "960px", "margin": "0 auto", "padding": "16px"},
    children=[
        html.H2("Pemantau Kualitas Udara Real-Time"),
        html.Div(id="status-box"),
        html.P(id="meta", style={"marginTop": "8px", "color": "#444"}),
        dcc.Graph(id="gas-graph"),
        html.P(id="traffic", style={"fontStyle": "italic"}),
        dcc.Interval(id="tick", interval=1000, n_intervals=0),
    ],
)


@app.callback(
    Output("gas-graph", "figure"),
    Output("status-box", "children"),
    Output("status-box", "style"),
    Output("meta", "children"),
    Output("traffic", "children"),
    Input("tick", "n_intervals"),
)
def refresh(_):
    with lock:
        x, y = list(times), list(gas_vals)
        st = dict(state)
        hint = traffic_hint()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines+markers", name="Nilai gas (ADC)"))
    fig.add_hline(y=THRESH_WASPADA, line_dash="dash", line_color=COLORS["WASPADA"], annotation_text="Waspada")
    fig.add_hline(y=THRESH_BAHAYA, line_dash="dash", line_color=COLORS["BAHAYA"], annotation_text="Bahaya")
    fig.update_layout(
        template="plotly_white",
        xaxis_title="Waktu",
        yaxis_title="Nilai ADC (0-4095)",
        yaxis_range=[0, 4095],
        margin={"l": 50, "r": 20, "t": 20, "b": 40},
        uirevision="keep",
    )

    status = st["status"]
    offline = st["device"] == "offline"

    label = f"STATUS: {status}" + ("  (ALARM TERKUNCI)" if st["latch"] else "")
    if offline:  # perangkat mati
        label += "  [PERANGKAT OFFLINE]"
        hint = "Perangkat offline: indikasi lalu lintas tidak aktif."

    box_style = {
        "padding": "16px", "borderRadius": "8px", "color": "white", "fontSize": "28px",
        "fontWeight": "bold", "textAlign": "center",
        "backgroundColor": GREY if offline else COLORS.get(status, GREY),
    }
    meta = f"Gas: {st['gas']}  |  Level sensor: {st['level']}  |  Perangkat: {st['device']}"
    return fig, label, box_style, meta, hint


if __name__ == "__main__":
    start_mqtt()
    app.run(debug=False)