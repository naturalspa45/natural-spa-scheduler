"""
Natural Spa - Publicador Automatico desde GitHub Actions
Corre cada 30 minutos. Verifica aprobacion en Gmail,
programa posts de Facebook y publica en Instagram + Historias.
"""

import os, json, time, imaplib, email, datetime, requests

# === CONFIGURACION ===
PAGE_TOKEN = os.environ.get('META_PAGE_TOKEN', '')
PAGE_ID    = os.environ.get('META_PAGE_ID', '')
IG_ID      = os.environ.get('META_IG_ID', '')
GMAIL_USER = os.environ.get('GMAIL_USER', 'esteticanaturaspa@gmail.com')
GMAIL_PWD  = os.environ.get('GMAIL_APP_PASSWORD', '')
VERSION    = "v23.0"
REPO       = "naturalspa45/natural-spa-scheduler"
RAW_BASE   = f"https://raw.githubusercontent.com/{REPO}/main"

FB_EXT = {
    "FACIAL":           "Natural Spa lleva anos perfeccionando sus protocolos faciales. Cada sesion empieza con un analisis personalizado - no usamos el mismo protocolo para todas. Tecnologia actualizada, esteticistas certificadas, resultados visibles desde la primera visita.",
    "MICROPIGMENTACION":"Natural Spa realiza micropigmentacion con pigmentos de alta duracion y tecnica manual especializada. Cada procedimiento incluye valoracion previa sin costo. Somos honestas: si no eres candidata ideal, te lo decimos antes de empezar.",
    "CORPORAL":         "Nuestros masajes corporales los aplican terapeutas formados en tecnicas combinadas: sueco, drenaje linfatico y relajacion profunda. Cada sesion se adapta a lo que tu cuerpo necesita ese dia.",
    "DEPILACION":       "En Natural Spa evaluamos tu tipo de piel antes de cualquier tratamiento de depilacion. Sistemas actualizados, protocolos de seguridad estrictos. El objetivo: que no tengas que volver a preocuparte por eso.",
    "PRODUCTOS":        "Los productos de Natural Spa se eligen con criterio clinico, no por marca ni comision. Si algo no funciona para nuestras clientas, no lo recomendamos.",
    "RELAJANTES":       "Los tratamientos relajantes de Natural Spa combinan tecnicas de distintas escuelas terapeuticas. Ambiente controlado, sin prisa, sin protocolo de fabrica.",
    "DEFAULT":          "Natural Spa - estetica profesional en Pereira con anos de trayectoria. Cada servicio lo aplican profesionales certificados con protocolos actualizados.",
}


def now_ts():
    return int(datetime.datetime.utcnow().timestamp())


def check_gmail_si(semana):
    """Busca en el inbox de Gmail si llegó un SI como respuesta al email de aprobacion."""
    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com', 993)
        mail.login(GMAIL_USER, GMAIL_PWD)
        mail.select('inbox')
        _, nums = mail.search(None, 'SUBJECT', f'semana {semana}')
        for n in nums[0].split():
            _, data = mail.fetch(n, '(RFC822)')
            msg = email.message_from_bytes(data[0][1])
            body = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/plain':
                        body += part.get_payload(decode=True).decode('utf-8', errors='ignore')
            else:
                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            # La respuesta debe ser SI (sola o acompañada) en las primeras letras
            if body.strip()[:10].upper().startswith('SI'):
                mail.logout()
                return True
        mail.logout()
    except Exception as ex:
        print(f"Gmail error: {ex}")
    return False


