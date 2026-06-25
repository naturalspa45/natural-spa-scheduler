"""
Natural Spa - Publicador Automatico desde GitHub Actions
Corre cada 30 minutos. Verifica aprobacion en Gmail,
programa posts de Facebook y publica en Instagram + Historias.
"""

import os, json, time, imaplib, email, datetime, requests

# === CONFIGURACION ===
def _clean(s): return (s or '').strip().strip('﻿').strip()
PAGE_TOKEN = _clean(os.environ.get('META_PAGE_TOKEN', ''))
PAGE_ID    = _clean(os.environ.get('META_PAGE_ID', ''))
IG_ID      = _clean(os.environ.get('META_IG_ID', ''))
GMAIL_USER = _clean(os.environ.get('GMAIL_USER', 'esteticanaturaspa@gmail.com'))
GMAIL_PWD  = _clean(os.environ.get('GMAIL_APP_PASSWORD', ''))
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
        semana_clean = semana.encode('ascii', errors='replace').decode('ascii')
        mail = imaplib.IMAP4_SSL('imap.gmail.com', 993)
        mail.login(GMAIL_USER, GMAIL_PWD)
        mail.select('inbox')
        # Buscar mensajes recientes (ultimos 7 dias) con "Natural Spa" en asunto
        since_date = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime('%d-%b-%Y')
        _, nums = mail.search(None, f'SINCE {since_date} SUBJECT "Natural Spa"')
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


def is_video(url):
    return url.lower().split('?')[0].endswith(('.mp4', '.mov', '.avi', '.webm'))


def ensure_local(local_path, raw_url):
    """Garantiza que el archivo exista localmente. Si no, lo descarga del raw URL de GitHub."""
    if os.path.exists(local_path) and os.path.getsize(local_path) > 1000:
        return local_path
    print(f"  Descargando desde GitHub: {os.path.basename(local_path)} ...")
    try:
        r = requests.get(raw_url, stream=True, timeout=120)
        if r.ok:
            os.makedirs(os.path.dirname(local_path) or '.', exist_ok=True)
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            size_kb = os.path.getsize(local_path) // 1024
            print(f"  Descargado OK: {size_kb} KB")
            return local_path
        print(f"  Download error {r.status_code}: {r.text[:100]}")
    except Exception as ex:
        print(f"  ensure_local error: {ex}")
    return None


def schedule_fb_video(video_src, message, ts_fb):
    """Programa un video en Facebook. video_src puede ser ruta local o URL."""
    try:
        body = {
            'description': message,
            'access_token': PAGE_TOKEN,
        }
        if ts_fb > now_ts() + 600:
            body['published'] = 'false'
            body['scheduled_publish_time'] = str(ts_fb)
        else:
            body['published'] = 'true'
        if video_src.startswith('http'):
            body['file_url'] = video_src
            r = requests.post(f"https://graph.facebook.com/{VERSION}/{PAGE_ID}/videos", data=body)
        else:
            with open(video_src, 'rb') as vf:
                r = requests.post(
                    f"https://graph.facebook.com/{VERSION}/{PAGE_ID}/videos",
                    data=body,
                    files={'source': (os.path.basename(video_src), vf, 'video/mp4')}
                )
        if r.ok:
            return r.json().get('id')
        print(f"  FB video error: {r.text[:200]}")
    except Exception as ex:
        print(f"  schedule_fb_video error: {ex}")
    return None


def create_ig_container(media_url, caption=None, media_type='IMAGE'):
    """Crea un contenedor de media en Instagram."""
    try:
        if media_type in ('REELS', 'STORIES') and is_video(media_url):
            data = {'video_url': media_url, 'media_type': media_type, 'access_token': PAGE_TOKEN}
        else:
            data = {'image_url': media_url, 'media_type': media_type, 'access_token': PAGE_TOKEN}
        if caption:
            data['caption'] = caption
        if media_type == 'REELS':
            data['share_to_feed'] = 'true'
        r = requests.post(f"https://graph.facebook.com/{VERSION}/{IG_ID}/media", data=data)
        if r.ok:
            return r.json().get('id')
        print(f"  IG container error: {r.text[:200]}")
    except Exception as ex:
        print(f"  create_ig_container error: {ex}")
    return None


