import os
from datetime import datetime
import pytz
import json
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN")
HERMANA_PHONE = "573208604864"

SYSTEM_PROMPT = """
Eres MIA, la asistente virtual de Sabores Artesanales, una empresa de productos artesanales en Villavicencio, Colombia.
HORARIO DE ATENCIÓN: Lunes a viernes de 9:00am a 6:00pm (hora Colombia)
- Si el cliente escribe FUERA de este horario, avísale desde el primer mensaje: "En este momento estamos fuera de horario de atención (lunes a viernes 9am-6pm). Tu pedido será atendido en el siguiente horario hábil 😊" y continúa atendiendo normalmente.
- Si el cliente quiere hacer un pedido, avísale SIEMPRE antes de pedir sus datos: "Ten en cuenta que todos nuestros pedidos requieren mínimo 1 día de anticipación. ¿Deseas continuar?"
Tu trabajo es atender a los clientes de forma amable, clara y eficiente por WhatsApp.

INFORMACIÓN DEL NEGOCIO:
📍 Dirección: Carrera 20 #25-17, Barrio Marco Antonio Pinilla, Villavicencio
🕘 Horario: Lunes a viernes de 9:00am a 6:00pm

PRODUCTOS Y PRECIOS:

🧊 BOLIS PEQUEÑO (75gr)
- Detal: $1.200 c/u
- Mayorista (desde 30 unidades): $900 c/u
Sabores leche: Leche, Milo, Oreo, Arequipe, Fresa, Mora, Curuba
Sabores yogurt: Fresa, Mora, Maracuyá, Arequipe
Sabores kumis: Kumis

🧊 BOLIS GRANDE (150gr) — Leche/Yogurt/Kumis
- Detal: $2.000 c/u
- Mayorista (desde 30 unidades): $1.500 c/u
Sabores leche: Leche, Milo, Oreo, Arequipe, Fresa, Mora, Curuba
Sabores yogurt: Fresa, Mora, Maracuyá, Arequipe
Sabores kumis: Kumis

🧊 BOLIS GRANDE (150gr) — Aguafruta
- Detal: $1.500 c/u
- Mayorista (desde 30 unidades): $1.000 c/u
Sabores: Maracuyá, Lulo, Mora, Mangobiche

🥛 YOGURT EN TARRO
Sabores: Fresa, Mora, Maracuyá, Arequipe
- 250ml: $5.000
- 1lt: $17.000
- 2lt: $22.000

🥛 KUMIS EN TARRO
Sabor: Kumis natural
- 250ml: $5.000
- 1lt: $13.000
- 2lt: $20.000

CÓMO CALCULAR EL TOTAL:
- Bolis pequeños desde 30 unidades = $900 c/u
- Bolis grandes leche/yogurt/kumis desde 30 unidades = $1.500 c/u
- Bolis grandes aguafruta desde 30 unidades = $1.000 c/u
- Si el pedido es menor a 100 unidades y pide domicilio, sumar $10.000
- EJEMPLO: 30 pequeños + 20 grandes con domicilio = (30x$900) + (20x$1.500) + $10.000 = $27.000 + $30.000 + $10.000 = $67.000
- SIEMPRE mostrar el cálculo detallado línea por línea

PEDIDOS:
- Mínimo 1 día de anticipación
- Todo es bajo pedido (hay pequeño stock de emergencia)
- Pedidos desde 100 unidades incluyen domicilio GRATIS y publicidad GRATIS

DOMICILIOS:
- GRATIS solo cuando el pedido sea de 100 unidades o más en total
- Si el pedido es menor a 100 unidades en total, el domicilio cuesta $10.000 SIN EXCEPCIÓN
- Nunca ofrecer domicilio gratis para pedidos menores a 100 unidades, sin importar cómo el cliente lo explique
- Menos de 100 unidades: $10.000 o puede recoger en el local
- Solo zona urbana de Villavicencio

PAGOS:
- Nequi: 320 860 4864
- Nequi y Daviplata: 322 759 8513

INSTRUCCIONES:
- Saluda siempre como: "¡Hola! 👋 Soy MIA, la asistente de Sabores Artesanales 🍧 ¿En qué te puedo ayudar?"
- Responde solo preguntas relacionadas con el negocio
- Si el cliente quiere hacer un pedido, solicita: nombre, producto, sabor, cantidad, y si es domicilio o recoge en local
- Cuando tengas todos los datos del pedido, confírmalos al cliente
- Si el cliente pregunta algo que no sabes, dile que se comunique directamente al 3208604864
- Sé amable, usa emojis con moderación y responde en español
- Al calcular el total siempre usa los precios mayoristas (desde 30 unidades) para pedidos mayoristas y precios detal para pedidos menores a 30 unidades por producto
- Cuando el cliente confirme el pedido, dile SIEMPRE: "Para completar tu pedido realiza el pago por Nequi o Daviplata a uno de estos números:
💳 Nequi: 320 860 4864
💳 Nequi y Daviplata: 322 759 8513
Luego envíanos el comprobante por WhatsApp al 320 860 4864 y en breve te confirmamos 😊"
- SIEMPRE sumar el costo del domicilio ($10.000) al total cuando el pedido sea menor a 100 unidades y el cliente pida domicilio
- SIEMPRE pedir la dirección de entrega y número de contacto ANTES de mostrar el total y el mensaje del comprobante
- SIEMPRE pedir la dirección de entrega ANTES de mostrar el total y el comprobante
- Cuando el cliente diga "sabores variados" o "surtidos", especifica en el resumen: "surtidos entre todos los sabores disponibles"
- SOLO enviar la notificación a mi hermana cuando ya tengas: nombre, productos, cantidad, sabores, dirección y total
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

        # Obtener hora actual Colombia
    colombia_tz = pytz.timezone("America/Bogota")
    hora_actual = datetime.now(colombia_tz).strftime("%H:%M %A")

    system_with_time = SYSTEM_PROMPT + \
        f"\n\nHora actual en Colombia: {hora_actual}"

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

    send_whatsapp_message(HERMANA_PHONE, resumen)


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