def upload_image_to_fb(image_url_or_path):
    """Sube imagen al CDN de Facebook. Acepta URL publica o ruta local.
    Devuelve (photo_id, cdn_url)."""
    try:
        if image_url_or_path.startswith('http'):
            # Subir desde URL publica
            r = requests.post(
                f"https://graph.facebook.com/{VERSION}/{PAGE_ID}/photos",
                params={'access_token': PAGE_TOKEN},
                data={'url': image_url_or_path, 'published': 'false'}
            )
        else:
            # Subir desde archivo local (en el runner de GitHub)
            with open(image_url_or_path, 'rb') as f:
                ext = os.path.splitext(image_url_or_path)[1].lower()
                ct = 'image/png' if ext == '.png' else 'image/jpeg'
                r = requests.post(
                    f"https://graph.facebook.com/{VERSION}/{PAGE_ID}/photos",
                    params={'access_token': PAGE_TOKEN},
                    data={'published': 'false'},
                    files={'source': (os.path.basename(image_url_or_path), f, ct)}
                )
        if not r.ok:
            print(f"  FB upload error: {r.text[:200]}")
            return None, None
        photo_id = r.json().get('id')
        time.sleep(2)
        r2 = requests.get(
            f"https://graph.facebook.com/{VERSION}/{photo_id}",
            params={'fields': 'images', 'access_token': PAGE_TOKEN}
        )
        imgs = r2.json().get('images', [])
        cdn_url = sorted(imgs, key=lambda x: x.get('height', 0), reverse=True)[0]['source'] if imgs else ''
        return photo_id, cdn_url
    except Exception as ex:
        print(f"  upload_image error: {ex}")
        return None, None


def schedule_fb_post(message, photo_id, ts_fb):
    """Crea el post en Facebook (programado o publicado ya)."""
    try:
        body = {
            'attached_media': f'[{{"media_fbid":"{photo_id}"}}]',
            'message': message,
            'access_token': PAGE_TOKEN,
        }
        if ts_fb > now_ts() + 600:
            body['published'] = 'false'
            body['scheduled_publish_time'] = str(ts_fb)
        else:
            body['published'] = 'true'
        r = requests.post(f"https://graph.facebook.com/{VERSION}/{PAGE_ID}/feed", data=body)
        if r.ok:
            return r.json().get('id')
        print(f"  FB post error: {r.text[:200]}")
    except Exception as ex:
        print(f"  schedule_fb_post error: {ex}")
    return None


def create_ig_container(image_url, caption=None, media_type='IMAGE'):
    """Crea un contenedor de media en Instagram."""
    try:
        data = {'image_url': image_url, 'media_type': media_type, 'access_token': PAGE_TOKEN}
        if caption:
            data['caption'] = caption
        r = requests.post(f"https://graph.facebook.com/{VERSION}/{IG_ID}/media", data=data)
        if r.ok:
            return r.json().get('id')
        print(f"  IG container error: {r.text[:200]}")
    except Exception as ex:
        print(f"  create_ig_container error: {ex}")
    return None


def wait_finished(container_id, tries=12):
    """Espera hasta que el contenedor de IG termine de procesar."""
    for _ in range(tries):
        r = requests.get(
            f"https://graph.facebook.com/{VERSION}/{container_id}",
            params={'fields': 'status_code', 'access_token': PAGE_TOKEN}
        )
        if r.json().get('status_code') == 'FINISHED':
            return True
        time.sleep(5)
    return False


def publish_ig(container_id):
    """Publica un contenedor FINISHED de Instagram."""
    try:
        r = requests.post(
            f"https://graph.facebook.com/{VERSION}/{IG_ID}/media_publish",
            data={'creation_id': container_id, 'access_token': PAGE_TOKEN}
        )
        if r.ok:
            return r.json().get('id')
        print(f"  IG publish error: {r.text[:200]}")
    except Exception as ex:
        print(f"  publish_ig error: {ex}")
    return None


def get_caption(entry, plataforma='ig'):
    """Construye el caption con hook + cuerpo + cta + hashtags/parrafo FB."""
    parts = []
    if entry.get('hook'):   parts.append(entry['hook'])
    if entry.get('cuerpo'): parts.append(entry['cuerpo'])
    if entry.get('cta'):    parts.append(entry['cta'])
    if plataforma == 'ig' and entry.get('hashtags'):
        parts.append(entry['hashtags'])
    elif plataforma == 'fb':
        svc = entry.get('servicio', 'DEFAULT')
        parts.append(FB_EXT.get(svc, FB_EXT['DEFAULT']))
    return '\n\n'.join(parts)


