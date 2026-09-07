# CC4303 Redes — Actividades

Repositorio de las actividades del curso CC4303 (Redes), primavera 2026.

Los códigos se ejecutan en una máquina virtual (WSL2 / Ubuntu 26.04). Ambos hacen
`bind` a `0.0.0.0` en vez de a una IP fija, así que quedan alcanzables tanto en
`127.0.0.1` como en la IP de la máquina virtual (`IP_VM`), que se obtiene con:

```bash
ip -4 addr show eth0
```

---

## Actividad 1 — Proxy HTTP con filtrado de contenido

Carpeta: [`tarea1-proxy/`](tarea1-proxy)

| Archivo | Descripción |
|---|---|
| `proxy.py` | Proxy HTTP. Solo usa `socket`, `json` y `sys`. |
| `config.json` | Usuario, dominios bloqueados y palabras prohibidas. |
| `gato.png` | Imagen local que se muestra en la página de bloqueo 403. |

### Ejecución

```bash
cd tarea1-proxy
python3 proxy.py config.json
```

Argumentos opcionales: `python3 proxy.py config.json [puerto] [buffer]`
(por defecto puerto `8000` y buffer `50` bytes).

### Pruebas

```bash
curl example.com -x IP_VM:8000
```

```bash
curl cc4303.bachmann.cl -x IP_VM:8000
```

```bash
curl -i cc4303.bachmann.cl/secret -x IP_VM:8000
```

```bash
curl cc4303.bachmann.cl/replace -x IP_VM:8000
```

Para probar con navegador, configurar `IP_VM:8000` como proxy HTTP del sistema
o del navegador y visitar `http://cc4303.bachmann.cl/`, `/replace` y `/secret`.

---

## Actividad 2 — Resolver DNS iterativo

Carpeta: [`tarea2-resolver/`](tarea2-resolver)

| Archivo | Descripción |
|---|---|
| `resolver.py` | Resolver DNS iterativo con caché. Usa librerías nativas y `dnslib`. |

Dependencia: `dnslib`. En Ubuntu 26.04 se instala con apt, porque PEP 668
bloquea `pip install` en el Python del sistema:

```bash
sudo apt install python3-dnslib
```

### Ejecución

```bash
cd tarea2-resolver
python3 resolver.py
```

Argumentos opcionales: `python3 resolver.py [puerto] [--debug|--no-debug]`
(por defecto puerto `8000` y modo debug activo).

### Pruebas

```bash
dig -p8000 @IP_VM www.uchile.cl
```

```bash
dig -p8000 @IP_VM eol.uchile.cl
```

```bash
dig -p8000 @IP_VM cc4303.bachmann.cl
```

Para ver el caché en acción, repetir la misma consulta dos veces y observar la
salida del modo debug.

---

## Herramientas útiles

Liberar un puerto que quedó tomado:

```bash
sudo lsof -i:8000
```

Espiar el tráfico del resolver:

```bash
sudo tcpdump -v -i lo udp port 8000
```
