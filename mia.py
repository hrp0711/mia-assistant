from openai import OpenAI
from flask import Flask, request, jsonify
import requests
import json
import pytz
import holidays
from datetime import datetime
import os
from dotenv import load_dotenv
load_dotenv()

# Cargar configuración del negocio
with open("negocio.json", "r", encoding="utf-8") as f:
    NEGOCIO = json.load(f)

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN")


def construir_prompt(negocio):
    return f"""
Eres MIA, la asistente virtual de {negocio['nombre']}, una {negocio['descripcion']}.

HORARIO DE ATENCIÓN: {negocio['horario']}
- Si el cliente escribe fuera de este horario, avísale y continúa atendiendo normalmente.
- Si hoy es festivo en Colombia, avísale y continúa atendiendo normalmente.

INFORMACIÓN DEL NEGOCIO:
📍 Dirección: {negocio['direccion']}
🕘 Horario: {negocio['horario']}

CATÁLOGO Y PRECIOS:
{negocio['catalogo']}

PAGOS:
{negocio['pagos']}

INSTRUCCIONES ESPECIALES:
{negocio['instrucciones_especiales']}

- Saluda siempre como: "¡Hola! 👋 Soy MIA, la asistente de {negocio['nombre']} ¿En qué te puedo ayudar?"
- Responde solo preguntas relacionadas con el negocio.
- Si el cliente pregunta algo que no sabes, dile que se comunique directamente al {negocio['telefono_contacto']}.
- Sé amable, usa emojis con moderación y responde en español.
- SOLO enviar notificación cuando tengas todos los datos del pedido completos.
"""


conversation_history = {}


def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v18.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    requests.post(url, headers=headers, json=data)


def get_ai_response(user_id, message):
    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({
        "role": "user",
        "content": message
    })

    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]

    colombia_tz = pytz.timezone("America/Bogota")
    ahora = datetime.now(colombia_tz)
    hora_actual = ahora.strftime("%H:%M %A")

    festivos_colombia = holidays.Colombia()
    es_festivo = ahora.date() in festivos_colombia
    es_fin_de_semana = ahora.weekday() >= 5

    if es_festivo:
        dia_info = f"{hora_actual} (HOY ES FESTIVO EN COLOMBIA, no hay atención)"
    elif es_fin_de_semana:
        dia_info = f"{hora_actual} (HOY ES FIN DE SEMANA, no hay atención)"
    else:
        dia_info = hora_actual

    system_with_time = construir_prompt(NEGOCIO) + \
        f"\n\nHora actual en Colombia: {dia_info}"

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_with_time}
        ] + conversation_history[user_id]
    )

    ai_message = response.choices[0].message.content

    conversation_history[user_id].append({
        "role": "assistant",
        "content": ai_message
    })

    # Detectar si hay un pedido confirmado
    keywords = ["comprobante", "envía tu comprobante",
                "pedido está registrado"]
    if any(k in ai_message.lower() for k in keywords):
        notify_hermana(user_id, conversation_history[user_id])

    return ai_message


def notify_hermana(user_phone, history):
    conversacion = ""
    for msg in history:
        rol = "Cliente" if msg["role"] == "user" else "MIA"
        conversacion += f"{rol}: {msg['content']}\n"

    try:
        extraccion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """Extrae los datos del pedido de esta conversación y responde SOLO con este formato exacto:
👤 *Cliente:* [nombre completo del cliente]
📦 *Pedido:* [descripción completa con cantidades y sabores]
💰 *Total:* [total en pesos incluyendo domicilio si aplica]
📍 *Dirección:* [dirección de entrega o 'Recoge en local']
📞 *Contacto:* [número de teléfono que el cliente proporcionó en la conversación]

Si algún dato no está disponible escribe 'No proporcionado'."""
                },
                {
                    "role": "user",
                    "content": conversacion
                }
            ]
        )
        datos = extraccion.choices[0].message.content
    except:
        datos = "Error al extraer datos"

    resumen = f"🛍️ *NUEVO PEDIDO*\n"
    resumen += f"📱 *Número:* {user_phone}\n"
    resumen += f"{datos}\n"
    resumen += f"⚠️ *Pendiente verificar pago en Nequi*"

    send_whatsapp_message(NEGOCIO['telefono_notificacion'], resumen)


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == META_VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            return jsonify({"status": "ok"}), 200

        message = value["messages"][0]
        user_phone = message["from"]

        if message["type"] != "text":
            send_whatsapp_message(
                user_phone, "Por ahora solo puedo responder mensajes de texto 😊")
            return jsonify({"status": "ok"}), 200

        user_message = message["text"]["body"]
        ai_response = get_ai_response(user_phone, user_message)
        send_whatsapp_message(user_phone, ai_response)

    except Exception as e:
        print(f"Error: {e}")

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