def get_ig_peak_hours_col():
    """Consulta Meta API para detectar horas pico reales de seguidores IG.
    Retorna (hora_manana_COL, hora_tarde_COL) o (None, None) si falla."""
    try:
        r = requests.get(
            f"https://graph.facebook.com/{VERSION}/{IG_ID}/insights",
            params={
                'metric': 'online_followers',
                'period': 'lifetime',
                'access_token': PAGE_TOKEN
            },
            timeout=30
        )
        if not r.ok:
            print(f"  insights API {r.status_code}: {r.text[:80]}")
            return None, None
        data = r.json().get('data', [])
        if not data or not data[0].get('values'):
            return None, None
        # Acumular seguidores por hora UTC sumando todos los dias disponibles
        acum = {}
        for v in data[0]['values']:
            for h_str, cnt in v.get('value', {}).items():
                h = int(h_str)
                acum[h] = acum.get(h, 0) + cnt
        # Convertir UTC a COL (UTC-5)
        col = {}
        for h_utc, cnt in acum.items():
            h_col = (h_utc - 5) % 24
            col[h_col] = col.get(h_col, 0) + cnt
        # Mejor hora manana (6-11h COL) y tarde (14-21h COL)
        man = {h: v for h, v in col.items() if 6 <= h <= 11}
        tar = {h: v for h, v in col.items() if 14 <= h <= 21}
        pico_m = max(man, key=man.get) if man else None
        pico_t = max(tar, key=tar.get) if tar else None
        print(f"  Pico IG real — manana: {pico_m}h COL | tarde: {pico_t}h COL")
        return pico_m, pico_t
    except Exception as ex:
        print(f"  get_ig_peak_hours error: {ex}")
        return None, None


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

    with open('semana/parrilla.json', 'r', encoding='utf-8-sig') as f:
        entries = json.load(f)
    if not isinstance(entries, list):
        entries = [entries]

    # Cargar estado de aprobacion
    aprobacion_path = 'semana/aprobacion.json'
    aprobacion = {'aprobada': False, 'semana': ''}
    if os.path.exists(aprobacion_path):
        with open(aprobacion_path, 'r', encoding='utf-8-sig') as f:
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

    # Detectar horas pico reales de seguidores en tiempo real
    print("Consultando horas pico reales desde Meta API...")
    pico_manana, pico_tarde = get_ig_peak_hours_col()
    if pico_manana is None:
        pico_manana = 10  # fallback: Sprout Social / Buffer 9.6M posts benchmark
        print("  Fallback manana: 10h COL (Sprout Social)")
    if pico_tarde is None:
        pico_tarde = 19   # fallback: TikTok Apaya 7.1M posts benchmark
        print("  Fallback tarde: 19h COL (TikTok Apaya)")
    # Publicar 35 min antes del pico para que el algoritmo indexe primero
    pub_manana_min = pico_manana * 60 - 35
    pub_tarde_min  = pico_tarde * 60 - 35
    print(f"  Publica manana a las {pub_manana_min//60}:{pub_manana_min%60:02d} COL (pico: {pico_manana}:00)")
    print(f"  Publica tarde a las  {pub_tarde_min//60}:{pub_tarde_min%60:02d} COL (pico: {pico_tarde}:00)")

    # Tiempo actual Colombia (UTC-5)
    now_utc_dt  = datetime.datetime.utcnow()
    now_col_dt  = now_utc_dt - datetime.timedelta(hours=5)
    now_col_min = now_col_dt.hour * 60 + now_col_dt.minute
    today_col   = now_col_dt.strftime('%Y-%m-%d')

    now = now_ts()
    changed = False

    for entry in entries:
        tipo = entry.get('tipo', 'feed')
        repo_path = entry.get('imagen_repo_path', '')
        local_img = repo_path if repo_path else ''
        raw_url   = f"{RAW_BASE}/{repo_path}" if repo_path else ''

        if tipo == 'feed':
            turno = entry.get('turno')  # 'manana' o 'tarde' — nuevo sistema dinamico

            # === MODO TURNO: horas pico en tiempo real (entradas nuevas con campo turno) ===
            if turno:
                if entry.get('fecha') != today_col:
                    continue  # No es hoy
                target_min = pub_manana_min if turno == 'manana' else pub_tarde_min
                if now_col_min < target_min:
                    continue  # Aun no es la hora
                # Timestamp Meta API: pico de hoy en UTC
                pico_h = pico_manana if turno == 'manana' else pico_tarde
                pico_col_h = now_col_dt.replace(hour=pico_h, minute=0, second=0, microsecond=0)
                pico_utc_h = pico_col_h + datetime.timedelta(hours=5)
                ts_dynamic = int(pico_utc_h.timestamp())
                if ts_dynamic <= now_ts():
                    ts_dynamic = now_ts() + 120  # pico ya paso, publicar ya
                print(f"Turno {turno} {entry.get('fecha')} — pico {pico_h}:00 COL")
                # FACEBOOK (solo si este entry es FB-only)
                if not entry.get('fb_programado') and repo_path:
                    print(f"  Publicando FB turno {turno}")
                    msg = get_caption(entry, 'fb')
                    es_vid = is_video(raw_url)
                    if es_vid:
                        src = ensure_local(local_img, raw_url)
                        fb_id = schedule_fb_video(src, msg, ts_dynamic) if src else None
                    else:
                        src = ensure_local(local_img, raw_url) or raw_url
                        photo_id, cdn_url = upload_image_to_fb(src)
                        fb_id = schedule_fb_post(msg, photo_id, ts_dynamic) if photo_id else None
                        if photo_id:
                            entry['fb_url_imagen'] = cdn_url
                    if fb_id:
                        entry['fb_programado'] = True
                        entry['fb_post_id'] = fb_id
                        entry['hora_col_publicada'] = f"{pub_manana_min//60}:{pub_manana_min%60:02d}" if turno == 'manana' else f"{pub_tarde_min//60}:{pub_tarde_min%60:02d}"
                        changed = True
                        print(f"  FB OK: {fb_id}")
                # INSTAGRAM (solo si este entry es IG-only)
                if not entry.get('ig_publicado'):
                    print(f"  Publicando IG turno {turno}")
                    caption = get_caption(entry, 'ig')
                    es_vid = is_video(raw_url)
                    ig_url = entry.get('fb_url_imagen') or raw_url
                    ig_type = 'REELS' if es_vid else 'IMAGE'
                    cid = create_ig_container(ig_url, caption, ig_type)
                    if cid and wait_finished(cid):
                        post_id = publish_ig(cid)
                        if post_id:
                            entry['ig_publicado'] = True
                            entry['ig_post_id'] = post_id
                            entry['ig_container_id'] = cid
                            changed = True
                            print(f"  IG OK: {post_id}")
                continue  # No procesar bloque timestamp para esta entrada

            # === MODO TIMESTAMP: horas fijas (entradas legadas con timestamp en parrilla) ===
            formato = entry.get('formato', 'IMAGEN UNICA').upper()
            es_video = is_video(raw_url)

            # --- FACEBOOK (una sola vez) ---
            if not entry.get('fb_programado') and repo_path:
                print(f"Programando FB: {entry.get('dia')} {entry.get('hora_fb_col', '')} COL")
                msg = get_caption(entry, 'fb')
                ts_fb = int(entry.get('timestamp_fb', 0))
                if es_video:
                    src = ensure_local(local_img, raw_url)
                    fb_id = schedule_fb_video(src, msg, ts_fb) if src else None
                else:
                    src = ensure_local(local_img, raw_url) or raw_url
                    photo_id, cdn_url = upload_image_to_fb(src)
                    fb_id = schedule_fb_post(msg, photo_id, ts_fb) if photo_id else None
                    if photo_id:
                        entry['fb_url_imagen'] = cdn_url
                if fb_id:
                    entry['fb_programado'] = True
                    entry['fb_post_id']    = fb_id
                    changed = True
                    print(f"  FB programado OK: {fb_id}")

            # --- INSTAGRAM: crear contenedor 2h antes (independiente de FB) ---
            if not entry.get('ig_publicado'):
                ts_ig = int(entry.get('timestamp_ig', 0))
                if not entry.get('ig_container_id') and now >= ts_ig - 7200 and raw_url:
                    caption = get_caption(entry, 'ig')
                    ig_type = 'REELS' if es_video else 'IMAGE'
                    # Para imagenes: CDN de Facebook es mas estable que GitHub raw
                    ig_url = (entry.get('fb_url_imagen') or raw_url) if not es_video else raw_url
                    cid = create_ig_container(ig_url, caption, ig_type)
                    if cid:
                        entry['ig_container_id'] = cid
                        changed = True
                        print(f"  IG contenedor creado ({ig_type}): {cid}")

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

