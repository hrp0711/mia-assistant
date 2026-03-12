from flask import Flask, request, jsonify
from openai import OpenAI
import logging
import os
from dotenv import load_dotenv
import requests
import urllib.parse
import re
import json

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")

user_sessions = {}


# ─────────────────────────────────────────
# VERIFICACIÓN DEL WEBHOOK (GET)
# ─────────────────────────────────────────
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == META_VERIFY_TOKEN:
        logger.info("✅ Webhook verificado por Meta")
        return challenge, 200
    return "Token inválido", 403


# ─────────────────────────────────────────
# RECEPCIÓN DE MENSAJES (POST)
# ─────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def receive_webhook():
    data = request.get_json()
    logger.info("📩 JSON recibido: %s", data)

    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            return jsonify({"status": "ok"}), 200

        message = value["messages"][0]
        from_number = message["from"]
        msg_type = message.get("type")

        if msg_type == "text":
            incoming_msg = message["text"]["body"].strip()
            logger.info("📨 Mensaje de %s: %s", from_number, incoming_msg)
            reply = procesar_mensaje(from_number, incoming_msg)
            send_whatsapp_message(from_number, reply)
        else:
            send_whatsapp_message(
                from_number,
                "Por ahora solo puedo responder mensajes de texto. 😊 Escríbeme qué producto buscas."
            )

    except Exception as e:
        logger.error("❌ Error procesando webhook: %s", e)

    return jsonify({"status": "ok"}), 200


# ─────────────────────────────────────────
# LÓGICA DE CONVERSACIÓN
# ─────────────────────────────────────────
def procesar_mensaje(sender, incoming_msg):
    sesion = user_sessions.get(sender, {})
    msg_lower = incoming_msg.lower()

    if any(s in msg_lower for s in ["hola", "buenas", "buenos días", "hi", "inicio", "start"]):
        user_sessions[sender] = {"estado": "esperando_producto"}
        return "¡Hola! 👋 Soy *MIA*, tu asistente de compras.\n\n¿Qué producto quieres buscar? 🛍️\n\nEscríbeme lo que necesitas, por ejemplo: _licuadora_, _televisor 50 pulgadas_, _zapatos deportivos_."

    elif sesion.get("estado") == "esperando_producto":
        producto = incoming_msg.strip()
        opciones = buscar_productos(producto)

        if opciones:
            user_sessions[sender] = {
                "estado": "mostrando_resultados",
                "producto": producto,
                "opciones": opciones
            }
            return crear_mensaje_resultados(producto, opciones)
        else:
            user_sessions[sender] = {"estado": "esperando_producto"}
            return f"😕 Tuve un problema buscando *{producto}*. ¿Puedes intentarlo de nuevo?"

    elif sesion.get("estado") == "mostrando_resultados" and incoming_msg in ["1", "2", "3"]:
        opciones = sesion.get("opciones", {})
        opcion_map = {"1": "mas_vendida", "2": "mejor_calificada", "3": "mas_barata"}
        opcion = opciones.get(opcion_map[incoming_msg], {})

        es_estimado = opciones.get("estimado", False)
        nombre = opcion.get("nombre", "Producto")
        precio = opcion.get("precio", "")
        if es_estimado:
            precio += " (⚠️ Precio estimado)"

        nombre_url = re.sub(r"[^a-zA-Z0-9]+", "-", nombre.lower()).strip("-")
        enlace = f"https://listado.mercadolibre.com.co/{nombre_url}"

        user_sessions[sender] = {"estado": "esperando_producto"}
        return (
            f"✅ Elegiste:\n\n*{nombre}*\n{opcion.get('descripcion', '')}\n"
            f"💰 {precio}\n🔗 {enlace}\n\n"
            f"¿Quieres buscar otro producto? Escribe *hola* para empezar de nuevo 😊"
        )

    else:
        user_sessions[sender] = {"estado": "esperando_producto"}
        return "¡Hola! 👋 Soy *MIA*.\n\n¿Qué producto quieres buscar? 🛍️"