def main():
    print(f"=== Natural Spa Publisher {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} ===")

    if not os.path.exists('semana/parrilla.json'):
        print("Sin parrilla esta semana. Nada que hacer.")
        return

    with open('semana/parrilla.json', 'r', encoding='utf-8') as f:
        entries = json.load(f)
    if not isinstance(entries, list):
        entries = [entries]

    # Cargar estado de aprobacion
    aprobacion_path = 'semana/aprobacion.json'
    aprobacion = {'aprobada': False, 'semana': ''}
    if os.path.exists(aprobacion_path):
        with open(aprobacion_path, 'r') as f:
            aprobacion = json.load(f)

    semana = aprobacion.get('semana', '')

    if not aprobacion.get('aprobada'):
        print(f"Verificando SI en Gmail para semana {semana}...")
        if check_gmail_si(semana):
            aprobacion['aprobada'] = True
            aprobacion['aprobada_en'] = datetime.datetime.utcnow().isoformat()
            with open(aprobacion_path, 'w') as f:
                json.dump(aprobacion, f)
            print("SI detectado - parrilla APROBADA.")
        else:
            print("Sin SI en Gmail todavia. Nada que publicar.")
            return

    now = now_ts()
    changed = False

    for entry in entries:
        tipo = entry.get('tipo', 'feed')
        repo_path = entry.get('imagen_repo_path', '')
        local_img = f"semana/imagenes/{os.path.basename(repo_path)}" if repo_path else ''
        raw_url   = f"{RAW_BASE}/semana/imagenes/{os.path.basename(repo_path)}" if repo_path else ''

        if tipo == 'feed':
            # --- FACEBOOK (una sola vez) ---
            if not entry.get('fb_programado') and repo_path:
                print(f"Programando FB: {entry.get('dia')} {entry.get('hora_fb_col', '')} COL")
                src = local_img if os.path.exists(local_img) else raw_url
                photo_id, cdn_url = upload_image_to_fb(src)
                if photo_id:
                    msg = get_caption(entry, 'fb')
                    fb_id = schedule_fb_post(msg, photo_id, int(entry.get('timestamp_fb', 0)))
                    if fb_id:
                        entry['fb_programado'] = True
                        entry['fb_post_id']    = fb_id
                        entry['fb_url_imagen'] = cdn_url
                        changed = True
                        print(f"  FB programado OK: {fb_id}")

            # --- INSTAGRAM: crear contenedor 2h antes ---
            if not entry.get('ig_publicado') and entry.get('fb_programado'):
                ts_ig = int(entry.get('timestamp_ig', 0))
                if not entry.get('ig_container_id') and now >= ts_ig - 7200 and raw_url:
                    caption = get_caption(entry, 'ig')
                    cid = create_ig_container(raw_url, caption)
                    if cid:
                        entry['ig_container_id'] = cid
                        changed = True
                        print(f"  IG contenedor creado: {cid}")

                # Publicar cuando llegue la hora
                if entry.get('ig_container_id') and now >= ts_ig:
                    if wait_finished(entry['ig_container_id']):
                        post_id = publish_ig(entry['ig_container_id'])
                        if post_id:
                            entry['ig_publicado'] = True
                            entry['ig_post_id']   = post_id
                            changed = True
                            print(f"  IG publicado OK: {post_id}")
                    else:
                        print(f"  IG contenedor no termino de procesar")

        elif tipo == 'historia':
            # --- HISTORIA IG (a las 7am COL = 12:00 UTC) ---
            if not entry.get('ig_story_publicado'):
                ts_st = int(entry.get('timestamp_fb', entry.get('timestamp_ig', 0)))
                if now >= ts_st and raw_url:
                    print(f"Publicando historia: {entry.get('dia')}")
                    cid = create_ig_container(raw_url, media_type='STORIES')
                    if cid and wait_finished(cid):
                        post_id = publish_ig(cid)
                        if post_id:
                            entry['ig_story_publicado'] = True
                            changed = True
                            print(f"  Historia publicada OK: {post_id}")

    if changed:
        with open('semana/parrilla.json', 'w', encoding='utf-8') as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        print("Parrilla actualizada.")
    else:
        print("Sin cambios esta corrida.")


if __name__ == '__main__':
    main()