# ─────────────────────────────────────────
# BÚSQUEDA DE PRODUCTOS
# ─────────────────────────────────────────
def buscar_productos(producto):
    es_estimado = False
    try:
        ml_url = f"https://api.mercadolibre.com/sites/MCO/search?q={urllib.parse.quote(producto)}&limit=3"
        response = requests.get(ml_url, timeout=5)
        if response.status_code == 403:
            logger.warning("API MercadoLibre retornó 403. Usando GPT (precios estimados).")
            es_estimado = True
        elif response.status_code == 200:
            data = response.json()
            if "results" in data and len(data["results"]) >= 3:
                res = data["results"]
                return {
                    "estimado": False,
                    "mas_vendida": {
                        "nombre": res[0]["title"],
                        "precio": f"${res[0]['price']:,.0f} COP".replace(",", "."),
                        "descripcion": "Opción destacada en MercadoLibre",
                        "razon": "Basado en resultados de búsqueda reales"
                    },
                    "mejor_calificada": {
                        "nombre": res[1]["title"],
                        "precio": f"${res[1]['price']:,.0f} COP".replace(",", "."),
                        "descripcion": "Excelente opción en MercadoLibre",
                        "razon": "Basado en resultados de búsqueda reales"
                    },
                    "mas_barata": {
                        "nombre": res[2]["title"],
                        "precio": f"${res[2]['price']:,.0f} COP".replace(",", "."),
                        "descripcion": "Alternativa en MercadoLibre",
                        "razon": "Basado en resultados de búsqueda reales"
                    }
                }
            else:
                es_estimado = True
        else:
            es_estimado = True
    except Exception as e:
        logger.error("Error con MercadoLibre API: %s", e)
        es_estimado = True

    try:
        prompt = f"""Eres un asistente de compras experto en Colombia. 
El usuario quiere comprar: {producto}

Devuelve exactamente 3 opciones de productos reales que se venden en Colombia con este formato JSON:
{{
  "mas_vendida": {{
    "nombre": "nombre del producto",
    "precio": "precio en pesos colombianos",
    "descripcion": "descripción breve de 1 línea",
    "razon": "por qué es la más vendida"
  }},
  "mejor_calificada": {{
    "nombre": "nombre del producto",
    "precio": "precio en pesos colombianos", 
    "descripcion": "descripción breve de 1 línea",
    "razon": "por qué es la mejor calificada"
  }},
  "mas_barata": {{
    "nombre": "nombre del producto",
    "precio": "precio en pesos colombianos",
    "descripcion": "descripción breve de 1 línea",
    "razon": "por qué es la más barata"
  }}
}}

Solo responde con el JSON, sin texto adicional."""

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )

        content = response.choices[0].message.content.strip()
        data = json.loads(content)
        data["estimado"] = es_estimado
        return data

    except Exception as e:
        logger.error("Error buscando productos con GPT: %s", e)
        return None


# ─────────────────────────────────────────
# FORMATO DE RESULTADOS
# ─────────────────────────────────────────
def crear_mensaje_resultados(producto, opciones):
    msg = f"🔍 Encontré estas opciones para *{producto}*:\n\n"
    es_estimado = opciones.get("estimado", False)

    def format_opcion(op):
        nombre = op["nombre"]
        precio = op["precio"]
        if es_estimado:
            precio += " (⚠️ Precio estimado)"
        nombre_url = re.sub(r"[^a-zA-Z0-9]+", "-", nombre.lower()).strip("-")
        enlace = f"https://listado.mercadolibre.com.co/{nombre_url}"
        return precio, enlace

    mv = opciones.get("mas_vendida", {})
    if mv:
        precio_mv, enlace_mv = format_opcion(mv)
        msg += f"🏆 *Más vendida*\n{mv['nombre']}\n{mv['descripcion']}\n💰 {precio_mv}\n🔗 {enlace_mv}\n_{mv['razon']}_\n\n"

    mc = opciones.get("mejor_calificada", {})
    if mc:
        precio_mc, enlace_mc = format_opcion(mc)
        msg += f"⭐ *Mejor calificada*\n{mc['nombre']}\n{mc['descripcion']}\n💰 {precio_mc}\n🔗 {enlace_mc}\n_{mc['razon']}_\n\n"

    mb = opciones.get("mas_barata", {})
    if mb:
        precio_mb, enlace_mb = format_opcion(mb)
        msg += f"💰 *Más barata*\n{mb['nombre']}\n{mb['descripcion']}\n💰 {precio_mb}\n🔗 {enlace_mb}\n_{mb['razon']}_\n\n"

    msg += "¿Te interesa alguna opción? Escribe *1*, *2* o *3* 😊"
    return msg


# ─────────────────────────────────────────
# ENVÍO DE MENSAJES POR META CLOUD API
# ─────────────────────────────────────────
def send_whatsapp_message(to_number, message_text):
    url = f"https://graph.facebook.com/v23.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message_text}
    }
    response = requests.post(url, headers=headers, json=payload)
    logger.info("📤 Meta API [%s]: %s", response.status_code, response.text)
    return response.json()


# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────
@app.route("/")
def home():
    return "MIA está viva 🚀"


# ─────────────────────────────────────────
# ARRANQUE
# ─────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🚀 MIA corriendo en puerto 5001...")
    app.run(host="0.0.0.0", port=5001, debug=True)